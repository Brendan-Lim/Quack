"""What gets counted, and that it's counted once. Meta's API is mocked. The verifier is real."""

from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from quack.db.database import Database
from quack.main import build_services, create_app
from quack.services.topics import topic_of
from quack.services.user_store import SESSION_INACTIVE_S, UserStore
from tests.accounts_support import account_settings, register
from tests.test_line_memory import compact
from tests.test_pipeline import CHAT, completion, png


class Model:
    def __init__(self) -> None:
        self.reply: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["response_format"]["json_schema"]["name"] == "CompactReading":
            return completion({"lines": self.reply})
        return completion({"level": 3, "text": "Think about what the multiplication means.", "references_step": "x"})


@pytest.fixture
def model() -> Any:
    with respx.mock:
        answer = Model()
        respx.post(CHAT).mock(side_effect=answer)
        yield answer


class Student:
    """A signed-in student going through a session the way the app would drive it."""

    def __init__(self, client: TestClient, email: str = "a@example.com") -> None:
        self.client = client
        self.headers = register(client, email)
        self.version = 0
        self.session = self.current()["session"]["id"]
        self.page = "page-1"

    def current(self) -> dict[str, Any]:
        return self.client.post("/api/sessions/current", headers=self.headers).json()

    def check(self, model: Model, math: str, acknowledged: list[str] | None = None) -> dict[str, Any]:
        self.version += 1
        model.reply = [compact(math, continues_previous="no")]
        response = self.client.post(
            "/api/analyze",
            json={
                "sessionId": self.page,
                "snapshotVersion": self.version,
                "image": png(),
                "acknowledgedMistakeIds": acknowledged or [],
                "inkLines": [{"id": f"line-{self.version}", "inImage": True}],
                "studySessionId": self.session,
            },
            headers=self.headers,
        )
        assert response.status_code == 200, response.text
        return response.json()

    def event(self, kind: str, mistake: str, event_id: str | None = None, **extra: Any) -> dict[str, Any]:
        body = {"id": event_id or uuid.uuid4().hex, "eventType": kind, "mistakeId": mistake, "sessionId": self.session} | extra
        response = self.client.post("/api/learning/events", json={"events": [body]}, headers=self.headers)
        assert response.status_code == 200, response.text
        return response.json()

    def wrong(self, model: Model, math: str) -> str:
        """Writes something wrong, and the app shows the alert."""

        mistake = self.check(model, math)["mistakes"][0]["id"]
        assert not self.event("mistake_detected", mistake)["refused"]
        return mistake

    def fix(self, model: Model, math: str, mistake: str) -> None:
        reply = self.check(model, math, [mistake])
        assert reply["resolvedMistakeIds"] == [mistake], reply
        assert not self.event("correction_verified", mistake)["refused"]

    def hint(self, mistake: str, level: int = 1) -> None:
        body = {"sessionId": self.page, "mistakeId": mistake, "level": level, "studySessionId": self.session}
        assert self.client.post("/api/hint", json=body, headers=self.headers).status_code == 200

    def explain(self, mistake: str) -> None:
        body = {"sessionId": self.page, "mistakeId": mistake, "studySessionId": self.session}
        assert self.client.post("/api/explain", json=body, headers=self.headers).status_code == 200

    def finish(self) -> dict[str, Any]:
        response = self.client.post(f"/api/sessions/{self.session}/finish", headers=self.headers)
        assert response.status_code == 200, response.text
        return response.json()


@pytest.fixture
def student(accounts_client: TestClient) -> Student:
    return Student(accounts_client)


