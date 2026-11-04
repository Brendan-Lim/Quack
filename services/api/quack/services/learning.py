"""Turning what happens to verified mistakes into a record that retries can't inflate.

Most of this comes in from the client, because only the client knows what the student was really told:
it throws away replies that arrive after the ink has moved on. The server doesn't take its word for
it, though. An event has to be about a mistake this server's verifier reported to this student, and
its topic comes from that record. A fix has to be one that two readings agreed on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quack.services.user_store import UserStore

SOURCE_BY_KIND = {"notebook": "notebook", "practice": "practice", "lesson": "lesson"}
# Where a mistake stands, going by the last of these that happened to it.
STANDING = {
    "mistake_detected": "open",
    "correction_verified": "corrected",
    "mistake_invalidated": "invalidated",
}


@dataclass(frozen=True)
class ClientEvent:
    id: str
    event_type: str
    mistake_id: str
    session_id: str | None
    reason: str | None = None
    # The line written where an erased mistake was, which two readings have now agreed on.
    rewritten: bool = False


def standing_of(events: list[dict[str, Any]], mistake_id: str) -> str | None:
    standing = None
    for event in events:
        if event["mistake_id"] == mistake_id and event["event_type"] in STANDING:
            standing = STANDING[event["event_type"]]
    return standing


class LearningLog:
    def __init__(self, store: UserStore) -> None:
        self.store = store

    def _session_for(self, user_id: str, session_id: str | None) -> dict[str, Any]:
        """A finished session's recap has been seen, so anything late belongs to the current one."""

        session = self.store.session(user_id, session_id) if session_id else None
        if session is not None and session["finished_at"] is None:
            return session
        if session is not None and session["kind"] in ("practice", "lesson"):
            return session
        return self.store.current_notebook_session(user_id)

    def record(self, user_id: str, event: ClientEvent) -> str | None:
        """Apply one event from the client. Returns why it was refused, or None.

        Sent twice, it has the same effect as once, with the same id or without it: a mistake already
        open can't be detected again, and one already corrected can't be corrected again. That's how
        several alerts for one mistake still count once.
        """

        fact = self.store.mistake_fact(user_id, event.mistake_id)
        if fact is None:
            return "unknown_mistake"
        session = self._session_for(user_id, event.session_id)
        session_id = session["id"]
        with self.store.database.transaction():
            history = self.store.mistake_events(user_id, event.mistake_id)
            within = [item for item in history if item["session_id"] == session_id]
            standing = standing_of(within, event.mistake_id)
            if event.event_type == "correction_verified":
                # Erasing isn't fixing. It takes two readings of what's there now.
                allowed = fact["resolved_how"] == "verified" or (fact["resolved_how"] == "erased" and event.rewritten)
                if not allowed:
                    return "not_verified"
                if standing == "corrected":
                    return None
            already_said = (event.event_type == "mistake_detected" and standing == "open") or (
                event.event_type == "mistake_dismissed"
                and any(item["event_type"] == "mistake_dismissed" for item in within)
            )
            if already_said:
                return None
            # A misreading takes a mistake off a lesson question's record, so it needs the server's own
            # word too: two readings that agreed the mistake had gone.
            misread_in_lesson = (
                session["kind"] == "lesson" and event.event_type == "mistake_invalidated" and event.reason == "misread"
            )
            if misread_in_lesson and fact["resolved_how"] != "verified":
                return "not_verified"
            self.store.add_event(
                user_id,
                session_id=session_id,
                event_type=event.event_type,
                source=SOURCE_BY_KIND[session["kind"]],
                dedupe_key=f"client:{event.id}",
                mistake_id=event.mistake_id,
                topic=fact["topic"],
                error_type=fact["error_type"],
                detail=event.reason,
            )
            if session["kind"] == "lesson":
                if event.event_type == "mistake_detected":
                    self.store.flag_problem_mistake(user_id, session_id, event.mistake_id, "shown", 1)
                    self.store.flag_problem_mistake(user_id, session_id, event.mistake_id, "misread", 0)
                elif event.event_type == "mistake_invalidated" and event.reason == "misread":
                    self.store.flag_problem_mistake(user_id, session_id, event.mistake_id, "misread", 1)
            self.store.touch_session(user_id, session_id)
        return None

    def help_given(
        self, user_id: str, session_id: str | None, mistake_id: str, kind: str, level: int, text: str | None = None
    ) -> None:
        """A hint or explanation, recorded by the server as it hands it over."""

        fact = self.store.mistake_fact(user_id, mistake_id)
        if fact is None:
            return
        session = self._session_for(user_id, session_id)
        history = self.store.mistake_events(user_id, mistake_id)
        fixes = sum(event["event_type"] == "correction_verified" for event in history)
        self.store.add_event(
            user_id,
            session_id=session["id"],
            event_type=kind,
            source=SOURCE_BY_KIND[session["kind"]],
            # Asking again repeats the same words, and it isn't more help. Unless it's been fixed since,
            # and has come back, when it's new help with the new try.
            dedupe_key=f"{kind}:{session['id']}:{mistake_id}:{level}" + (f":{fixes}" if fixes else ""),
            mistake_id=mistake_id,
            topic=fact["topic"],
            error_type=fact["error_type"],
            detail=str(level),
            help_text=text,
        )
        if session["kind"] == "lesson":
            self.store.flag_problem_mistake(user_id, session["id"], mistake_id, "helped", 1)
