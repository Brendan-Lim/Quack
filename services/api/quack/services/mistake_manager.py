"""Per-session memory of which mistakes the student has already been told about.

A new server response is not a new mistake. This is the single place that decides
whether a confirmed error deserves a quack.

The decision is based on the mistakes the client says it has shown, not on what the server
last sent. The client discards replies that arrive after the student has moved on, so the
server cannot assume that a reply it sent was ever seen. Recording an alert at send time
loses the quack altogether in exactly that case.

Session state lives in memory, which is enough for one process during the hackathon. A
restart loses hint history and makes old mistakes unknown until they are detected again.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field

from quack.services.math_verifier import TermFact

MAX_SESSIONS = 1000
SESSION_TTL_S = 6 * 3600
MAX_RESOLVED_HISTORY = 50


@dataclass
class TrackedMistake:
    id: str
    category: str
    error_type: str
    operation_type: str
    previous_step: str
    current_step: str
    expected: str
    evidence: str
    # What the line itself does, from its parsed operator. See StepResult.
    parsed_operation: str = ""
    term_facts: tuple[TermFact, ...] = ()
    extra_terms: tuple[str, ...] = ()
    hints_given: list[str] = field(default_factory=list)
    # What was said at each level, so asking again repeats it rather than generating another.
    hints_by_level: dict[int, tuple[str, str]] = field(default_factory=dict)


@dataclass
class ResolvedRecord:
    mistake_id: str
    category: str
    resolved_at: float


@dataclass
class SessionState:
    latest_version: int = -1
    known: dict[str, TrackedMistake] = field(default_factory=dict)
    resolved: list[ResolvedRecord] = field(default_factory=list)
    alerts: int = 0
    touched_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Decision:
    should_quack: bool = False
    stale: bool = False
    resolved_ids: tuple[str, ...] = ()


class MistakeManager:
    def __init__(self) -> None:
        self._sessions: OrderedDict[str, SessionState] = OrderedDict()
        self._lock = threading.Lock()

    def _session(self, session_id: str) -> SessionState:
        now = time.time()
        state = self._sessions.get(session_id)
        if state is None or now - state.touched_at > SESSION_TTL_S:
            state = SessionState()
            self._sessions[session_id] = state
        state.touched_at = now
        self._sessions.move_to_end(session_id)
        while len(self._sessions) > MAX_SESSIONS:
            self._sessions.popitem(last=False)
        return state

    def begin(self, session_id: str, version: int) -> bool:
        """Note a request's version. False means a newer snapshot has already arrived."""

        with self._lock:
            state = self._session(session_id)
            if version < state.latest_version:
                return False
            state.latest_version = version
            return True

    def record(
        self,
        session_id: str,
        version: int,
        *,
        new: Iterable[TrackedMistake] = (),
        seen: Iterable[TrackedMistake] = (),
        resolved: Iterable[str] = (),
    ) -> Decision:
        """Apply one analysis: mistakes to alert about, ones still present, and ones now gone."""

        with self._lock:
            state = self._session(session_id)
            if version < state.latest_version:
                return Decision(stale=True)
            resolved_ids = self._resolve(state, resolved)
            for mistake in seen:
                # Keep the record if there is one, as it holds the hints already given.
                state.known.setdefault(mistake.id, mistake)
            alerted = False
            for mistake in new:
                state.known[mistake.id] = mistake
                state.alerts += 1
                alerted = True
            return Decision(should_quack=alerted, resolved_ids=resolved_ids)

    def clear(self, session_id: str, version: int, mistake_ids: Iterable[str]) -> Decision:
        """The page is empty, or these mistakes' lines were erased."""

        with self._lock:
            # A session with nothing on record has nothing to clear. Not creating one here
            # stops free requests from evicting real students' sessions.
            state = self._sessions.get(session_id)
            ids = tuple(mistake_ids)
            if state is None:
                return Decision(resolved_ids=ids)
            if version < state.latest_version:
                return Decision(stale=True)
            return Decision(resolved_ids=self._resolve(state, ids))

    @staticmethod
    def _resolve(state: SessionState, mistake_ids: Iterable[str]) -> tuple[str, ...]:
        resolved = tuple(mistake_ids)
        for mistake_id in resolved:
            known = state.known.pop(mistake_id, None)
            record = ResolvedRecord(mistake_id, known.category if known else "unknown", time.time())
            state.resolved = [*state.resolved, record][-MAX_RESOLVED_HISTORY:]
        return resolved

    def get_mistake(self, session_id: str, mistake_id: str) -> TrackedMistake | None:
        with self._lock:
            state = self._sessions.get(session_id)
            return state.known.get(mistake_id) if state is not None else None

    def add_hint(self, session_id: str, mistake_id: str, text: str, level: int | None = None, source: str = "model") -> None:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is not None and mistake_id in state.known:
                mistake = state.known[mistake_id]
                mistake.hints_given.append(text)
                if level is not None:
                    mistake.hints_by_level[level] = (text, source)

    def get_session(self, session_id: str) -> SessionState | None:
        with self._lock:
            return self._sessions.get(session_id)
