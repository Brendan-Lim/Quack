"""The iPad session of 2026-11-04, afternoon: Level 6, `Solve 5x − 7 = 23`, replayed through the backend.

The readings are the real model's, copied from that session's development trace (`GET /api/dev/trace/{session}`
on the server that ran it, port 8200, saved before anything was restarted). They're played back through a mocked
Meta API, by reasoning effort, so the first and second readings can differ as they did. This shows what Quack does
with those readings. It says nothing about how often the real model reads the same ink the same way.

What the trace showed:

- v24, `5x = 29` and `x = 29/5` on the page. Both readings read both lines right, but the page had been cut into
  five lines (the fraction's 29, its x = and bar, and its 5 apart), and the second reading put the same words one
  line lower. They disagreed on every line below the first, so nothing could be said: "can't read". The fix is in
  the web app's line grouping (`apps/web/lib/lines.ts`), and here it's checked that with the page in three lines,
  the same readings find the mistake straight away, on `5x = 29` and only there.
- v39, `x = 6` written beside a crossed-out 5. One reading was unsure, with `x = 36` as its other answer. Quack
  rightly didn't call it right, and said nothing about which line. With the old 5 rubbed out (v40), both read
  `x = 6` clearly, and the mistake was put right.
"""

from __future__ import annotations

import json
import random
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from quack.services.lesson_questions import TEMPLATES
from quack.services.lessons import question_columns
from tests.accounts_support import register
from tests.test_lessons import Lesson, store_of, unlock
from tests.test_line_memory import compact
from tests.test_pipeline import CHAT, completion, png

LEVEL = "solve_linear_equations"
EARLIER = ("meet_the_variables", "substitute_and_evaluate", "combine_like_terms", "simplify_expressions", "brackets_and_factors")


class ByEffort:
    """Each check's two readings, by reasoning effort: `minimal` is the first, `low` the second."""

    def __init__(self) -> None:
        self.first: list[list[dict[str, Any]]] = []
        self.second: list[list[dict[str, Any]]] = []
        self.calls: list[str] = []

    def read(self, first: list[dict[str, Any]], second: list[dict[str, Any]] | None = None) -> None:
        self.first, self.second = [first], [second if second is not None else first]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["response_format"]["json_schema"]["name"] != "CompactReading":
            return completion({"level": 1, "text": "Have another look at that step.", "references_step": "x"})
        effort = body["reasoning_effort"]
        self.calls.append(effort)
        queue = self.second if effort == "low" else self.first
        return completion({"lines": queue[0]})


def line(math: str, **overrides: Any) -> dict[str, Any]:
    return compact(math, operation="other", continues_previous="no") | overrides


def blank() -> dict[str, Any]:
    # A line both readings gave nothing for: in the trace, `''`, kind `prose`.
    return compact("", kind="prose")


@pytest.fixture
def model() -> Any:
    with respx.mock:
        answer = ByEffort()
        respx.post(CHAT).mock(side_effect=answer)
        yield answer


@pytest.fixture
def lesson(accounts_client: TestClient) -> Lesson:
    headers = register(accounts_client, "ipad-nov4@example.com")
    unlock(accounts_client, headers, *EARLIER)
    started = Lesson(accounts_client, LEVEL, headers=headers)
    question = TEMPLATES["linear_equations.two_step_minus"].build(random.Random(0), {"a": 5, "b": 7, "solution": 6})
    user = accounts_client.get("/api/profile", headers=headers).json()["userId"]
    columns = question_columns(question)
    assignments = ", ".join(f"{name} = ?" for name in columns)
    store_of(accounts_client).database.execute(
        f"UPDATE practice_problems SET {assignments} WHERE user_id = ? AND id = ?", (*columns.values(), user, started.question["id"])
    )
    return started


