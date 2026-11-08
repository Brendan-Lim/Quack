"""Append-only record of every model request, and the budgets enforced against it."""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("quack.ledger")


def _nonnegative(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        raise ValueError("ledger amounts must be finite, nonnegative numbers")
    return float(value)


def _validated(record: object) -> dict:
    if not isinstance(record, dict):
        raise ValueError("ledger record must be an object")
    _nonnegative(record.get("estimated_usd", 0.0))
    _nonnegative(record.get("timestamp", time.time()))
    if not isinstance(record.get("session_id", ""), str):
        raise ValueError("ledger session must be text")
    return record


class BudgetExceeded(Exception):
    def __init__(self, scope: str, spent: float, limit: float) -> None:
        super().__init__(f"{scope} budget reached: ${spent:.4f} of ${limit:.2f}")
        self.scope = scope
        self.spent = spent
        self.limit = limit


@dataclass
class UsageRecord:
    model: str
    reasoning_effort: str
    purpose: str
    run_id: str
    session_id: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    estimated_usd: float = 0.0
    elapsed_ms: float = 0.0
    http_status: int | None = None
    error_category: str | None = None
    attempt: int = 1
    timestamp: float = field(default_factory=time.time)


class Ledger:
    """Totals are rebuilt from the file at start-up, so restarts cannot reset a budget."""

    def __init__(
        self,
        path: Path | None,
        project_limit: float,
        session_limit: float,
        request_limit: float,
        hourly_limit: float | None = None,
    ) -> None:
        self.path = path
        for limit in (project_limit, session_limit, request_limit, hourly_limit):
            if limit is not None:
                _nonnegative(limit)
        self.project_limit = project_limit
        self.session_limit = session_limit
        self.request_limit = request_limit
        self.hourly_limit = hourly_limit
        self._lock = threading.Lock()
        self._project_spend = 0.0
        self._session_spend: dict[str, float] = defaultdict(float)
        self._recent: deque[tuple[float, float]] = deque()
        # Worst-case cost of requests in flight, so concurrent calls cannot all pass a check.
        self._reserved_project = 0.0
        self._reserved_session: dict[str, float] = defaultdict(float)
        self._read_up_to = 0
        self._write_failed = False
        if path is not None and path.exists():
            self._repair_tail(path)
            self._catch_up()

    @property
    def project_spend(self) -> float:
        return self._project_spend

    @staticmethod
    def _repair_tail(path: Path) -> None:
        """A crash mid-write can leave a partial last line. The next record must not join onto it."""

        data = path.read_bytes()
        complete = data.rfind(b"\n") + 1
        if complete != len(data):
            with path.open("r+b") as handle:
                handle.truncate(complete)

    def _catch_up(self) -> None:
        """Count new lines, whichever process wrote them: evals share these budgets with the server.

        A bad line in the middle means real spend would go uncounted, so it stops here instead.
        """

        if self.path is None or not self.path.exists():
            return
        size = self.path.stat().st_size
        if size < self._read_up_to:
            # The file was replaced, so nothing counted so far can be trusted to be in it.
            self._project_spend, self._read_up_to = 0.0, 0
            self._session_spend.clear()
            self._recent.clear()
        if size == self._read_up_to:
            return
        with self.path.open("rb") as handle:
            handle.seek(self._read_up_to)
            data = handle.read()
        complete = data.rfind(b"\n") + 1
        # All or nothing, or a retry after a bad line would count the good ones twice.
        records = [_validated(json.loads(line)) for line in data[:complete].splitlines() if line.strip()]
        for record in records:
            self._add(record)
        self._read_up_to += complete

    def _add(self, record: dict) -> None:
        cost = float(record.get("estimated_usd", 0.0))
        self._project_spend += cost
        if record.get("session_id"):
            self._session_spend[record["session_id"]] += cost
        if cost:
            self._recent.append((float(record.get("timestamp", time.time())), cost))

    def _hourly_spend(self) -> float:
        cutoff = time.time() - 3600
        # Other processes and wall-clock changes can append records out of timestamp order.
        self._recent = deque((stamp, cost) for stamp, cost in self._recent if stamp >= cutoff)
        return sum(cost for _, cost in self._recent)

    def session_spend(self, session_id: str) -> float:
        with self._lock:
            return self._session_spend.get(session_id, 0.0)

    def _check_locked(self, session_id: str, worst_case_usd: float) -> None:
        _nonnegative(worst_case_usd)
        if self._write_failed:
            raise BudgetExceeded("ledger", self.project_spend, self.project_limit)
        try:
            self._catch_up()
        except (ValueError, OSError) as exc:
            # We can't tell what has been spent, so nothing more gets spent.
            raise BudgetExceeded("ledger", self._project_spend, self.project_limit) from exc
        if worst_case_usd > self.request_limit:
            raise BudgetExceeded("request", worst_case_usd, self.request_limit)
        committed = self._project_spend + self._reserved_project
        if committed + worst_case_usd > self.project_limit:
            raise BudgetExceeded("project", committed, self.project_limit)
        if session_id:
            spent = self._session_spend.get(session_id, 0.0) + self._reserved_session.get(session_id, 0.0)
            if spent + worst_case_usd > self.session_limit:
                raise BudgetExceeded("session", spent, self.session_limit)
        if self.hourly_limit is not None:
            hourly = self._hourly_spend() + self._reserved_project
            if hourly + worst_case_usd > self.hourly_limit:
                raise BudgetExceeded("hourly", hourly, self.hourly_limit)

    def check(self, session_id: str, worst_case_usd: float) -> None:
        """Refuse a request that could cross a limit, before any money is spent."""

        with self._lock:
            self._check_locked(session_id, worst_case_usd)

    def reserve_operation(self, session_id: str, worst_case_usd: float) -> AbstractContextManager[None]:
        """File accounting reserves each attempt instead of prepaying retries."""
        return nullcontext()

    def reserve_attempt(self, session_id: str, worst_case_usd: float) -> AbstractContextManager[None]:
        return self.reserve(session_id, worst_case_usd)

    @contextmanager
    def reserve(self, session_id: str, worst_case_usd: float) -> Iterator[None]:
        """Hold a request's worst-case cost against the limits until it has finished."""

        with self._lock:
            self._check_locked(session_id, worst_case_usd)
            self._reserved_project += worst_case_usd
            if session_id:
                self._reserved_session[session_id] += worst_case_usd
        try:
            yield
        finally:
            with self._lock:
                self._reserved_project -= worst_case_usd
                if session_id:
                    self._reserved_session[session_id] -= worst_case_usd
                    if self._reserved_session[session_id] <= 1e-12:
                        del self._reserved_session[session_id]

    def record(self, record: UsageRecord) -> None:
        payload = _validated(asdict(record))
        with self._lock:
            if self.path is None:
                self._add(payload)
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # First as well, so that a file replaced since the last look is noticed.
                self._catch_up()
                with self.path.open("a") as handle:
                    handle.write(json.dumps(payload) + "\n")
                self._catch_up()
            except (ValueError, OSError):
                # The call has already happened, so count it, and the next check will refuse.
                self._add(payload)
                self._write_failed = True
                log.error("ledger_write_failed")


def read_records(path: Path) -> list[UsageRecord]:
    if not path.exists():
        return []
    # A line with no newline is one still being written, or cut off by a crash.
    with path.open() as handle:
        return [UsageRecord(**json.loads(line)) for line in handle if line.endswith("\n") and line.strip()]