class TestTheJourneyInTheBrief:
    def test_two_mistakes_one_hint_one_alone(self, student: Student, model: Model) -> None:
        multiplication = student.wrong(model, "3 * 4 = 10")
        student.hint(multiplication)
        student.fix(model, "3 * 4 = 12", multiplication)

        derivative = student.wrong(model, "d/dx(x^2 + 2x) = 3x + 2")
        student.fix(model, "d/dx(x^2 + 2x) = 2x + 2", derivative)

        recap = student.finish()
        glance = recap["glance"]
        assert glance["found"] == 2
        assert glance["corrected"] == 2
        assert glance["unresolved"] == 0
        assert glance["hints"] == 1
        assert glance["independent"] == 1
        assert glance["assisted"] == 1
        assert glance["explanations"] == 0
        labels = {topic["label"] for group in recap["topics"] for topic in group["topics"]}
        assert labels == {"Multiplication", "Power rule"}
        assert recap["recommendation"]["topic"] == "multiplication"
        assert recap["recommendation"]["reason"] == "You used a hint on this, so a similar question could help."


class TestCounting:
    def test_one_mistake_quacked_at_again_counts_once(self, student: Student, model: Model) -> None:
        mistake = student.wrong(model, "2 * 3 = 5")
        for _ in range(3):
            student.check(model, "2 * 3 = 5")
            assert not student.event("mistake_detected", mistake)["refused"]
        assert student.finish()["glance"]["found"] == 1

    def test_the_same_event_sent_twice_counts_once(self, student: Student, model: Model) -> None:
        mistake = student.check(model, "2 * 3 = 5")["mistakes"][0]["id"]
        for _ in range(3):
            student.event("mistake_detected", mistake, event_id="the-same-event-id")
        student.check(model, "2 * 3 = 6", [mistake])
        for _ in range(3):
            student.event("correction_verified", mistake, event_id="the-same-fix-id")
        glance = student.finish()["glance"]
        assert (glance["found"], glance["corrected"]) == (1, 1)
        rows = student.client.app.state.services.accounts.store.database.query("SELECT event_type FROM learning_events")
        assert sorted(row["event_type"] for row in rows) == ["correction_verified", "mistake_detected"]

    def test_a_second_correction_with_a_new_id_counts_once(self, student: Student, model: Model) -> None:
        mistake = student.wrong(model, "2 * 3 = 5")
        student.fix(model, "2 * 3 = 6", mistake)
        student.event("correction_verified", mistake)
        assert student.finish()["glance"]["corrected"] == 1

    def test_different_mistakes_count_separately(self, student: Student, model: Model) -> None:
        student.wrong(model, "2 * 3 = 5")
        student.wrong(model, "4 * 5 = 21")
        assert student.finish()["glance"]["found"] == 2

    def test_uncertainty_isnt_a_mistake(self, student: Student, model: Model) -> None:
        model.reply = [compact("2 * 3 = 5", legibility="unsure", alternatives=["2 * 3 = 6"])]
        reply = student.client.post(
            "/api/analyze",
            json={"sessionId": "p", "snapshotVersion": 1, "image": png(), "inkLines": [{"id": "l", "inImage": True}]},
            headers=student.headers,
        ).json()
        assert reply["mistakes"] == []
        assert student.finish()["glance"]["found"] == 0

    def test_only_mistakes_the_verifier_reported_can_be_recorded(self, student: Student) -> None:
        refused = student.event("mistake_detected", "m_invented")["refused"]
        assert refused[0]["reason"] == "unknown_mistake"
        assert student.finish()["glance"]["found"] == 0

    def test_another_students_mistake_cant_be_recorded(self, accounts_client: TestClient, model: Model) -> None:
        one, other = Student(accounts_client), Student(accounts_client, "b@example.com")
        mistake = one.wrong(model, "2 * 3 = 5")
        assert other.event("mistake_detected", mistake)["refused"][0]["reason"] == "unknown_mistake"

    def test_a_session_id_from_another_account_goes_nowhere(self, accounts_client: TestClient, model: Model) -> None:
        one, other = Student(accounts_client), Student(accounts_client, "b@example.com")
        mistake = other.check(model, "2 * 3 = 5")["mistakes"][0]["id"]
        other.event("mistake_detected", mistake, sessionId=one.session)
        assert one.finish()["glance"]["found"] == 0
        assert other.finish()["glance"]["found"] == 1

    def test_unfinished_sessions_of_other_accounts_cant_be_finished(self, accounts_client: TestClient) -> None:
        one, other = Student(accounts_client), Student(accounts_client, "b@example.com")
        response = accounts_client.post(f"/api/sessions/{one.session}/finish", headers=other.headers)
        assert response.status_code == 404