def check(lesson: Lesson, ink: list[tuple[str, bool]], trigger: str = "auto", known: list[str] | None = None,
          scopes: dict[str, list[str]] | None = None) -> dict[str, Any]:
    lesson.version += 1
    response = lesson.client.post(
        f"/api/practice/problems/{lesson.question['id']}/check",
        json={
            "sessionId": lesson.question["checkSessionId"],
            "snapshotVersion": lesson.version,
            "image": png(size=(40 + lesson.version, 20)),
            "acknowledgedMistakeIds": known or [],
            "inkLines": [{"id": line_id, "inImage": drawn} for line_id, drawn in ink],
            "studySessionId": lesson.state["sessionId"],
            "trigger": trigger,
        } | ({"mistakeLineIds": scopes} if scopes else {}),
        headers=lesson.headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def problem_status(lesson: Lesson) -> str:
    return lesson.row["status"]


# Line ids from the trace. `wk67w` is `5x = 30`: the `5x =` of `5x = 29` kept, its 29 rubbed out and 30 written, so
# it's the line the mistake's remaining ink is on, and the one the client names for it.
QUESTION, WRONG_STEP, FRACTION, REWRITTEN = "19mg73k1dv5hva", "l8ahs", "ese6q", "wk67w"


class TestTheQuestion:
    def test_it_is_the_question_from_the_video(self, lesson: Lesson) -> None:
        assert lesson.row["prompt_display"] == "Solve 5x − 7 = 23"
        assert lesson.row["expected_answer"] == "x = 6"


class TestFiveLinesForThree:
    """v24: the readings were right, and the page was cut into the wrong lines."""

    def test_the_readings_as_they_came_are_not_a_mistake_or_a_pass(self, lesson: Lesson, model: ByEffort) -> None:
        # As in the trace: five lines, the second reading one line lower than the first from line 1 on.
        model.read(
            [line("5x-7=23"), line("5x=29"), line("x=29/5"), blank(), blank()],
            [line("5x-7=23"), blank(), line("5x=29"), blank(), line("x=29/5")],
        )
        reply = check(lesson, [(QUESTION, True), (WRONG_STEP, True), (FRACTION, True), ("v7q1u", True), ("tbk76", True)])
        assert reply["mistakes"] == []
        assert reply["outcome"] == "in_progress"
        assert reply["allRead"] is False
        assert problem_status(lesson) != "correct"

    def test_in_three_lines_the_same_readings_find_the_first_wrong_step(self, lesson: Lesson, model: ByEffort) -> None:
        model.read([line("5x-7=23"), line("5x=29"), line("x=29/5")])
        reply = check(lesson, [(QUESTION, True), (WRONG_STEP, True), (FRACTION, True)])
        assert reply["status"] == "confirmed_error", reply
        # One mistake, on the first wrong step. x = 29/5 follows from 5x = 29, so it isn't a second one.
        assert [mistake["lineId"] for mistake in reply["mistakes"]] == [WRONG_STEP]
        mistake = reply["mistakes"][0]
        assert mistake["category"] == "equation_solving"
        assert mistake["currentStep"].replace(" ", "") == "5x=29"
        assert reply["intervention"]["shouldQuack"] is True
        assert reply["outcome"] == "incorrect"
        assert problem_status(lesson) != "correct"


class TestTheSteps:
    """Each step of the working, on its own and together, with both readings agreeing."""

    @pytest.mark.parametrize(
        ("maths", "wrong"),
        [
            (["5x-7=23", "5x=30"], None),
            (["5x-7=23", "5x=30", "x=6"], None),
            (["5x-7=23", "5x=29"], 1),
            (["5x-7=23", "5x=29", "x=29/5"], 1),
            (["5x = 29", "x = 29/5"], 0),
        ],
    )
    def test_where_the_mistake_is(self, lesson: Lesson, model: ByEffort, maths: list[str], wrong: int | None) -> None:
        model.read([line(math) for math in maths])
        ids = [f"line-{index}" for index in range(len(maths))]
        reply = check(lesson, [(line_id, True) for line_id in ids])
        found = [mistake["lineId"] for mistake in reply["mistakes"]]
        assert found == ([] if wrong is None else [ids[wrong]]), reply["mistakes"]

    def test_the_finished_answer_is_right(self, lesson: Lesson, model: ByEffort) -> None:
        model.read([line("5x-7=23"), line("5x=30"), line("x=6")])
        reply = check(lesson, [("a", True), ("b", True), ("c", True)])
        assert reply["outcome"] == "correct", reply
        assert problem_status(lesson) == "correct"

    def test_x_equals_6_alone_is_right(self, lesson: Lesson, model: ByEffort) -> None:
        model.read([line("x=6")])
        assert check(lesson, [("a", True)])["outcome"] == "correct"

    def test_x_equals_29_over_5_is_never_the_answer(self, lesson: Lesson, model: ByEffort) -> None:
        # Written on its own, it's a wrong answer to the question; it's never right.
        model.read([line("x=29/5")])
        reply = check(lesson, [("a", True)], trigger="manual")
        assert reply["outcome"] != "correct"
        assert problem_status(lesson) != "correct"

    def test_5x_equals_29_then_x_equals_29_over_5_is_not_two_mistakes(self, lesson: Lesson, model: ByEffort) -> None:
        model.read([line("5x-7=23"), line("5x=29"), line("x=29/5")])
        reply = check(lesson, [("a", True), ("b", True), ("c", True)])
        assert len(reply["mistakes"]) == 1


class TestTheCrossedOutFive:
    """v37 to v40: x = 5, then a 6 written beside the 5 with the 5 crossed out, then the 5 rubbed out."""

    def flagged(self, lesson: Lesson, model: ByEffort) -> str:
        model.read([line("5x-7=23"), line("5x=29")])
        reply = check(lesson, [(QUESTION, True), (WRONG_STEP, True)])
        assert reply["status"] == "confirmed_error"
        return reply["mistakes"][0]["id"]

    def test_an_unsure_reading_of_the_crossed_out_answer_is_never_right(self, lesson: Lesson, model: ByEffort) -> None:
        known = self.flagged(lesson, model)
        # v39: `x = 6` with one reading unsure, `x = 36` its other answer.
        model.read(
            [line("5x-7=23"), line("5x=30"), line("x=6", legibility="unsure", alternatives=["x=36"])],
            [line("5x-7=23"), line("5x=30"), line("x=6")],
        )
        reply = check(lesson, [(QUESTION, True), ("wk67w", True), ("401i1", True)], trigger="manual", known=[known],
                      scopes={known: [REWRITTEN]})
        assert reply["outcome"] != "correct"
        assert reply["allRead"] is False
        assert "401i1" not in reply["settledLineIds"]
        assert problem_status(lesson) != "correct"
        # The unsure line isn't accused of anything.
        assert all(mistake["lineId"] != "401i1" for mistake in reply["mistakes"])
        # The mistake was `5x = 29`, and its own line now reads `5x = 30`, clearly, in both readings, and is right.
        # That's the fix verified, so it's put right even with the answer below it unread (`_shows_a_fix`: a line
        # that can't be read somewhere else has no bearing). Being put right isn't being right: the question stays open.
        assert reply["resolvedMistakeIds"] == [known]

    def test_both_unsure_the_other_way_is_never_right_either(self, lesson: Lesson, model: ByEffort) -> None:
        known = self.flagged(lesson, model)
        model.read(
            [line("5x-7=23"), line("5x=30"), line("x=6")],
            [line("5x-7=23"), line("5x=30"), line("x=6", legibility="unsure", alternatives=["x=36"])],
        )
        reply = check(lesson, [(QUESTION, True), ("wk67w", True), ("401i1", True)], trigger="manual", known=[known],
                      scopes={known: [REWRITTEN]})
        assert reply["outcome"] != "correct"
        assert problem_status(lesson) != "correct"

    def test_with_the_old_5_rubbed_out_it_is_right_and_the_mistake_put_right(self, lesson: Lesson, model: ByEffort) -> None:
        known = self.flagged(lesson, model)
        model.read([line("5x-7=23"), line("5x=30"), line("x=6")])
        reply = check(lesson, [(QUESTION, True), ("wk67w", True), ("963v2", True)], known=[known], scopes={known: [REWRITTEN]})
        assert reply["outcome"] == "correct", reply
        assert reply["resolvedMistakeIds"] == [known]
        assert problem_status(lesson) == "correct"

    def test_a_wrong_answer_under_a_right_step_is_its_own_mistake(self, lesson: Lesson, model: ByEffort) -> None:
        # v37: x = 5 under 5x = 30 is wrong in its own right, not a consequence of the earlier slip.
        known = self.flagged(lesson, model)
        model.read([line("5x-7=23"), line("5x=30"), line("x=5")])
        reply = check(lesson, [(QUESTION, True), ("wk67w", True), ("pnlc4", True)], known=[known], scopes={known: [REWRITTEN]})
        assert [mistake["lineId"] for mistake in reply["mistakes"]] == ["pnlc4"]
        assert reply["outcome"] == "incorrect"


class TestTheHints:
    """On the iPad the hints were "Check that this step keeps the equation balanced" and "Whatever you do to one
    side…", which don't say where. The left side gained exactly 7, so the help can ask about that."""

    def hints(self, lesson: Lesson, model: ByEffort, previous: str, current: str) -> list[str]:
        model.read([line(previous), line(current)])
        reply = check(lesson, [("a", True), ("b", True)])
        assert reply["status"] == "confirmed_error", reply
        known = reply["mistakes"][0]["id"]
        body = {"sessionId": lesson.question["checkSessionId"], "mistakeId": known, "studySessionId": lesson.state["sessionId"]}
        return [
            lesson.client.post("/api/hint", json=body | {"level": level}, headers=lesson.headers).json()["text"]
            for level in (1, 2)
        ]

    def test_they_ask_about_adding_7_then_about_23_plus_7(self, lesson: Lesson, model: ByEffort) -> None:
        first, second = self.hints(lesson, model, "5x-7=23", "5x=29")
        assert first == "What happens when you add 7 to both sides?"
        assert second == "Check the right-hand side. What is 23 + 7?"
        # Neither says the answer, or the right step.
        for text in (first, second):
            assert "30" not in text and "6" not in text

    def test_taking_away_is_asked_about_as_taking_away(self, lesson: Lesson, model: ByEffort) -> None:
        first, second = self.hints(lesson, model, "5x+7=42", "5x=36")
        assert first == "What happens when you take away 7 from both sides?"
        assert second == "Check the right-hand side. What is 42 − 7?"

    @pytest.mark.parametrize(
        ("previous", "current"),
        [
            ("5x-7=23", "x-7=23"),  # Not a number added: 4x went from the left.
            ("5x-7=23", "5x=30"),  # Balanced: nothing to say about it.
            ("5x-7=23", "5x+1=31"),  # Balanced too, adding 8 each side.
            # The left changed by 1, but by a slip in expanding: asking about taking 1 away would mislead.
            ("2(x+1)=10", "2x+1=10"),
            # 7 went from the left and 6 from the right, but the 7 wasn't moved: 2 was added to it.
            ("5x-7+2=23", "5x-7=21"),
        ],
    )
    def test_only_a_number_added_to_one_side_gets_this_help(self, previous: str, current: str) -> None:
        from quack.services.hint_engine import _balance_help
        from quack.services.mistake_manager import TrackedMistake

        mistake = TrackedMistake("m_x", "equation_solving", "solution_set_changed", "equation", previous, current, "x = 6", "")
        found = [_balance_help(level, mistake) for level in (1, 2)]
        assert found == [None, None]
