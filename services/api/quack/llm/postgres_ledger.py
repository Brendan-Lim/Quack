"""Durable, conservative model quotas for serverless production.

Each operation reserves its maximum retry allowance before it can contact a model.
Only complete, successful usage receipts can settle it to actual estimated cost.
Crashes and cancellations retain the reserve. These quotas are not provider billing caps.
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import asdict
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb

from quack.db.database import Database
from quack.llm.ledger import BudgetExceeded, Ledger, UsageRecord, _nonnegative, _validated

_active_debit: ContextVar[str | None] = ContextVar("quack_model_debit", default=None)
_LOCK_ID = 743_812_095


class PostgresLedger(Ledger):
    """Atomic quotas across processes; restart-safe even when a provider reply is lost."""

    def __init__(
        self, url: str, project_limit: float, session_limit: float,
        request_limit: float, hourly_limit: float | None = None,
    ) -> None:
        if not url:
            raise ValueError("PostgreSQL accounting requires QUACK_DATABASE_URL")
        for limit in (project_limit, session_limit, request_limit, hourly_limit):
            if limit is not None:
                _nonnegative(limit)
        self.path = None
        self.project_limit = project_limit
        self.session_limit = session_limit
        self.request_limit = request_limit
        self.hourly_limit = hourly_limit
        self.database = Database(url=url)
        self._healthy = True
        self.database.execute("""
            CREATE TABLE IF NOT EXISTS quack_model_debits (
                id TEXT PRIMARY KEY, account_scope TEXT NOT NULL,
                charged_usd NUMERIC NOT NULL CHECK (charged_usd >= 0),
                created_at DOUBLE PRECISION NOT NULL
            )
        """)
        self.database.execute("""
            CREATE TABLE IF NOT EXISTS quack_model_usage (
                id TEXT PRIMARY KEY, debit_id TEXT NOT NULL REFERENCES quack_model_debits(id),
                estimated_usd NUMERIC NOT NULL CHECK (estimated_usd >= 0),
                record JSONB NOT NULL
            )
        """)
        for table in ("quack_model_debits", "quack_model_usage"):
            self.database.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

    @staticmethod
    def _scope(session_id: str) -> str:
        # Authenticated sessions are user-id:client-session-id. Rotating the client
        # session must not reset an account's allowance. Account-free local tests
        # retain their ordinary session scope.
        return session_id.partition(":")[0]

    @property
    def project_spend(self) -> float:
        row = self.database.one("SELECT COALESCE(SUM(charged_usd), 0) AS total FROM quack_model_debits")
        assert row is not None
        return float(row["total"])

    def session_spend(self, session_id: str) -> float:
        row = self.database.one(
            "SELECT COALESCE(SUM(charged_usd), 0) AS total FROM quack_model_debits WHERE account_scope = ?",
            (self._scope(session_id),),
        )
        assert row is not None
        return float(row["total"])

    def _check_locked(self, session_id: str, amount: float) -> None:
        if not self._healthy or not math.isfinite(amount) or amount < 0:
            raise BudgetExceeded("ledger", 0, self.project_limit)
        row = self.database.one("""
            SELECT COALESCE(SUM(charged_usd), 0) AS project,
                   COALESCE(SUM(CASE WHEN account_scope = ? THEN charged_usd ELSE 0 END), 0) AS account,
                   COALESCE(SUM(CASE WHEN created_at >= ? THEN charged_usd ELSE 0 END), 0) AS hourly
            FROM quack_model_debits
        """, (self._scope(session_id), time.time() - 3600))
        assert row is not None
        for scope, key, limit in (
            ("request", "", self.request_limit),
            ("project", "project", self.project_limit),
            ("account", "account", self.session_limit if session_id else None),
            ("hourly", "hourly", self.hourly_limit),
        ):
            spent = Decimal(0) if not key else row[key]
            if limit is not None and spent + Decimal(str(amount)) > Decimal(str(limit)):
                raise BudgetExceeded(scope, float(spent), limit)

    def _allocate(self, session_id: str, amount: float, *, debit: bool) -> str:
        debit_id = str(uuid.uuid4())
        try:
            with self.database.transaction():
                # The database lock coordinates independent connections and processes.
                # A Python lock or a single replica alone cannot do this after a restart.
                self.database.execute("SELECT pg_advisory_xact_lock(?)", (_LOCK_ID,))
                self._check_locked(session_id, amount)
                if debit:
                    self.database.execute(
                        "INSERT INTO quack_model_debits (id, account_scope, charged_usd, created_at) VALUES (?, ?, ?, ?)",
                        (debit_id, self._scope(session_id), Decimal(str(amount)), time.time()),
                    )
        except psycopg.Error as exc:
            self._healthy = False
            raise BudgetExceeded("ledger", 0, self.project_limit) from exc
        return debit_id

    def check(self, session_id: str, worst_case_usd: float) -> None:
        self._allocate(session_id, worst_case_usd, debit=False)

    def reserve_operation(self, session_id: str, worst_case_usd: float) -> AbstractContextManager[None]:
        """Keep the production operation's complete retry allowance durably prepaid."""
        return self.reserve(session_id, worst_case_usd)

    def reserve_attempt(self, session_id: str, worst_case_usd: float) -> AbstractContextManager[None]:
        # Each attempt is already covered by the surrounding durable operation debit.
        if _active_debit.get() is None or not self._healthy:
            raise BudgetExceeded("ledger", 0, self.project_limit)
        _nonnegative(worst_case_usd)
        return nullcontext()

    @contextmanager
    def reserve(self, session_id: str, worst_case_usd: float) -> Iterator[None]:
        from quack.llm.router import MAX_ATTEMPTS

        _nonnegative(worst_case_usd)

        # The existing router can retry an operation. Prepay its complete retry
        # allowance. Voice has no retry but uses the same conservative bound.
        # Never expire an unresolved debit.
        debit_id = self._allocate(session_id, worst_case_usd * MAX_ATTEMPTS, debit=True)
        token = _active_debit.set(debit_id)
        try:
            yield
            self._settle_known(debit_id)
        finally:
            _active_debit.reset(token)

    def _settle_known(self, debit_id: str) -> None:
        try:
            with self.database.transaction():
                self.database.execute("SELECT pg_advisory_xact_lock(?)", (_LOCK_ID,))
                rows = self.database.query(
                    "SELECT estimated_usd, record FROM quack_model_usage WHERE debit_id = ?", (debit_id,),
                )
                for row in rows:
                    receipt = row["record"]
                    has_usage = receipt["input_tokens"] + receipt["output_tokens"] > 0
                    if receipt["http_status"] != 200 or receipt["error_category"] or not (
                        has_usage or receipt["purpose"] == "voice_transcribe"
                    ):
                        return
                if rows:
                    actual = sum((row["estimated_usd"] for row in rows), Decimal(0))
                    self.database.execute("UPDATE quack_model_debits SET charged_usd = ? WHERE id = ?", (actual, debit_id))
        except psycopg.Error as exc:
            self._healthy = False
            raise BudgetExceeded("ledger", 0, self.project_limit) from exc

    def record(self, record: UsageRecord) -> None:
        debit_id = _active_debit.get()
        try:
            _validated(asdict(record))
        except ValueError as exc:
            self._healthy = False
            raise BudgetExceeded("ledger", 0, self.project_limit) from exc
        if debit_id is None or not math.isfinite(record.estimated_usd) or record.estimated_usd < 0:
            raise BudgetExceeded("ledger", 0, self.project_limit)
        try:
            with self.database.transaction():
                self.database.execute("SELECT pg_advisory_xact_lock(?)", (_LOCK_ID,))
                self.database.execute(
                    "INSERT INTO quack_model_usage (id, debit_id, estimated_usd, record) VALUES (?, ?, ?, ?)",
                    (str(uuid.uuid4()), debit_id, Decimal(str(record.estimated_usd)), Jsonb(asdict(record))),
                )
                # If actual usage exceeds an estimate, keep the larger amount.
                # Subsequent calls stop; a quota cannot undo an already billed call.
                self.database.execute("""
                    UPDATE quack_model_debits SET charged_usd = GREATEST(charged_usd,
                        (SELECT SUM(estimated_usd) FROM quack_model_usage WHERE debit_id = ?))
                    WHERE id = ?
                """, (debit_id, debit_id))
        except psycopg.Error as exc:
            self._healthy = False
            raise BudgetExceeded("ledger", 0, self.project_limit) from exc
