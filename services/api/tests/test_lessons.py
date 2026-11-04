"""Lessons end to end: real API, real verifier, real classifier and policy. Only Meta's API is mocked.

These say what Quack does with the readings it's given. How well the real model reads real handwriting
is a separate question, which none of this answers.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient

from tests.accounts_support import register
from tests.test_line_memory import compact
from tests.test_pipeline import png
from tests.test_practice import CHAT, Model

COURSE = "algebra_foundations"
# What each level needs done first, so a test can start there. Moving on isn't mastery.
PREREQUISITES = {
    "combine_like_terms": ("meet_the_variables",),
    "simplify_expressions": ("meet_the_variables", "combine_like_terms"),
}


@pytest.fixture
def model() -> Any:
    with respx.mock:
        answer = Model()
        respx.post(CHAT).mock(side_effect=answer)
        yield answer


def store_of(client: TestClient) -> Any:
    return client.app.state.services.accounts.store  # type: ignore[attr-defined]


class Lesson:
    """A student working through one level, as the browser would drive it."""

    def __init__(self, client: TestClient, level: str = "combine_like_terms", headers: dict[str, str] | None = None,
                 email: str | None = None) -> None:
        self.client = client
        self.headers = headers or register(client, email or f"{uuid.uuid4()}@example.com")
        self.level = level
        if headers is None:
            unlock(client, self.headers, *PREREQUISITES.get(level, ()))
        self.version = 0
        self.state = self.start()

    def start(self) -> dict[str, Any]:
        response = self.client.post(f"/api/learn/courses/{COURSE}/levels/{self.level}/start", headers=self.headers)
        assert response.status_code == 200, response.text
        return response.json()

    @property
    def question(self) -> dict[str, Any]:
        return self.state["question"]

    @property
    def row(self) -> dict[str, Any]:
        user = self.client.get("/api/profile", headers=self.headers).json()["userId"]
        return store_of(self.client).problem(user, self.question["id"])

    def write(self, model: Model, *maths: str, acknowledged: list[str] | None = None, **overrides: Any) -> dict[str, Any]:
        """One check of the page, with these lines read clearly by both readings."""

        self.version += 1
        model.reply = [compact(math, **overrides) for math in maths]
        response = self.client.post(
            f"/api/practice/problems/{self.question['id']}/check",
            json={
                "sessionId": self.question["checkSessionId"],
                "snapshotVersion": self.version,
                "image": png(),
                "acknowledgedMistakeIds": acknowledged or [],
                "inkLines": [{"id": f"line-{self.version}-{i}", "inImage": True} for i in range(len(maths))],
                "studySessionId": self.state["sessionId"],
            },
            headers=self.headers,
        )
        assert response.status_code == 200, response.text
        return response.json()

    def shown(self, mistake_id: str, event: str = "mistake_detected", **extra: Any) -> None:
        """What the client sends once it has shown the student something."""

        body = {"events": [{"id": str(uuid.uuid4()), "eventType": event, "mistakeId": mistake_id,
                            "sessionId": self.state["sessionId"]} | extra]}
        response = self.client.post("/api/learning/events", json=body, headers=self.headers)
        assert response.status_code == 200 and response.json()["refused"] == [], response.text

    def next(self, after: str | None = None) -> dict[str, Any]:
        current = after if after is not None else self.question["id"]
        response = self.client.post(
            f"/api/learn/sessions/{self.state['sessionId']}/next", json={"currentProblemId": current}, headers=self.headers
        )
        assert response.status_code == 200, response.text
        self.state = response.json()
        return self.state

    def course(self) -> dict[str, Any]:
        return self.client.get(f"/api/learn/courses/{COURSE}", headers=self.headers).json()

    def level_of(self, level_id: str) -> dict[str, Any]:
        return next(level for level in self.course()["levels"] if level["id"] == level_id)

    def answer_right(self, model: Model) -> dict[str, Any]:
        reply = self.write(model, self.row["expected_answer"])
        assert reply["outcome"] == "correct", reply
        return reply


def unlock(client: TestClient, headers: dict[str, str], *levels: str) -> None:
    """Prerequisites marked as moved on, so a later level can be reached without playing the earlier ones."""

    user = client.get("/api/profile", headers=headers).json()["userId"]
    for level in levels:
        store_of(client).mark_level(user, COURSE, level, "moved_on_at")


class TestTheLoop:
    """Scenarios A to D: right alone, a mistake and its correction, and the question that follows."""

    def test_the_first_question_is_the_one_in_the_lesson(self, accounts_client: TestClient) -> None:
        lesson = Lesson(accounts_client)
        assert lesson.question["prompt"] == "Simplify 3x + 2x"
        assert lesson.question["tier"] == "learn"
        assert lesson.state["selection"]["kind"] == "start"

    def test_a_right_answer_counts_once_and_moves_up(self, accounts_client: TestClient, model: Model) -> None:
        lesson = Lesson(accounts_client)
        lesson.answer_right(model)
        # Checking the same right answer again doesn't add anything.
        assert lesson.write(model, "5x")["outcome"] == "correct"
        state = accounts_client.get(f"/api/learn/sessions/{lesson.state['sessionId']}", headers=lesson.headers).json()
        assert state["mastery"]["credits"] == 1.0
        assert state["mastery"]["independent"] == 1
        following = lesson.next()
        assert following["selection"]["kind"] == "step_up"
        assert following["question"]["tier"] == "apply"

    def test_a_dropped_letter_is_flagged_named_and_targeted(self, accounts_client: TestClient, model: Model) -> None:
        lesson = Lesson(accounts_client)
        wrong = lesson.write(model, "5")
        assert wrong["outcome"] == "incorrect"
        assert wrong["mistakes"][0]["errorType"] == "lost_variable"
        assert wrong["misconception"]["id"] == "variable_dropped"
        mistake = wrong["mistakes"][0]["id"]
        lesson.shown(mistake)

        fixed = lesson.write(model, "5x", acknowledged=[mistake])
        assert fixed["resolvedMistakeIds"] == [mistake]
        assert fixed["outcome"] == "correct"
        lesson.shown(mistake, "correction_verified")

        first = lesson.question
        following = lesson.next()
        assert following["selection"]["kind"] == "targeted"
        assert following["selection"]["target"]["id"] == "variable_dropped"
        assert following["selection"]["reason"].startswith("Nice correction!")
        assert following["question"]["prompt"] != first["prompt"]
        assert following["question"]["skill"] == "algebra.like_terms"
        # Practising the same skill, with a question that can show the same slip.
        user = accounts_client.get("/api/profile", headers=lesson.headers).json()["userId"]
        row = store_of(accounts_client).problem(user, following["question"]["id"])
        from quack.services.lesson_questions import TEMPLATES

        assert "variable_dropped" in TEMPLATES[row["template_id"]].targets
        assert row["selection_target"] == "variable_dropped"

    def test_a_corrected_mistake_is_half_credit_and_the_mistake_must_be_cleared(
        self, accounts_client: TestClient, model: Model
    ) -> None:
        lesson = Lesson(accounts_client)
        mistake = lesson.write(model, "5")["mistakes"][0]["id"]
        lesson.shown(mistake)
        lesson.write(model, "5x", acknowledged=[mistake])
        mastery = lesson.next()["mastery"]
        assert mastery["credits"] == 0.5
        assert mastery["corrected"] == 1
        assert [item["id"] for item in mastery["openMisconceptions"]] == ["variable_dropped"]

        # Scenario D: the targeted question right, alone, clears it.
        lesson.answer_right(model)
        after = lesson.next()["mastery"]
        assert after["credits"] == 1.5
        assert after["openMisconceptions"] == []

    def test_a_mistake_the_student_never_saw_isnt_evidence(self, accounts_client: TestClient, model: Model) -> None:
        lesson = Lesson(accounts_client)
        lesson.write(model, "5")
        # No mistake_detected: the reply was dropped as stale, so the student wasn't told. Then fixed.
        lesson.write(model, "5x")
        following = lesson.next()
        assert following["selection"]["kind"] == "step_up"
        assert following["mastery"]["independent"] == 1

    def test_a_misreading_isnt_held_against_anyone(self, accounts_client: TestClient, model: Model) -> None:
        lesson = Lesson(accounts_client)
        mistake = lesson.write(model, "5")["mistakes"][0]["id"]
        lesson.shown(mistake)
        # Read again, the same ink says 5x: two readings agree the mistake isn't there.
        assert lesson.write(model, "5x", acknowledged=[mistake])["resolvedMistakeIds"] == [mistake]
        lesson.shown(mistake, "mistake_invalidated", reason="misread")
        following = lesson.next()
        assert following["mastery"]["openMisconceptions"] == []
        assert following["mastery"]["independent"] == 1

    def test_a_misreading_the_server_never_saw_is_refused(self, accounts_client: TestClient, model: Model) -> None:
        lesson = Lesson(accounts_client)
        mistake = lesson.write(model, "5")["mistakes"][0]["id"]
        lesson.shown(mistake)
        body = {"events": [{"id": str(uuid.uuid4()), "eventType": "mistake_invalidated", "mistakeId": mistake,
                            "sessionId": lesson.state["sessionId"], "reason": "misread"}]}
        refused = accounts_client.post("/api/learning/events", json=body, headers=lesson.headers).json()["refused"]
        assert refused[0]["reason"] == "not_verified"
        assert [m["id"] for m in lesson.next()["mastery"]["openMisconceptions"]] == ["variable_dropped"]


class TestUncertainty:
    """Scenario F: an unsure reading changes nothing."""

    def test_an_unsure_reading_is_no_mistake_and_no_answer(self, accounts_client: TestClient, model: Model) -> None:
        lesson = Lesson(accounts_client)
        reply = lesson.write(model, "5", legibility="unsure", alternatives=["5x"])
        assert reply["mistakes"] == []
        assert reply["outcome"] == "in_progress"
        assert reply["misconception"] is None
        following = lesson.next()
        assert following["selection"]["kind"] == "retry"
        assert following["selection"]["target"] is None
        assert following["mastery"]["credits"] == 0
        assert following["mastery"]["openMisconceptions"] == []

    def test_moving_on_with_nothing_written_is_no_evidence(self, accounts_client: TestClient) -> None:
        lesson = Lesson(accounts_client)
        first = lesson.question["id"]
        following = lesson.next()
        assert following["selection"]["kind"] == "retry"
        user = accounts_client.get("/api/profile", headers=lesson.headers).json()["userId"]
        assert store_of(accounts_client).problem(user, first)["status"] == "skipped"
        assert following["mastery"]["asked"] == 2
        assert following["mastery"]["credits"] == 0


class TestRepeatedStruggle:
    """Scenario E: reinforcement is bounded, and the idea is gone over again on the way."""

    def test_the_same_slip_again_brings_the_idea_back_then_stops_targeting(
        self, accounts_client: TestClient, model: Model
    ) -> None:
        lesson = Lesson(accounts_client)
        kinds = []
        for _ in range(5):
            row = lesson.row
            # The total of the numbers in front, with the letter gone: what a dropped variable gives.
            from quack.services.math_parser import parse_expression, to_sympy
            from quack.services.misconceptions import _at_ones

            dropped = str(_at_ones(to_sympy(parse_expression(row["expected_answer"]))))
            reply = lesson.write(model, dropped)
            assert reply["misconception"]["id"] == "variable_dropped", reply
            lesson.shown(reply["mistakes"][0]["id"])
            kinds.append(lesson.next()["selection"]["kind"])
        assert kinds[0] == "simpler"
        assert "concept_review" in kinds
        assert kinds.count("concept_review") == 1
        targeted = [kind for kind in kinds if kind in ("simpler", "targeted", "concept_review", "resume_targeted")]
        assert len(targeted) == 3
        assert kinds[3] == "change_of_pace"

    def test_after_enough_questions_the_student_may_move_on(self, accounts_client: TestClient) -> None:
        lesson = Lesson(accounts_client)
        refused = accounts_client.post(
            f"/api/learn/courses/{COURSE}/levels/combine_like_terms/move-on", headers=lesson.headers
        )
        assert refused.status_code == 409
        for _ in range(8):
            lesson.next()
        assert lesson.state["mastery"]["canMoveOn"] is True
        course = accounts_client.post(
            f"/api/learn/courses/{COURSE}/levels/combine_like_terms/move-on", headers=lesson.headers
        ).json()
        statuses = {level["id"]: level["status"] for level in course["levels"]}
        assert statuses["combine_like_terms"] == "moved_on"
        assert statuses["simplify_expressions"] == "available"
        # Moving on isn't mastery.
        assert course["mastered"] == 0


class TestMastery:
    def master(self, lesson: Lesson, model: Model) -> dict[str, Any]:
        for _ in range(8):
            lesson.answer_right(model)
            state = lesson.next()
            if state["status"] == "mastered":
                return state
        raise AssertionError(lesson.state)

    def test_right_alone_up_to_a_challenge_masters_the_level(self, accounts_client: TestClient, model: Model) -> None:
        lesson = Lesson(accounts_client)
        state = self.master(lesson, model)
        mastery = state["mastery"]
        assert mastery["independentAtMet"] is True
        assert mastery["credits"] >= 3
        assert mastery["distinctTemplates"] >= 2
        assert state["nextLevel"]["id"] == "simplify_expressions"
        statuses = {level["id"]: level["status"] for level in lesson.course()["levels"]}
        assert statuses["simplify_expressions"] == "available"
        assert statuses["solve_linear_equations"] == "locked"

    def test_mastery_is_recorded_once_and_replay_adds_no_credit(self, accounts_client: TestClient, model: Model) -> None:
        lesson = Lesson(accounts_client)
        self.master(lesson, model)
        accounts_client.post(f"/api/learn/sessions/{lesson.state['sessionId']}/finish", headers=lesson.headers)
        user = accounts_client.get("/api/profile", headers=lesson.headers).json()["userId"]
        store = store_of(accounts_client)
        mastered_at = store.level_progress(user, COURSE)["combine_like_terms"]["mastered_at"]

        # Scenario I: replay. A new visit, a different first question, and nothing about mastery changes.
        replay = Lesson(accounts_client, headers=lesson.headers)
        assert replay.state["sessionId"] != lesson.state["sessionId"]
        assert replay.state["status"] == "mastered"
        assert replay.state["selection"]["kind"] == "start"
        replay.answer_right(model)
        replay.next()
        assert store.level_progress(user, COURSE)["combine_like_terms"]["mastered_at"] == mastered_at
        events = store.database.query(
            "SELECT * FROM learning_events WHERE user_id = ? AND event_type = 'level_mastered'", (user,)
        )
        assert len(events) == 1
        assert lesson.course()["mastered"] == 1

    def test_a_revealed_answer_earns_nothing(self, accounts_client: TestClient, model: Model) -> None:
        lesson = Lesson(accounts_client)
        accounts_client.post(f"/api/practice/problems/{lesson.question['id']}/reveal", headers=lesson.headers)
        assert lesson.write(model, "5x")["outcome"] == "in_progress"
        assert lesson.next()["mastery"]["credits"] == 0

    def test_a_nudge_makes_it_help_and_never_gives_the_answer(self, accounts_client: TestClient, model: Model) -> None:
        lesson = Lesson(accounts_client)
        nudges = [
            accounts_client.post(f"/api/learn/problems/{lesson.question['id']}/nudge", json={"level": n},
                                 headers=lesson.headers).json()
            for n in (1, 2)
        ]
        assert nudges[1]["last"] is True
        assert all("5x" not in nudge["text"] for nudge in nudges)
        lesson.answer_right(model)
        state = lesson.next()
        assert state["mastery"]["credits"] == 0.5
        assert state["mastery"]["assisted"] == 1
        assert state["selection"]["kind"] == "same_tier"


class TestPersistence:
    """Scenarios G and H: coming back finds the same place, and paths don't share progress."""

    def test_coming_back_resumes_the_question_and_the_level(self, accounts_client: TestClient, model: Model) -> None:
        lesson = Lesson(accounts_client)
        lesson.answer_right(model)
        lesson.next()
        again = Lesson(accounts_client, headers=lesson.headers)
        assert again.question["id"] == lesson.question["id"]
        assert again.state["sessionId"] == lesson.state["sessionId"]
        home = accounts_client.get("/api/learn", headers=lesson.headers).json()
        assert home["continueLearning"]["levelId"] == "combine_like_terms"
        assert home["continueLearning"]["levelTitle"] == "Combine Like Terms"

    def test_levels_keep_their_own_progress(self, accounts_client: TestClient, model: Model) -> None:
        first = Lesson(accounts_client, "meet_the_variables")
        first.answer_right(model)
        unlock(accounts_client, first.headers, "meet_the_variables")
        second = Lesson(accounts_client, "combine_like_terms", headers=first.headers)
        assert second.state["mastery"]["credits"] == 0
        assert second.state["sessionId"] != first.state["sessionId"]
        levels = {level["id"]: level for level in first.course()["levels"]}
        assert levels["meet_the_variables"]["mastery"]["credits"] == 1
        assert levels["combine_like_terms"]["mastery"]["credits"] == 0

    def test_an_open_misconception_is_where_the_next_visit_starts(self, accounts_client: TestClient, model: Model) -> None:
        lesson = Lesson(accounts_client)
        mistake = lesson.write(model, "5")["mistakes"][0]["id"]
        lesson.shown(mistake)
        accounts_client.post(f"/api/learn/sessions/{lesson.state['sessionId']}/finish", headers=lesson.headers)
        back = Lesson(accounts_client, headers=lesson.headers)
        assert back.state["selection"]["kind"] == "resume_targeted"
        assert back.state["selection"]["reason"].startswith("Last time, the letter went missing")


