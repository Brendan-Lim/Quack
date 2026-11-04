"""My Mistakes: what's in it, what isn't, and whose it is. Meta's API is mocked. The verifier is real."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from quack.db.database import Database
from quack.services.user_store import UserStore
from tests.test_learning import Model, Student, model, student  # noqa: F401
from tests.test_line_memory import compact
from tests.test_pipeline import png


def entries(who: Student, **params: str) -> list[dict[str, Any]]:
    response = who.client.get("/api/mistakes", params=params, headers=who.headers)
    assert response.status_code == 200, response.text
    return response.json()["entries"]


def detail(who: Student, mistake: str) -> dict[str, Any]:
    response = who.client.get(f"/api/mistakes/{mistake}", headers=who.headers)
    assert response.status_code == 200, response.text
    return response.json()


class TestWhatIsSaved:
    def test_an_entry_says_what_happened(self, student: Student, model: Model) -> None:  # noqa: F811
        mistake = student.wrong(model, "d/dx(x^3) = 2x")
        student.hint(mistake)
        student.fix(model, "d/dx(x^3) = 3x^2", mistake)
        entry = detail(student, mistake)
        assert entry["written"] == "d/dx(x^3) = 2x"
        assert entry["correctedStep"] == "d/dx(x^3) = 3x^2"
        assert entry["status"] == "corrected"
        assert entry["independent"] is False
        assert entry["topic"] in ("power_rule", "differentiation")
        assert entry["hintsUsed"] == 1
        assert entry["explained"] is False
        assert entry["sessionId"] == student.session
        assert entry["practiceTopic"] == "power_rule"
        assert entry["hints"][0]["level"] == 1
        assert entry["hints"][0]["text"]

    def test_fixed_with_no_help_says_so(self, student: Student, model: Model) -> None:  # noqa: F811
        mistake = student.wrong(model, "3 * 4 = 10")
        student.fix(model, "3 * 4 = 12", mistake)
        assert detail(student, mistake)["independent"] is True

    def test_still_open_stays_open(self, student: Student, model: Model) -> None:  # noqa: F811
        mistake = student.wrong(model, "3 * 4 = 10")
        assert entries(student)[0]["status"] == "open"
        assert detail(student, mistake)["correctedStep"] == "3 * 4 = 12"

    def test_an_equation_step_reads_as_the_line_it_was(self, student: Student, model: Model) -> None:  # noqa: F811
        student.version += 1
        model.reply = [compact("2x + 3 = 7", continues_previous="no"), compact("2x = 5", continues_previous="yes")]
        reply = student.client.post(
            "/api/analyze",
            json={
                "sessionId": student.page, "snapshotVersion": student.version, "image": png(),
                "inkLines": [{"id": "a", "inImage": True}, {"id": "b", "inImage": True}], "studySessionId": student.session,
            },
            headers=student.headers,
        ).json()
        mistake = reply["mistakes"][0]["id"]
        student.event("mistake_detected", mistake)
        entry = detail(student, mistake)
        assert entry["written"] == "2x = 5"
        assert entry["correctedStep"] == "x = 2"


class TestNoInflation:
    def test_seen_many_times_is_one_entry(self, student: Student, model: Model) -> None:  # noqa: F811
        mistake = student.wrong(model, "3 * 4 = 10")
        for _ in range(3):
            student.check(model, "3 * 4 = 10", [mistake])
            student.event("mistake_detected", mistake)
        assert [entry["id"] for entry in entries(student)] == [mistake]

    def test_a_retried_event_is_one_event(self, student: Student, model: Model) -> None:  # noqa: F811
        mistake = student.wrong(model, "3 * 4 = 10")
        student.event("mistake_dismissed", mistake, "same-event-id")
        student.event("mistake_dismissed", mistake, "same-event-id")
        assert len(entries(student)) == 1

    def test_repeated_hints_are_counted_once_per_level(self, student: Student, model: Model) -> None:  # noqa: F811
        mistake = student.wrong(model, "3 * 4 = 10")
        student.hint(mistake)
        student.hint(mistake)
        assert entries(student)[0]["hintsUsed"] == 1

    def test_correct_work_never_enters(self, student: Student, model: Model) -> None:  # noqa: F811
        student.check(model, "3 * 4 = 12")
        assert entries(student) == []

    def test_uncertain_work_never_enters(self, student: Student, model: Model) -> None:  # noqa: F811
        student.version += 1
        model.reply = [compact("3 * 4 = 10", legibility="unsure")]
        student.client.post(
            "/api/analyze",
            json={"sessionId": student.page, "snapshotVersion": student.version, "image": png(), "inkLines": [{"id": "z", "inImage": True}]},
            headers=student.headers,
        )
        assert entries(student) == []

    def test_a_mistake_that_was_reported_and_never_shown_never_enters(self, student: Student, model: Model) -> None:  # noqa: F811
        # The client threw the reply away, as the ink had moved on, so it sent no event.
        assert student.check(model, "3 * 4 = 10")["mistakes"]
        assert entries(student) == []

    def test_an_ambiguous_line_never_enters(self, student: Student, model: Model) -> None:  # noqa: F811
        reply = student.check(model, "3x + 2x = 5")
        assert reply["mistakes"] == []
        assert entries(student) == []

    def test_a_misreading_taken_back_does_not_persist(self, student: Student, model: Model) -> None:  # noqa: F811
        mistake = student.wrong(model, "3 * 4 = 18")
        student.event("mistake_invalidated", mistake, reason="misread")
        assert entries(student) == []
        recap = student.finish()
        assert recap["glance"]["found"] == 0

    def test_erasing_is_not_correcting(self, student: Student, model: Model) -> None:  # noqa: F811
        mistake = student.wrong(model, "3 * 4 = 10")
        student.event("mistake_invalidated", mistake, reason="erased")
        entry = entries(student)[0]
        assert entry["status"] == "erased"
        assert entry["independent"] is False

    def test_independent_mistakes_have_their_own_entries(self, student: Student, model: Model) -> None:  # noqa: F811
        one = student.wrong(model, "3 * 4 = 10")
        two = student.wrong(model, "∫ x dx = 2")
        student.hint(two)
        assert {entry["id"]: entry["hintsUsed"] for entry in entries(student)} == {one: 0, two: 1}

    def test_practice_mistakes_stay_with_practice(self, student: Student, model: Model) -> None:  # noqa: F811
        problem = student.client.post("/api/practice/sessions", json={"topic": "multiplication"}, headers=student.headers).json()
        model.reply = [compact("1", continues_previous="no")]
        reply = student.client.post(
            f"/api/practice/problems/{problem['id']}/check",
            json={"sessionId": problem["checkSessionId"], "snapshotVersion": 1, "image": png(), "inkLines": [{"id": "p", "inImage": True}]},
            headers=student.headers,
        ).json()
        mistake = reply["mistakes"][0]["id"]
        student.client.post(
            "/api/learning/events",
            json={"events": [{"id": "abcdefgh1", "eventType": "mistake_detected", "mistakeId": mistake, "sessionId": problem["sessionId"]}]},
            headers=student.headers,
        )
        assert entries(student) == []


class TestLookingBack:
    def test_newest_first(self, student: Student, model: Model) -> None:  # noqa: F811
        one = student.wrong(model, "3 * 4 = 10")
        two = student.wrong(model, "5 * 5 = 20")
        assert [entry["id"] for entry in entries(student)] == [two, one]

    def test_a_recap_can_ask_for_its_own(self, student: Student, model: Model) -> None:  # noqa: F811
        first = student.wrong(model, "3 * 4 = 10")
        earlier = student.session
        student.finish()
        student.session = student.current()["session"]["id"]
        second = student.wrong(model, "5 * 5 = 20")
        assert [entry["id"] for entry in entries(student, session=earlier)] == [first]
        assert [entry["id"] for entry in entries(student, session=student.session)] == [second]

    def test_the_same_mistake_in_a_later_session_is_the_same_entry(self, student: Student, model: Model) -> None:  # noqa: F811
        mistake = student.wrong(model, "3 * 4 = 10")
        student.fix(model, "3 * 4 = 12", mistake)
        earlier = student.session
        student.finish()
        student.session = student.current()["session"]["id"]
        assert student.wrong(model, "3 * 4 = 10") == mistake
        entry = entries(student)[0]
        assert entry["status"] == "open"
        assert entry["sessionIds"] == [earlier, student.session]

    def test_practice_from_an_entry_is_linked(self, student: Student, model: Model) -> None:  # noqa: F811
        mistake = student.wrong(model, "3 * 4 = 10")
        problem = student.client.post(
            "/api/practice/sessions", json={"topic": "multiplication", "sourceMistakeId": mistake}, headers=student.headers
        ).json()
        row = student.client.app.state.services.accounts.store.problem  # type: ignore[attr-defined]
        user = student.client.get("/api/profile", headers=student.headers).json()["userId"]
        assert row(user, problem["id"])["source_mistake_id"] == mistake

    def test_nobody_else_can_be_linked(self, accounts_client: TestClient, model: Model) -> None:  # noqa: F811
        owner, other = Student(accounts_client), Student(accounts_client, "b@example.com")
        mistake = owner.wrong(model, "3 * 4 = 10")
        problem = other.client.post(
            "/api/practice/sessions", json={"topic": "multiplication", "sourceMistakeId": mistake}, headers=other.headers
        ).json()
        user = other.client.get("/api/profile", headers=other.headers).json()["userId"]
        assert other.client.app.state.services.accounts.store.problem(user, problem["id"])["source_mistake_id"] is None  # type: ignore[attr-defined]


class TestIsolationAndDeleting:
    def test_each_account_has_its_own(self, accounts_client: TestClient, model: Model) -> None:  # noqa: F811
        one, two = Student(accounts_client), Student(accounts_client, "b@example.com")
        mistake = one.wrong(model, "3 * 4 = 10")
        assert entries(two) == []
        assert two.client.get(f"/api/mistakes/{mistake}", headers=two.headers).status_code == 404

    def test_deleting_someone_elses_does_nothing(self, accounts_client: TestClient, model: Model) -> None:  # noqa: F811
        one, two = Student(accounts_client), Student(accounts_client, "b@example.com")
        mistake = one.wrong(model, "3 * 4 = 10")
        assert two.client.delete(f"/api/mistakes/{mistake}", headers=two.headers).status_code == 200
        assert len(entries(one)) == 1

    def test_signed_out_gets_nothing(self, accounts_client: TestClient) -> None:
        assert accounts_client.get("/api/mistakes").status_code == 401
        assert accounts_client.get("/api/mistakes/m_1").status_code == 401
        assert accounts_client.delete("/api/mistakes/m_1").status_code == 401

    def test_one_can_be_deleted(self, student: Student, model: Model) -> None:  # noqa: F811
        keep = student.wrong(model, "3 * 4 = 10")
        drop = student.wrong(model, "5 * 5 = 20")
        assert student.client.delete(f"/api/mistakes/{drop}", headers=student.headers).status_code == 200
        assert [entry["id"] for entry in entries(student)] == [keep]
        assert student.client.get(f"/api/mistakes/{drop}", headers=student.headers).status_code == 404

    def test_deleting_all_learning_empties_it(self, student: Student, model: Model) -> None:  # noqa: F811
        student.wrong(model, "3 * 4 = 10")
        student.client.delete("/api/account/learning", headers=student.headers)
        assert entries(student) == []


class TestComingFromAnOlderDatabase:
    def test_columns_are_added_and_rows_are_kept(self, tmp_path: Path) -> None:
        path = tmp_path / "old.db"
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE mistake_facts (user_id TEXT NOT NULL, mistake_id TEXT NOT NULL, category TEXT NOT NULL, error_type TEXT NOT NULL,
              parsed_operation TEXT NOT NULL, topic TEXT NOT NULL, current_step TEXT NOT NULL, resolved_how TEXT,
              updated_at DOUBLE PRECISION NOT NULL, PRIMARY KEY (user_id, mistake_id));
            INSERT INTO mistake_facts VALUES ('u', 'm_1', 'arithmetic', 'arithmetic_error', 'multiplication', 'multiplication', '3 * 4 = 10', NULL, 1.0);
            CREATE TABLE learning_events (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL, mistake_id TEXT, problem_id TEXT,
              event_type TEXT NOT NULL, topic TEXT, error_type TEXT, source TEXT NOT NULL, detail TEXT, timestamp DOUBLE PRECISION NOT NULL,
              sequence BIGINT NOT NULL, dedupe_key TEXT NOT NULL, UNIQUE (user_id, dedupe_key));
            INSERT INTO learning_events VALUES ('e', 'u', 's', 'm_1', NULL, 'mistake_detected', 'multiplication', 'arithmetic_error', 'notebook', NULL, 2.0, 1, 'k');
            """
        )
        connection.commit()
        connection.close()
        store = UserStore(Database(path=path))
        from quack.services.mistake_notebook import notebook

        entry = notebook(store, "u").entries[0]
        assert entry.written == "3 * 4 = 10"
        assert entry.status == "open"
        # And opening it again changes nothing.
        assert notebook(UserStore(Database(path=path)), "u").entries[0].written == "3 * 4 = 10"