class TestCorrections:
    def test_erasing_isnt_correcting(self, student: Student, model: Model) -> None:
        mistake = student.wrong(model, "2 * 3 = 5")
        student.client.post(
            "/api/clear",
            json={"sessionId": student.page, "snapshotVersion": 99, "acknowledgedMistakeIds": [mistake]},
            headers=student.headers,
        )
        refused = student.event("correction_verified", mistake)["refused"]
        assert refused[0]["reason"] == "not_verified"
        student.event("mistake_invalidated", mistake, reason="erased")
        glance = student.finish()["glance"]
        assert (glance["found"], glance["corrected"], glance["erased"], glance["unresolved"]) == (1, 0, 1, 0)

    def test_erasing_and_rewriting_correctly_is_correcting(self, student: Student, model: Model) -> None:
        mistake = student.wrong(model, "2 * 3 = 5")
        student.client.post(
            "/api/clear",
            json={"sessionId": student.page, "snapshotVersion": 99, "acknowledgedMistakeIds": [mistake]},
            headers=student.headers,
        )
        student.event("mistake_invalidated", mistake, reason="erased")
        student.version = 100
        student.check(model, "2 * 3 = 6")
        assert not student.event("correction_verified", mistake, rewritten=True)["refused"]
        glance = student.finish()["glance"]
        assert (glance["corrected"], glance["erased"], glance["independent"]) == (1, 0, 1)

    def test_undoing_an_erase_puts_the_mistake_back(self, student: Student, model: Model) -> None:
        mistake = student.wrong(model, "2 * 3 = 5")
        student.event("mistake_invalidated", mistake, reason="erased")
        student.check(model, "2 * 3 = 5")
        student.event("mistake_detected", mistake)
        glance = student.finish()["glance"]
        assert (glance["found"], glance["erased"], glance["unresolved"]) == (1, 0, 1)

    def test_dismissing_isnt_correcting(self, student: Student, model: Model) -> None:
        mistake = student.wrong(model, "2 * 3 = 5")
        student.event("mistake_dismissed", mistake)
        glance = student.finish()["glance"]
        assert (glance["found"], glance["corrected"], glance["unresolved"]) == (1, 0, 1)

    def test_a_fix_claimed_without_being_seen_is_refused(self, student: Student, model: Model) -> None:
        mistake = student.wrong(model, "2 * 3 = 5")
        assert student.event("correction_verified", mistake)["refused"][0]["reason"] == "not_verified"
        assert student.finish()["glance"]["corrected"] == 0

    def test_another_try_thats_also_wrong_isnt_a_fix(self, student: Student, model: Model) -> None:
        first = student.wrong(model, "2 * 3 = 5")
        student.event("mistake_invalidated", first, reason="replaced")
        second = student.wrong(model, "2 * 3 = 7")
        student.fix(model, "2 * 3 = 6", second)
        glance = student.finish()["glance"]
        assert (glance["found"], glance["corrected"], glance["triedAgain"], glance["unresolved"]) == (2, 1, 1, 0)


