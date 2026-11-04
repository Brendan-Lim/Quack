"""One small database interface over SQLite and Postgres, using SQL both accept.

SQLite needs no setup, for development and tests. Postgres, such as Supabase's, is for when accounts
have to follow a student between devices. One connection behind a lock is enough for one process,
and these statements take milliseconds. Callers on the event loop go through `asyncio.to_thread`.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_SCHEMA = Path(__file__).with_name("schema.sql")
TABLES = (
    "profiles",
    "calibration_samples",
    "study_sessions",
    "mistake_facts",
    "learning_events",
    "practice_problems",
    "problem_mistakes",
    "level_progress",
)
# Columns added since the first release. A database from before then gets them here, and keeps its rows.
_ADDED_COLUMNS = (
    ("mistake_facts", "written_step", "TEXT"),
    ("mistake_facts", "corrected_step", "TEXT"),
    ("learning_events", "help_text", "TEXT"),
    # Lessons. A lesson question is a practice problem with these filled in. See services/lessons.py.
    ("practice_problems", "course_id", "TEXT"),
    ("practice_problems", "level_id", "TEXT"),
    ("practice_problems", "skill_id", "TEXT"),
    ("practice_problems", "template_id", "TEXT"),
    ("practice_problems", "instruction", "TEXT"),
    ("practice_problems", "probes_json", "TEXT"),
    ("practice_problems", "selection_kind", "TEXT"),
    ("practice_problems", "selection_reason", "TEXT"),
    ("practice_problems", "selection_target", "TEXT"),
    ("practice_problems", "selection_json", "TEXT"),
    ("practice_problems", "assisted", "INTEGER NOT NULL DEFAULT 0"),
    # What the question gives before it asks: `If x = 3, ...`. See lessons.question_context.
    ("practice_problems", "context_json", "TEXT"),
)
_LOCAL_ACCOUNTS = """
CREATE TABLE IF NOT EXISTS local_accounts (
    email TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
)
"""


def _statements(script: str) -> list[str]:
    without_comments = "\n".join(line for line in script.splitlines() if not line.strip().startswith("--"))
    return [statement.strip() for statement in without_comments.split(";") if statement.strip()]


class Database:
    def __init__(self, *, url: str = "", path: Path | None = None) -> None:
        self._url = url
        self._path = path
        self._lock = threading.RLock()
        self._connection: Any = None
        self._depth = 0
        self.is_postgres = bool(url)
        self._connect()
        for statement in _statements(_SCHEMA.read_text()):
            self.execute(statement)
        for table, column, kind in _ADDED_COLUMNS:
            self._add_column(table, column, kind)
        if self.is_postgres:
            # With no policies this refuses everyone but the owner, which is this server. Nothing in
            # these tables can be reached with the public key through Supabase's own API.
            for table in TABLES:
                self.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

    def _add_column(self, table: str, column: str, kind: str) -> None:
        if self.is_postgres:
            self.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {kind}")
        elif column not in {row["name"] for row in self.query(f"PRAGMA table_info({table})")}:
            self.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    def enable_local_accounts(self) -> None:
        self.execute(_LOCAL_ACCOUNTS)

    def _connect(self) -> None:
        if self.is_postgres:
            import psycopg
            from psycopg.rows import dict_row

            self._connection = psycopg.connect(self._url, autocommit=True, row_factory=dict_row)
            return
        assert self._path is not None
        if str(self._path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")

    def _run(self, sql: str, params: Sequence[Any]) -> Any:
        if not self.is_postgres:
            return self._connection.execute(sql, params)
        import psycopg

        converted = sql.replace("?", "%s")
        try:
            return self._connection.execute(converted, params)
        except psycopg.OperationalError:
            # The connection dropped, such as after the database restarted. One more try on a new one,
            # unless a transaction was under way, which has to be started again by its caller.
            if self._connection.info.transaction_status != psycopg.pq.TransactionStatus.UNKNOWN:
                raise
            self._connect()
            return self._connection.execute(converted, params)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a statement and say how many rows it changed."""

        with self._lock:
            return self._run(sql, params).rowcount

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._run(sql, params).fetchall()]

    def one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Everything inside either happens or doesn't, and nothing else runs in between."""

        with self._lock:
            if self.is_postgres:
                with self._connection.transaction():
                    yield
                return
            # One transaction can use a method that has its own. The outermost one decides.
            if self._depth:
                yield
                return
            self._connection.execute("BEGIN")
            self._depth = 1
            try:
                yield
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")
            finally:
                self._depth = 0

    def close(self) -> None:
        with self._lock:
            self._connection.close()
