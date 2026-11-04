"""Everything kept about a student. Every method takes the user it's about, from a verified token.

Writes that can be retried say so in their key, so that a retry changes nothing.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from quack.db.database import Database

OnboardingStatus = Literal["account_created", "calibration_in_progress", "calibration_completed"]
SessionKind = Literal["notebook", "practice", "lesson"]
# With nothing happening for this long, a session is over. The same as how long the server
# remembers a student's open mistakes. See docs/SESSION_RECAP.md.
SESSION_INACTIVE_S = 6 * 3600
MAX_HISTORY = 50


@dataclass(frozen=True)
class StoredSample:
    prompt_id: str
    reference_expression: str
    image_data: str
    strokes_json: str
    canvas_width: float
    created_at: float
    calibration_version: int


class UserStore:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._sequence = 0

    def _next_sequence(self) -> int:
        # Rising even if two events share a clock tick, or the clock steps back.
        self._sequence = max(self._sequence + 1, time.time_ns())
        return self._sequence

    # Profiles

    def profile(self, user_id: str) -> dict[str, Any]:
        """A verified user with no profile yet has only just registered."""

        self.database.execute(
            "INSERT INTO profiles (user_id, onboarding_status, created_at) VALUES (?, 'account_created', ?) "
            "ON CONFLICT (user_id) DO NOTHING",
            (user_id, time.time()),
        )
        row = self.database.one("SELECT * FROM profiles WHERE user_id = ?", (user_id,))
        assert row is not None
        return row

    # Calibration

    def save_sample(
        self,
        user_id: str,
        *,
        version: int,
        prompt_id: str,
        reference_expression: str,
        strokes_json: str,
        canvas_width: float,
        image_data: str,
        required: list[str],
    ) -> dict[str, Any]:
        """Saving again replaces that sample, which is what makes a retried upload harmless."""

        self.profile(user_id)
        now = time.time()
        with self.database.transaction():
            self.database.execute(
                "INSERT INTO calibration_samples (user_id, calibration_version, prompt_id, reference_expression, "
                "strokes_json, canvas_width, image_data, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (user_id, calibration_version, prompt_id) DO UPDATE SET "
                "reference_expression = excluded.reference_expression, strokes_json = excluded.strokes_json, "
                "canvas_width = excluded.canvas_width, image_data = excluded.image_data, created_at = excluded.created_at",
                (user_id, version, prompt_id, reference_expression, strokes_json, canvas_width, image_data, now),
            )
            saved = {
                row["prompt_id"]
                for row in self.database.query(
                    "SELECT prompt_id FROM calibration_samples WHERE user_id = ? AND calibration_version = ?",
                    (user_id, version),
                )
            }
            if set(required) <= saved:
                self.database.execute(
                    "UPDATE profiles SET onboarding_status = 'calibration_completed', calibration_version = ?, "
                    "calibration_completed_at = ? WHERE user_id = ?",
                    (version, now, user_id),
                )
            else:
                # Never moves a completed profile backwards, so that redoing one sample can't ask for the rest.
                self.database.execute(
                    "UPDATE profiles SET onboarding_status = 'calibration_in_progress' "
                    "WHERE user_id = ? AND onboarding_status = 'account_created'",
                    (user_id,),
                )
        return self.profile(user_id)

    def samples(self, user_id: str, version: int | None = None) -> list[StoredSample]:
        chosen = version if version is not None else self.profile(user_id)["calibration_version"]
        if chosen is None:
            return []
        rows = self.database.query(
            "SELECT * FROM calibration_samples WHERE user_id = ? AND calibration_version = ? ORDER BY created_at",
            (user_id, chosen),
        )
        return [
            StoredSample(
                row["prompt_id"], row["reference_expression"], row["image_data"], row["strokes_json"],
                row["canvas_width"], row["created_at"], row["calibration_version"],
            )
            for row in rows
        ]

    def saved_prompt_ids(self, user_id: str, version: int) -> list[str]:
        rows = self.database.query(
            "SELECT prompt_id FROM calibration_samples WHERE user_id = ? AND calibration_version = ? ORDER BY created_at",
            (user_id, version),
        )
        return [row["prompt_id"] for row in rows]

    def delete_calibration(self, user_id: str) -> None:
        """The account stays calibrated, so it isn't asked again. It's just read without references now."""

        self.database.execute("DELETE FROM calibration_samples WHERE user_id = ?", (user_id,))

    def restart_calibration(self, user_id: str) -> None:
        self.profile(user_id)
        with self.database.transaction():
            self.database.execute("DELETE FROM calibration_samples WHERE user_id = ?", (user_id,))
            self.database.execute(
                "UPDATE profiles SET onboarding_status = 'calibration_in_progress', calibration_completed_at = NULL, "
                "calibration_version = NULL WHERE user_id = ?",
                (user_id,),
            )

    # Sessions

    def _new_session(
        self, user_id: str, kind: SessionKind, parent: str | None = None, topic: str | None = None
    ) -> dict[str, Any]:
        now = time.time()
        session_id = str(uuid.uuid4())
        self.database.execute(
            "INSERT INTO study_sessions (id, user_id, kind, parent_session_id, topic, started_at, last_active_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, user_id, kind, parent, topic, now, now),
        )
        return self.session(user_id, session_id)  # type: ignore[return-value]

    def new_practice_session(self, user_id: str, parent: str | None, topic: str) -> dict[str, Any]:
        return self._new_session(user_id, "practice", parent, topic)

    def new_lesson_session(self, user_id: str, course_id: str, level_id: str) -> dict[str, Any]:
        return self._new_session(user_id, "lesson", None, f"{course_id}/{level_id}")

    def open_lesson_session(self, user_id: str, course_id: str, level_id: str) -> dict[str, Any] | None:
        return self.database.one(
            "SELECT * FROM study_sessions WHERE user_id = ? AND kind = 'lesson' AND topic = ? AND finished_at IS NULL "
            "ORDER BY started_at DESC LIMIT 1",
            (user_id, f"{course_id}/{level_id}"),
        )

    def latest_lesson_session(self, user_id: str) -> dict[str, Any] | None:
        return self.database.one(
            "SELECT * FROM study_sessions WHERE user_id = ? AND kind = 'lesson' ORDER BY last_active_at DESC LIMIT 1",
            (user_id,),
        )

    def finish_lesson_session(self, user_id: str, session_id: str) -> None:
        self.database.execute(
            "UPDATE study_sessions SET finished_at = ?, finish_reason = 'finished' "
            "WHERE id = ? AND user_id = ? AND kind = 'lesson' AND finished_at IS NULL",
            (time.time(), session_id, user_id),
        )

    def session(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        return self.database.one("SELECT * FROM study_sessions WHERE id = ? AND user_id = ?", (session_id, user_id))

    def open_notebook_session(self, user_id: str) -> dict[str, Any] | None:
        return self.database.one(
            "SELECT * FROM study_sessions WHERE user_id = ? AND kind = 'notebook' AND finished_at IS NULL "
            "ORDER BY started_at DESC LIMIT 1",
            (user_id,),
        )

    def current_notebook_session(self, user_id: str) -> dict[str, Any]:
        # Two tabs, or two requests at once, must share one. Events go to one and the recap comes from it.
        with self.database.transaction():
            return self.open_notebook_session(user_id) or self._new_session(user_id, "notebook")

    def touch_session(self, user_id: str, session_id: str, *, checked: bool = False) -> None:
        self.database.execute(
            "UPDATE study_sessions SET last_active_at = ?, checks = checks + ? WHERE id = ? AND user_id = ?",
            (time.time(), 1 if checked else 0, session_id, user_id),
        )

    def finish_session(
        self, user_id: str, session_id: str, recap: dict[str, Any], reason: str, finished_at: float
    ) -> bool:
        """Only the first finish counts, so a recap can't change once it has been seen."""

        return bool(
            self.database.execute(
                "UPDATE study_sessions SET finished_at = ?, finish_reason = ?, recap_json = ? "
                "WHERE id = ? AND user_id = ? AND finished_at IS NULL",
                (finished_at, reason, json.dumps(recap), session_id, user_id),
            )
        )

    def finished_sessions(
        self, user_id: str, kind: SessionKind = "notebook", limit: int = MAX_HISTORY
    ) -> list[dict[str, Any]]:
        return self.database.query(
            "SELECT * FROM study_sessions WHERE user_id = ? AND kind = ? AND finished_at IS NOT NULL "
            "ORDER BY finished_at DESC LIMIT ?",
            (user_id, kind, limit),
        )

    def practice_sessions_of(self, user_id: str, parent: str) -> list[dict[str, Any]]:
        return self.database.query(
            "SELECT * FROM study_sessions WHERE user_id = ? AND kind = 'practice' AND parent_session_id = ? "
            "ORDER BY started_at",
            (user_id, parent),
        )

    # Mistakes and events

    def record_mistake_fact(
        self,
        user_id: str,
        mistake_id: str,
        category: str,
        error_type: str,
        parsed_operation: str,
        topic: str,
        current_step: str,
        written_step: str = "",
        corrected_step: str = "",
    ) -> None:
        """A mistake reported again is back on the page, whatever became of it before."""

        self.database.execute(
            "INSERT INTO mistake_facts (user_id, mistake_id, category, error_type, parsed_operation, topic, current_step, "
            "updated_at, written_step, corrected_step) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (user_id, mistake_id) DO UPDATE SET resolved_how = NULL, updated_at = excluded.updated_at, "
            "written_step = excluded.written_step, corrected_step = excluded.corrected_step",
            (
                user_id, mistake_id, category, error_type, parsed_operation, topic, current_step, time.time(),
                written_step or current_step, corrected_step,
            ),
        )

    def mistake_facts(self, user_id: str, mistake_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not mistake_ids:
            return {}
        rows = self.database.query("SELECT * FROM mistake_facts WHERE user_id = ?", (user_id,))
        wanted = set(mistake_ids)
        return {row["mistake_id"]: row for row in rows if row["mistake_id"] in wanted}

    def notebook_mistake_events(self, user_id: str) -> list[dict[str, Any]]:
        """Mistakes made while writing, in the notebook or a lesson. Practice answers are counted with practice."""

        return self.database.query(
            "SELECT * FROM learning_events WHERE user_id = ? AND source IN ('notebook', 'lesson') "
            "AND mistake_id IS NOT NULL ORDER BY sequence",
            (user_id,),
        )

    def problems_from_mistakes(self, user_id: str) -> list[dict[str, Any]]:
        return self.database.query(
            "SELECT * FROM practice_problems WHERE user_id = ? AND source_mistake_id IS NOT NULL", (user_id,)
        )

    def delete_mistake(self, user_id: str, mistake_id: str) -> None:
        """Gone from My Mistakes, and from what later recaps count. A recap already written keeps its
        numbers, which were what was seen, and loses the line itself."""

        fact = self.mistake_fact(user_id, mistake_id)
        steps = {fact["written_step"], fact["current_step"]} if fact else set()
        sessions = self.database.query(
            "SELECT DISTINCT session_id FROM learning_events WHERE user_id = ? AND mistake_id = ?", (user_id, mistake_id)
        )
        with self.database.transaction():
            for row in sessions:
                self._drop_from_recap(user_id, row["session_id"], steps)
            self.database.execute("DELETE FROM learning_events WHERE user_id = ? AND mistake_id = ?", (user_id, mistake_id))
            self.database.execute("DELETE FROM mistake_facts WHERE user_id = ? AND mistake_id = ?", (user_id, mistake_id))
            # A lesson question keeps what it counted for, with nothing left to say which mistake it was.
            for link in self.database.query(
                "SELECT problem_id FROM problem_mistakes WHERE user_id = ? AND mistake_id = ?", (user_id, mistake_id)
            ):
                self.database.execute(
                    "UPDATE problem_mistakes SET mistake_id = ?, evidence = '' "
                    "WHERE user_id = ? AND problem_id = ? AND mistake_id = ?",
                    (f"deleted:{uuid.uuid4()}", user_id, link["problem_id"], mistake_id),
                )
            self.database.execute(
                "UPDATE practice_problems SET source_mistake_id = NULL WHERE user_id = ? AND source_mistake_id = ?",
                (user_id, mistake_id),
            )

    def _drop_from_recap(self, user_id: str, session_id: str, steps: set[str | None]) -> None:
        session = self.session(user_id, session_id)
        if session is None or not session["recap_json"]:
            return
        recap = json.loads(session["recap_json"])
        for group in recap.get("topics", []):
            for topic in group.get("topics", []):
                topic["examples"] = [example for example in topic.get("examples", []) if example not in steps]
        self.database.execute(
            "UPDATE study_sessions SET recap_json = ? WHERE id = ? AND user_id = ?", (json.dumps(recap), session_id, user_id)
        )

    def mark_resolved(self, user_id: str, mistake_ids: list[str], how: Literal["verified", "erased"]) -> None:
        for mistake_id in mistake_ids:
            self.database.execute(
                "UPDATE mistake_facts SET resolved_how = ?, updated_at = ? WHERE user_id = ? AND mistake_id = ?",
                (how, time.time(), user_id, mistake_id),
            )

    def mistake_fact(self, user_id: str, mistake_id: str) -> dict[str, Any] | None:
        return self.database.one("SELECT * FROM mistake_facts WHERE user_id = ? AND mistake_id = ?", (user_id, mistake_id))

    def add_event(
        self,
        user_id: str,
        *,
        session_id: str,
        event_type: str,
        source: str,
        dedupe_key: str,
        mistake_id: str | None = None,
        problem_id: str | None = None,
        topic: str | None = None,
        error_type: str | None = None,
        detail: str | None = None,
        help_text: str | None = None,
    ) -> bool:
        """False if an event with this key is already on record, which is what a retry looks like."""

        return bool(
            self.database.execute(
                "INSERT INTO learning_events (id, user_id, session_id, mistake_id, problem_id, event_type, topic, "
                "error_type, source, detail, timestamp, sequence, dedupe_key, help_text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (user_id, dedupe_key) DO NOTHING",
                (
                    str(uuid.uuid4()), user_id, session_id, mistake_id, problem_id, event_type, topic, error_type,
                    source, detail, time.time(), self._next_sequence(), dedupe_key, help_text,
                ),
            )
        )

    def session_events(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        return self.database.query(
            "SELECT * FROM learning_events WHERE user_id = ? AND session_id = ? ORDER BY sequence", (user_id, session_id)
        )

    def mistake_events(self, user_id: str, mistake_id: str) -> list[dict[str, Any]]:
        return self.database.query(
            "SELECT * FROM learning_events WHERE user_id = ? AND mistake_id = ? ORDER BY sequence",
            (user_id, mistake_id),
        )

    def notebook_mistakes_before(self, user_id: str, before_session: str, since: float) -> list[dict[str, Any]]:
        """Mistakes found in other notebook sessions, for telling what keeps coming back."""

        return self.database.query(
            "SELECT DISTINCT found.session_id, found.mistake_id, found.topic FROM learning_events found "
            "WHERE found.user_id = ? AND found.session_id <> ? "
            "AND found.source = 'notebook' AND found.event_type = 'mistake_detected' AND found.timestamp >= ? "
            # A line that two readings have since said was read wrongly never was a mistake.
            "AND NOT EXISTS (SELECT 1 FROM learning_events later WHERE later.user_id = found.user_id "
            "AND later.mistake_id = found.mistake_id AND later.event_type = 'mistake_invalidated' "
            "AND later.detail = 'misread' AND later.sequence > found.sequence)",
            (user_id, before_session, since),
        )

    # Practice

    def add_problem(self, user_id: str, problem: dict[str, Any]) -> None:
        self.database.execute(
            "INSERT INTO practice_problems (id, user_id, session_id, source_mistake_id, topic, difficulty, prompt_math, "
            "prompt_display, expected_answer, verification_method, status, created_at, course_id, level_id, skill_id, "
            "template_id, instruction, probes_json, selection_kind, selection_reason, selection_target, selection_json, "
            "context_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                problem["id"], user_id, problem["session_id"], problem.get("source_mistake_id"), problem["topic"],
                problem["difficulty"], problem["prompt_math"], problem["prompt_display"], problem["expected_answer"],
                problem["verification_method"], time.time(), problem.get("course_id"), problem.get("level_id"),
                problem.get("skill_id"), problem.get("template_id"), problem.get("instruction"), problem.get("probes_json"),
                problem.get("selection_kind"), problem.get("selection_reason"), problem.get("selection_target"),
                problem.get("selection_json"), problem.get("context_json"),
            ),
        )

    # Lessons

    def level_problems(self, user_id: str, course_id: str, level_id: str) -> list[dict[str, Any]]:
        return self.database.query(
            "SELECT * FROM practice_problems WHERE user_id = ? AND course_id = ? AND level_id = ? ORDER BY created_at, id",
            (user_id, course_id, level_id),
        )

    def course_problems(self, user_id: str, course_id: str) -> list[dict[str, Any]]:
        return self.database.query(
            "SELECT * FROM practice_problems WHERE user_id = ? AND course_id = ? ORDER BY created_at, id",
            (user_id, course_id),
        )

    def skip_problem(self, user_id: str, problem_id: str) -> None:
        """Moved on from with nothing verified about it. It says nothing either way."""

        self.database.execute(
            "UPDATE practice_problems SET status = 'skipped', completed_at = ? "
            "WHERE id = ? AND user_id = ? AND status = 'open'",
            (time.time(), problem_id, user_id),
        )

    def mark_assisted(self, user_id: str, problem_id: str) -> None:
        self.database.execute(
            "UPDATE practice_problems SET assisted = 1 WHERE id = ? AND user_id = ?", (problem_id, user_id)
        )

    def link_problem_mistake(
        self, user_id: str, problem_id: str, mistake_id: str, misconception: str, confidence: str, evidence: str
    ) -> bool:
        """The first naming stands: the same mistake read again is the same mistake."""

        return bool(
            self.database.execute(
                "INSERT INTO problem_mistakes (user_id, problem_id, mistake_id, misconception, confidence, evidence, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (user_id, problem_id, mistake_id) DO NOTHING",
                (user_id, problem_id, mistake_id, misconception, confidence, evidence, time.time()),
            )
        )

    def flag_problem_mistake(
        self, user_id: str, session_id: str, mistake_id: str, field: Literal["shown", "helped", "misread"], value: int
    ) -> None:
        """What happened to a verified mistake on the questions of one lesson visit."""

        self.database.execute(
            f"UPDATE problem_mistakes SET {field} = ? WHERE user_id = ? AND mistake_id = ? AND problem_id IN "
            "(SELECT id FROM practice_problems WHERE user_id = ? AND session_id = ?)",
            (value, user_id, mistake_id, user_id, session_id),
        )

    def lock_session(self, user_id: str, session_id: str) -> None:
        """Inside a transaction, on Postgres: one request at a time moves this session on, across processes.
        SQLite's single writer already does that."""

        if self.database.is_postgres:
            self.database.query(
                "SELECT id FROM study_sessions WHERE id = ? AND user_id = ? FOR UPDATE", (session_id, user_id)
            )

    def problem_mistake_links(self, user_id: str, problem_ids: list[str]) -> list[dict[str, Any]]:
        if not problem_ids:
            return []
        wanted = set(problem_ids)
        rows = self.database.query("SELECT * FROM problem_mistakes WHERE user_id = ? ORDER BY created_at", (user_id,))
        return [row for row in rows if row["problem_id"] in wanted]

    def mistake_links(self, user_id: str, mistake_ids: list[str]) -> list[dict[str, Any]]:
        if not mistake_ids:
            return []
        wanted = set(mistake_ids)
        rows = self.database.query("SELECT * FROM problem_mistakes WHERE user_id = ? ORDER BY created_at", (user_id,))
        return [row for row in rows if row["mistake_id"] in wanted]

    def lesson_events(self, user_id: str, session_ids: list[str]) -> list[dict[str, Any]]:
        if not session_ids:
            return []
        wanted = set(session_ids)
        rows = self.database.query(
            "SELECT * FROM learning_events WHERE user_id = ? AND source = 'lesson' ORDER BY sequence", (user_id,)
        )
        return [row for row in rows if row["session_id"] in wanted]

    def level_progress(self, user_id: str, course_id: str) -> dict[str, dict[str, Any]]:
        rows = self.database.query("SELECT * FROM level_progress WHERE user_id = ? AND course_id = ?", (user_id, course_id))
        return {row["level_id"]: row for row in rows}

    def mark_level(self, user_id: str, course_id: str, level_id: str, field: Literal["mastered_at", "moved_on_at"]) -> bool:
        """True only the first time, so a celebration or event can't happen twice."""

        now = time.time()
        with self.database.transaction():
            self.database.execute(
                "INSERT INTO level_progress (user_id, course_id, level_id, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (user_id, course_id, level_id) DO NOTHING",
                (user_id, course_id, level_id, now),
            )
            return bool(
                self.database.execute(
                    f"UPDATE level_progress SET {field} = ?, updated_at = ? "
                    f"WHERE user_id = ? AND course_id = ? AND level_id = ? AND {field} IS NULL",
                    (now, now, user_id, course_id, level_id),
                )
            )

    def problem(self, user_id: str, problem_id: str) -> dict[str, Any] | None:
        return self.database.one("SELECT * FROM practice_problems WHERE id = ? AND user_id = ?", (problem_id, user_id))

    def problems_in(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        return self.database.query(
            "SELECT * FROM practice_problems WHERE user_id = ? AND session_id = ? ORDER BY created_at",
            (user_id, session_id),
        )

    def set_problem_status(self, user_id: str, problem_id: str, status: str) -> None:
        """A problem answered right stays that way, and so does one whose answer was shown."""

        self.database.execute(
            "UPDATE practice_problems SET status = ?, completed_at = ? "
            "WHERE id = ? AND user_id = ? AND status NOT IN ('correct', 'revealed')",
            (status, time.time(), problem_id, user_id),
        )

    def note_attempt(self, user_id: str, problem_id: str) -> None:
        self.database.execute(
            "UPDATE practice_problems SET status = 'attempted' WHERE id = ? AND user_id = ? AND status = 'open'",
            (problem_id, user_id),
        )

    # Deleting

    def delete_learning(self, user_id: str) -> None:
        with self.database.transaction():
            for table in (
                "learning_events", "practice_problems", "mistake_facts", "study_sessions", "problem_mistakes",
                "level_progress",
            ):
                self.database.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))

    def delete_everything(self, user_id: str) -> None:
        with self.database.transaction():
            self.delete_learning(user_id)
            self.database.execute("DELETE FROM calibration_samples WHERE user_id = ?", (user_id,))
            self.database.execute("DELETE FROM profiles WHERE user_id = ?", (user_id,))