class TestAttribution:
    def test_a_hint_belongs_to_the_mistake_it_was_about(self, student: Student, model: Model) -> None:
        helped = student.wrong(model, "2 * 3 = 5")
        alone = student.wrong(model, "4 * 5 = 21")
        student.hint(helped)
        student.fix(model, "2 * 3 = 6", helped)
        student.fix(model, "4 * 5 = 20", alone)
        glance = student.finish()["glance"]
        assert (glance["independent"], glance["assisted"], glance["hints"]) == (1, 1, 1)

    def test_asking_for_the_same_hint_again_isnt_another_hint(self, student: Student, model: Model) -> None:
        mistake = student.wrong(model, "2 * 3 = 5")
        student.hint(mistake)
        student.hint(mistake)
        assert student.finish()["glance"]["hints"] == 1

    def test_a_hint_that_beats_the_detection_to_the_server_still_counts(self, student: Student, model: Model) -> None:
        # Regression: events wait in the client's queue, and a quick tap on the duck doesn't.
        mistake = student.check(model, "2 * 3 = 5")["mistakes"][0]["id"]
        student.hint(mistake)
        student.event("mistake_detected", mistake)
        student.fix(model, "2 * 3 = 6", mistake)
        glance = student.finish()["glance"]
        assert (glance["independent"], glance["assisted"], glance["hints"]) == (0, 1, 1)

    def test_help_the_last_time_doesnt_count_against_this_time(self, student: Student, model: Model) -> None:
        mistake = student.wrong(model, "2 * 3 = 5")
        student.hint(mistake)
        student.fix(model, "2 * 3 = 6", mistake)
        student.finish()
        student.session = student.current()["session"]["id"]
        student.wrong(model, "2 * 3 = 5")
        student.fix(model, "2 * 3 = 6", mistake)
        assert student.finish()["glance"]["independent"] == 1

    def test_two_levels_are_two_hints(self, student: Student, model: Model) -> None:
        mistake = student.wrong(model, "2 * 3 = 5")
        student.hint(mistake, 1)
        student.hint(mistake, 2)
        assert student.finish()["glance"]["hints"] == 2

    def test_explanations_count_and_mean_help(self, student: Student, model: Model) -> None:
        mistake = student.wrong(model, "2 * 3 = 5")
        student.explain(mistake)
        student.fix(model, "2 * 3 = 6", mistake)
        glance = student.finish()["glance"]
        assert (glance["explanations"], glance["independent"], glance["assisted"]) == (1, 0, 1)


class TestRecapContent:
    def test_a_session_with_no_mistakes(self, student: Student, model: Model) -> None:
        student.check(model, "2 * 3 = 6")
        recap = student.finish()
        assert recap["glance"]["found"] == 0
        assert recap["recommendation"] is None
        assert recap["observations"] == []
        assert recap["note"] == "No particular trouble spot stood out this session. Keep practising and I'll help you notice patterns over time."

    def test_a_session_with_nothing_checked_doesnt_claim_it_went_well(self, student: Student) -> None:
        recap = student.finish()
        assert recap["opening"] == "We didn't check any working this time."
        assert recap["observations"] == []

    def test_one_slip_isnt_called_a_weakness(self, student: Student, model: Model) -> None:
        mistake = student.wrong(model, "2 * 3 = 5")
        student.fix(model, "2 * 3 = 6", mistake)
        recap = student.finish()
        assert recap["recommendation"]["reason"] == "It came up once this session, so this is just a suggestion."
        text = json.dumps(recap).lower()
        for claim in ("master", "weak", "struggl", "always", "never"):
            assert claim not in text

    def test_repeats_in_a_session_come_first(self, student: Student, model: Model) -> None:
        student.wrong(model, "d/dx(x^3) = 2x^2")
        student.wrong(model, "d/dx(x^4) = 3x^3")
        lone = student.wrong(model, "2 * 3 = 5")
        student.explain(lone)
        recap = student.finish()
        assert recap["recommendation"]["topic"] == "power_rule"
        assert recap["recommendation"]["reason"] == "That's 2 mistakes this session, so a little more practice could help."

    def test_repeats_across_sessions_are_noticed(self, student: Student, model: Model) -> None:
        student.wrong(model, "2 * 3 = 5")
        student.finish()
        student.session = student.current()["session"]["id"]
        student.wrong(model, "4 * 5 = 21")
        reason = student.finish()["recommendation"]["reason"]
        assert reason == "That's 1 mistake this session and 1 in recent sessions, so a little more practice could help."

    def test_an_unclassified_mistake_isnt_given_a_misconception(self, student: Student, model: Model) -> None:
        recap_topic = topic_of("algebra", "incorrect_simplification", "algebra")
        assert recap_topic == "algebra"
        assert topic_of("something_new", "x", "") == "unclassified"

    def test_a_topic_with_no_practice_recommends_nothing(self, student: Student, model: Model) -> None:
        student.wrong(model, "x^2 * x^3 = x^6")
        recap = student.finish()
        assert recap["glance"]["found"] == 1
        assert recap["recommendation"] is None
        assert recap["note"] == "Want to keep going? Try a quick practice question with me."

    def test_opens_quickly_with_no_model_call(self, student: Student, model: Model) -> None:
        for number in range(2, 8):
            student.wrong(model, f"{number} * 3 = {number * 3 + 1}")
        with respx.mock(assert_all_called=False) as router:
            refused = router.post(CHAT).mock(return_value=httpx.Response(500))
            started = time.perf_counter()
            recap = student.finish()
            elapsed = time.perf_counter() - started
        assert recap["glance"]["found"] == 6
        assert refused.call_count == 0
        assert elapsed < 1.0