class TestRecapCopy:
    DEVELOPER_WORDS = ("Quack has practice", "invent", "verify", "verified", "topic", "unclassified")

    def all_text(self, recap: dict[str, Any]) -> str:
        fields = [recap["opening"], recap.get("note") or "", *recap["observations"], *recap["leftOpen"]]
        if recap["recommendation"]:
            fields.append(recap["recommendation"]["reason"])
        return " ".join(fields)

    def test_no_developer_language_in_any_case(self, student: Student, model: Model) -> None:  # noqa: F811
        for text in (self.all_text(student.finish()),):
            for word in self.DEVELOPER_WORDS:
                assert word not in text
        student.session = student.current()["session"]["id"]
        one = student.wrong(model, "3 * 4 = 10")
        student.hint(one)
        student.fix(model, "3 * 4 = 12", one)
        two = student.wrong(model, "x^2 * x^3 = x^6")
        student.event("mistake_invalidated", two, reason="erased")
        student.wrong(model, "1/2 + 1/3 = 2/5")
        text = self.all_text(student.finish())
        for word in self.DEVELOPER_WORDS:
            assert word not in text, text

    def test_the_counts_and_words_agree(self, student: Student, model: Model) -> None:  # noqa: F811
        mistake = student.wrong(model, "3 * 4 = 10")
        student.hint(mistake)
        student.fix(model, "3 * 4 = 12", mistake)
        recap = student.finish()
        assert recap["opening"] == "You worked through the mistake I spotted this session."
        assert recap["observations"] == ["You worked through a multiplication mistake with a little help."]

    def test_a_recap_from_before_reads_the_same_as_one_from_today(self, student: Student, model: Model) -> None:  # noqa: F811
        import json

        mistake = student.wrong(model, "x^2 * x^3 = x^6")
        student.fix(model, "x^2 * x^3 = x^5", mistake)
        student.finish()
        database = student.client.app.state.services.accounts.store.database  # type: ignore[attr-defined]
        row = database.one("SELECT recap_json FROM study_sessions WHERE id = ?", (student.session,))
        old = json.loads(row["recap_json"])
        old |= {"note": "These mistakes aren't in a topic Quack has practice questions for yet.", "opening": "", "assistedLabels": []}
        database.execute("UPDATE study_sessions SET recap_json = ? WHERE id = ?", (json.dumps(old), student.session))
        recap = student.client.get(f"/api/sessions/{student.session}/recap", headers=student.headers).json()["recap"]
        assert "aren't in a topic" not in json.dumps(recap)
        assert recap["opening"]
        assert recap["glance"]["found"] == 1

    def test_a_single_slip_is_not_a_weakness(self, student: Student, model: Model) -> None:  # noqa: F811
        student.wrong(model, "3 * 4 = 10")
        text = self.all_text(student.finish())
        for claim in ("weak", "struggl", "keep coming back", "recurring", "always"):
            assert claim not in text.lower()

    def test_no_achievement_is_invented(self, student: Student, model: Model) -> None:  # noqa: F811
        student.wrong(model, "3 * 4 = 10")
        recap = student.finish()
        assert recap["observations"] == []
        assert recap["opening"] == "I spotted one mistake this session."

    def test_every_recommended_topic_has_questions(self, student: Student, model: Model) -> None:  # noqa: F811
        from quack.services.practice import SUPPORTED
        from quack.services.topics import TOPICS

        assert all(topic.practice in SUPPORTED for topic in TOPICS.values() if topic.practice)
        student.wrong(model, "12 + 5 = 18")
        recommendation = student.finish()["recommendation"]
        assert recommendation["topic"] == "addition_subtraction"
        assert student.client.post(
            "/api/practice/sessions", json={"topic": recommendation["topic"]}, headers=student.headers
        ).status_code == 200