class TestSafety:
    def test_nothing_sent_to_the_browser_holds_the_answer(self, accounts_client: TestClient, model: Model) -> None:
        lesson = Lesson(accounts_client, "simplify_expressions")
        expected = lesson.row["expected_answer"]
        assert expected == "4x + 5"
        texts = [
            accounts_client.get("/api/learn", headers=lesson.headers).text,
            lesson.course().__repr__(),
            accounts_client.get(f"/api/learn/sessions/{lesson.state['sessionId']}", headers=lesson.headers).text,
        ]
        for text in texts:
            assert expected not in text
            assert "expected" not in text.lower()

    def test_another_account_cant_reach_any_of_it(self, accounts_client: TestClient, model: Model) -> None:
        mine = Lesson(accounts_client)
        theirs = register(accounts_client, "someone@example.com")
        session, problem = mine.state["sessionId"], mine.question["id"]
        for method, url in (
            ("get", f"/api/learn/sessions/{session}"),
            ("post", f"/api/learn/sessions/{session}/next"),
            ("post", f"/api/learn/sessions/{session}/finish"),
            ("post", f"/api/learn/problems/{problem}/nudge"),
            ("post", f"/api/learn/problems/{problem}/assisted"),
            ("post", f"/api/practice/problems/{problem}/check"),
            ("post", f"/api/practice/problems/{problem}/reveal"),
        ):
            kwargs: dict[str, Any] = {"headers": theirs}
            if method == "post":
                kwargs["json"] = {"level": 1} if "nudge" in url else {}
            response = getattr(accounts_client, method)(url, **kwargs)
            assert response.status_code in (404, 422), (url, response.status_code)
        assert accounts_client.get("/api/learn", headers=theirs).json()["continueLearning"] is None
        assert mine.row["assisted"] == 0

    def test_signed_out_gets_nothing(self, accounts_client: TestClient) -> None:
        assert accounts_client.get("/api/learn").status_code == 401
        assert accounts_client.post(f"/api/learn/courses/{COURSE}/levels/combine_like_terms/start").status_code == 401

    def test_a_locked_level_cant_be_started(self, accounts_client: TestClient) -> None:
        headers = register(accounts_client)
        assert accounts_client.post(
            f"/api/learn/courses/{COURSE}/levels/combine_like_terms/start", headers=headers
        ).status_code == 403
        response = accounts_client.post(f"/api/learn/courses/{COURSE}/levels/solve_linear_equations/start", headers=headers)
        assert response.status_code == 403
        assert accounts_client.post(f"/api/learn/courses/{COURSE}/levels/nope/start", headers=headers).status_code == 404
        # Every path in the catalog is open now, so a course that isn't in it stands in for one that isn't.
        assert accounts_client.get("/api/learn/courses/not_a_course", headers=headers).status_code == 404

    def test_asking_twice_for_the_next_question_gives_one(self, accounts_client: TestClient) -> None:
        lesson = Lesson(accounts_client)
        first = lesson.question["id"]
        one = lesson.next(first)
        two = lesson.next(first)
        assert one["question"]["id"] == two["question"]["id"]
        assert two["question"]["number"] == 2

    def test_at_the_same_moment_too(self, accounts_client: TestClient) -> None:
        lesson = Lesson(accounts_client)
        first = lesson.question["id"]
        results: list[dict[str, Any]] = []

        def ask() -> None:
            response = accounts_client.post(
                f"/api/learn/sessions/{lesson.state['sessionId']}/next", json={"currentProblemId": first},
                headers=lesson.headers,
            )
            results.append(response.json())

        threads = [threading.Thread(target=ask) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len({result["question"]["id"] for result in results}) == 1
        user = accounts_client.get("/api/profile", headers=lesson.headers).json()["userId"]
        assert len(store_of(accounts_client).problems_in(user, lesson.state["sessionId"])) == 2

    def test_a_forged_completion_isnt_possible(self, accounts_client: TestClient, model: Model) -> None:
        lesson = Lesson(accounts_client)
        # The only events a client can send are about mistakes the verifier reported. None says "right".
        body = {"events": [{"id": str(uuid.uuid4()), "eventType": "correction_verified", "mistakeId": "m_forged",
                            "sessionId": lesson.state["sessionId"]}]}
        refused = accounts_client.post("/api/learning/events", json=body, headers=lesson.headers).json()["refused"]
        assert refused[0]["reason"] == "unknown_mistake"
        assert lesson.next()["mastery"]["credits"] == 0


class TestMyMistakesAndRecap:
    def test_a_lesson_mistake_is_in_my_mistakes_with_where_it_was(self, accounts_client: TestClient, model: Model) -> None:
        lesson = Lesson(accounts_client)
        mistake = lesson.write(model, "5")["mistakes"][0]["id"]
        lesson.shown(mistake)
        lesson.write(model, "5x", acknowledged=[mistake])
        lesson.shown(mistake, "correction_verified")
        entries = accounts_client.get("/api/mistakes", headers=lesson.headers).json()["entries"]
        assert len(entries) == 1
        assert entries[0]["status"] == "corrected"
        assert entries[0]["lesson"] == {
            "courseId": COURSE, "levelId": "combine_like_terms", "levelTitle": "Combine Like Terms",
            "skillLabel": "Like terms", "misconception": "The letter went missing",
        }
        # Checking again doesn't make it two.
        lesson.write(model, "5x", acknowledged=[])
        assert len(accounts_client.get("/api/mistakes", headers=lesson.headers).json()["entries"]) == 1

    def test_the_recap_says_what_happened_and_no_more(self, accounts_client: TestClient, model: Model) -> None:
        lesson = Lesson(accounts_client)
        mistake = lesson.write(model, "5")["mistakes"][0]["id"]
        lesson.shown(mistake)
        lesson.write(model, "5x", acknowledged=[mistake])
        lesson.shown(mistake, "correction_verified")
        lesson.next()
        lesson.answer_right(model)
        recap = accounts_client.post(f"/api/learn/sessions/{lesson.state['sessionId']}/finish", headers=lesson.headers).json()
        assert recap["asked"] == 2
        assert recap["rightAlone"] == 1
        assert recap["corrected"] == 1
        assert recap["mistakes"] == 1
        assert recap["misconceptions"] == [{"label": "The letter went missing", "cleared": True}]
        assert recap["message"] == (
            "You got 1 right on your own, 1 put right after a slip. 1.5 of 3 points towards mastering Combine Like Terms."
        )
        # And the notebook's own recap is untouched by it.
        session = accounts_client.post("/api/sessions/current", headers=lesson.headers).json()["session"]["id"]
        notebook = accounts_client.post(f"/api/sessions/{session}/finish", headers=lesson.headers).json()
        assert notebook["glance"]["found"] == 0


@pytest.mark.parametrize(
    ("level", "prerequisites", "wrong", "misconception"),
    [
        ("brackets_and_factors", ("combine_like_terms", "simplify_expressions"), "5x + 3", "distribution_incomplete"),
        ("solve_linear_equations", ("combine_like_terms", "simplify_expressions", "brackets_and_factors"), "2x = 24",
         "incorrect_inverse_operation"),
        ("substitute_and_evaluate", (), "57", "substitution_as_digits"),
    ],
)
def test_each_core_skill_names_its_own_misconception(
    accounts_client: TestClient, model: Model, level: str, prerequisites: tuple[str, ...], wrong: str, misconception: str
) -> None:
    headers = register(accounts_client, f"{uuid.uuid4()}@example.com")
    unlock(accounts_client, headers, "meet_the_variables", *prerequisites)
    lesson = Lesson(accounts_client, level, headers=headers)
    reply = lesson.write(model, wrong)
    assert reply["outcome"] == "incorrect", reply
    assert reply["misconception"]["id"] == misconception
    lesson.shown(reply["mistakes"][0]["id"])
    following = lesson.next()
    assert following["selection"]["target"]["id"] == misconception
    assert following["selection"]["kind"] == "simpler"


def test_what_a_misconception_says_never_holds_a_worked_example() -> None:
    """It's shown beside the question, so any example in it could be that question's answer."""

    import re

    from quack.services.misconceptions import TAXONOMY

    for item in TAXONOMY.values():
        for text in (item.idea, item.happened, item.label):
            assert not re.search(r"\d|=", text), (item.id, text)


def test_the_final_challenge_mixes_skills_and_starts_at_a_challenge(accounts_client: TestClient, model: Model) -> None:
    headers = register(accounts_client, f"{uuid.uuid4()}@example.com")
    unlock(accounts_client, headers, "meet_the_variables", "substitute_and_evaluate", "combine_like_terms",
           "simplify_expressions", "brackets_and_factors", "solve_linear_equations")
    lesson = Lesson(accounts_client, "algebra_adventure", headers=headers)
    assert lesson.question["tier"] == "challenge"
    skills = {lesson.question["skill"]}
    for _ in range(3):
        lesson.answer_right(model)
        skills.add(lesson.next()["question"]["skill"])
    # Right every time, it moves through different skills rather than drilling one.
    assert len(skills) >= 3
    assert lesson.state["mastery"]["distinctSkillsNeeded"] == 3



class TestReviewFindings:
    """From the independent security review of the first version."""

    def test_deleting_a_mistake_from_my_mistakes_changes_no_credit(self, accounts_client: TestClient, model: Model) -> None:
        lesson = Lesson(accounts_client)
        mistake = lesson.write(model, "5")["mistakes"][0]["id"]
        lesson.shown(mistake)
        lesson.write(model, "5x", acknowledged=[mistake])
        lesson.shown(mistake, "correction_verified")
        before = lesson.next()["mastery"]
        assert accounts_client.delete(f"/api/mistakes/{mistake}", headers=lesson.headers).status_code == 200
        after = accounts_client.get(f"/api/learn/sessions/{lesson.state['sessionId']}", headers=lesson.headers).json()["mastery"]
        assert (after["credits"], after["independent"], after["corrected"]) == (before["credits"], 0, 1) == (0.5, 0, 1)
        assert [m["id"] for m in after["openMisconceptions"]] == ["variable_dropped"]
        # And it's gone from My Mistakes, as asked.
        assert accounts_client.get("/api/mistakes", headers=lesson.headers).json()["entries"] == []

    def test_my_mistakes_doesnt_answer_a_question_still_being_answered(
        self, accounts_client: TestClient, model: Model
    ) -> None:
        lesson = Lesson(accounts_client)
        mistake = lesson.write(model, "5")["mistakes"][0]["id"]
        lesson.shown(mistake)
        detail = accounts_client.get(f"/api/mistakes/{mistake}", headers=lesson.headers).json()
        assert detail["correctedStep"] is None
        assert "5x" not in str(detail)
        lesson.write(model, "5x", acknowledged=[mistake])
        detail = accounts_client.get(f"/api/mistakes/{mistake}", headers=lesson.headers).json()
        assert detail["correctedStep"] is not None

    def test_help_still_counts_after_the_mistake_is_deleted(self, accounts_client: TestClient, model: Model) -> None:
        lesson = Lesson(accounts_client)
        mistake = lesson.write(model, "5")["mistakes"][0]["id"]
        lesson.shown(mistake)
        hint = accounts_client.post(
            "/api/hint",
            json={"sessionId": lesson.question["checkSessionId"], "mistakeId": mistake, "level": 1,
                  "studySessionId": lesson.state["sessionId"]},
            headers=lesson.headers,
        )
        assert hint.status_code == 200
        lesson.write(model, "5x", acknowledged=[mistake])
        accounts_client.delete(f"/api/mistakes/{mistake}", headers=lesson.headers)
        assert lesson.next()["mastery"]["assisted"] == 1