class TestLifecycle:
    def test_finishing_twice_gives_the_same_recap(self, student: Student, model: Model) -> None:
        student.wrong(model, "2 * 3 = 5")
        first = student.finish()
        late = student.check(model, "4 * 5 = 21")["mistakes"][0]["id"]
        student.event("mistake_detected", late)
        assert student.finish() == first

    def test_a_late_event_goes_to_the_session_that_is_open(self, student: Student, model: Model) -> None:
        mistake = student.wrong(model, "2 * 3 = 5")
        old = student.session
        assert student.finish()["glance"]["corrected"] == 0
        student.check(model, "2 * 3 = 6", [mistake])
        student.event("correction_verified", mistake)
        student.session = student.current()["session"]["id"]
        assert student.session != old
        glance = student.finish()["glance"]
        # Fixed in this session, and it was found in the last one.
        assert (glance["found"], glance["corrected"], glance["earlierCorrected"]) == (0, 0, 1)

    def test_help_from_an_earlier_session_still_counts_as_help(self, student: Student, model: Model) -> None:
        mistake = student.wrong(model, "2 * 3 = 5")
        student.hint(mistake)
        student.finish()
        student.session = student.current()["session"]["id"]
        student.wrong(model, "4 * 5 = 21")
        student.check(model, "2 * 3 = 6", [mistake])
        student.event("correction_verified", mistake)
        assert student.finish()["glance"]["independent"] == 0

    def test_the_session_carries_on_after_a_refresh(self, student: Student) -> None:
        assert student.current()["session"]["id"] == student.session

    def test_a_session_left_too_long_is_wrapped_up_on_the_next_visit(self, student: Student, model: Model) -> None:
        student.wrong(model, "2 * 3 = 5")
        database = student.client.app.state.services.accounts.store.database
        long_ago = time.time() - SESSION_INACTIVE_S - 60
        database.execute("UPDATE study_sessions SET last_active_at = ?", (long_ago,))
        current = student.current()
        assert current["session"]["id"] != student.session
        assert current["wrappedUp"]["sessionId"] == student.session
        assert current["wrappedUp"]["finishReason"] == "inactive"
        assert current["wrappedUp"]["finishedAt"] == pytest.approx(long_ago)

    def test_a_session_never_used_isnt_wrapped_up(self, student: Student) -> None:
        database = student.client.app.state.services.accounts.store.database
        database.execute("UPDATE study_sessions SET last_active_at = ?", (time.time() - SESSION_INACTIVE_S - 60,))
        current = student.current()
        assert current["session"]["id"] == student.session
        assert current["wrappedUp"] is None

    def test_history_survives_a_restart_and_signing_in_again(self, tmp_path: Path, model: Model) -> None:
        settings = account_settings(tmp_path)
        student = Student(TestClient(create_app(build_services(settings))))
        student.wrong(model, "2 * 3 = 5")
        recap = student.finish()
        later = TestClient(create_app(build_services(settings)))
        login = later.post("/api/auth/local/login", json={"email": "a@example.com", "password": "correct horse"}).json()
        headers = {"authorization": f"Bearer {login['accessToken']}"}
        items = later.get("/api/sessions", headers=headers).json()["sessions"]
        assert [item["sessionId"] for item in items] == [recap["sessionId"]]
        assert items[0]["topicLabels"] == ["Multiplication"]
        detail = later.get(f"/api/sessions/{recap['sessionId']}/recap", headers=headers).json()
        assert detail["recap"] == recap

    def test_events_recorded_before_a_restart_still_count(self, tmp_path: Path, model: Model) -> None:
        settings = account_settings(tmp_path)
        student = Student(TestClient(create_app(build_services(settings))))
        student.wrong(model, "2 * 3 = 5")
        student.client = TestClient(create_app(build_services(settings)))
        assert student.finish()["glance"]["found"] == 1

    def test_histories_are_separate(self, accounts_client: TestClient, model: Model) -> None:
        one, other = Student(accounts_client), Student(accounts_client, "b@example.com")
        one.wrong(model, "2 * 3 = 5")
        recap = one.finish()
        assert accounts_client.get("/api/sessions", headers=other.headers).json()["sessions"] == []
        url = f"/api/sessions/{recap['sessionId']}/recap"
        assert accounts_client.get(url, headers=other.headers).status_code == 404

    def test_learning_history_can_be_deleted(self, student: Student, model: Model) -> None:
        student.wrong(model, "2 * 3 = 5")
        student.finish()
        assert student.client.delete("/api/account/learning", headers=student.headers).status_code == 200
        assert student.client.get("/api/sessions", headers=student.headers).json()["sessions"] == []
        rows = student.client.app.state.services.accounts.store.database.query("SELECT * FROM learning_events")
        assert rows == []


class TestRateLimits:
    def test_recording_what_happened_doesnt_use_up_a_students_checks(self, tmp_path: Path, model: Model) -> None:
        settings = account_settings(tmp_path, QUACK_RATE_LIMIT_PER_MINUTE=5)
        student = Student(TestClient(create_app(build_services(settings))))
        for _ in range(8):
            assert student.client.post("/api/learning/events", json={"events": []}, headers=student.headers).status_code == 200
        assert student.check(model, "2 * 3 = 6")["status"] == "no_error"

    def test_practice_checks_spend_credit_so_they_count(self, tmp_path: Path, model: Model) -> None:
        settings = account_settings(tmp_path, QUACK_RATE_LIMIT_PER_MINUTE=2)
        student = Student(TestClient(create_app(build_services(settings))))
        problem = student.client.post("/api/practice/sessions", json={"topic": "division"}, headers=student.headers).json()
        model.reply = [compact("1", continues_previous="no")]
        body = {"sessionId": "x", "snapshotVersion": 1, "image": png(), "inkLines": [{"id": "l", "inImage": True}]}
        url = f"/api/practice/problems/{problem['id']}/check"
        statuses = [student.client.post(url, json={**body, "snapshotVersion": n}, headers=student.headers).status_code for n in range(1, 5)]
        assert 429 in statuses


class TestOneSessionAtATime:
    def test_two_requests_at_once_get_the_same_session(self, tmp_path: Path) -> None:
        store = UserStore(Database(path=tmp_path / "quack.db"))

        def arrive(barrier: threading.Barrier, user_id: str) -> str:
            barrier.wait()
            return store.current_notebook_session(user_id)["id"]

        for _ in range(10):
            user_id = str(uuid.uuid4())
            barrier = threading.Barrier(8)
            with ThreadPoolExecutor(8) as pool:
                found = {future.result() for future in [pool.submit(arrive, barrier, user_id) for _ in range(8)]}
            assert len(found) == 1
