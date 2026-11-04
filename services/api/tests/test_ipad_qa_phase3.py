"""The Phase 3 iPad session of 2026-11-03, Level 2 (Substitute and Evaluate), replayed through the backend.

The readings are the real model's, copied from that session's development trace (`GET /api/dev/trace/
{session}` on the server that ran it, saved under `services/api/var/ipad_qa_2026-11-03/`). They're played
back through a mocked Meta API, so this shows what Quack does with them. It says nothing about how often
the real model reads the same ink the same way.

What went wrong, and why:

- `x = 3`, restating the question, was a mistake. Everything on a practice page is a claim Quack posed,
  so it was checked as the identity x ≡ 3.
- `5x + 4`, then `= 5(3) + 4`, was a mistake too: putting the value in was checked as algebra in x.
- On `x² + 6x` with x = −4, `(−4)² + 6(4)` was caught only because it isn't an identity in x either, so
  it was called a like-terms slip, and its explanation said the answer was x² + 6x.
- With `x = 3` rubbed out, the line below it kept the form it had been remembered in, `= 5(3) + 4`, with
  nothing above it, so the finished answer couldn't be accepted until it was written again.
"""

from __future__ import annotations

import json
import random
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient

from quack.services.lesson_questions import TEMPLATES
from quack.services.lessons import question_columns
from tests.test_lessons import Lesson, store_of
from tests.test_line_memory import compact
from tests.test_pipeline import png
from tests.test_practice import CHAT, Model

LEVEL = "substitute_and_evaluate"


def pose(lesson: Lesson, template: str, **params: Any) -> None:
    """Make the current question this one, as the policy might have chosen it."""

    question = TEMPLATES[template].build(random.Random(0), params)
    user = lesson.client.get("/api/profile", headers=lesson.headers).json()["userId"]
    columns = question_columns(question)
    assignments = ", ".join(f"{name} = ?" for name in columns)
    store_of(lesson.client).database.execute(
        f"UPDATE practice_problems SET {assignments} WHERE user_id = ? AND id = ?",
        (*columns.values(), user, lesson.question["id"]),
    )


def page(lesson: Lesson, model: Model, lines: list[tuple[str, bool, str | None]], **body: Any) -> dict[str, Any]:
    """One check of a page whose lines have fixed ids: (id, drawn, reading if drawn)."""

    lesson.version += 1
    model.reply = [compact(math) for _, drawn, math in lines if drawn and math is not None]
    response = lesson.client.post(
        f"/api/practice/problems/{lesson.question['id']}/check",
        json={
            "sessionId": lesson.question["checkSessionId"],
            "snapshotVersion": lesson.version,
            "image": png(),
            "acknowledgedMistakeIds": [],
            "inkLines": [{"id": line_id, "inImage": drawn} for line_id, drawn, _ in lines],
            "studySessionId": lesson.state["sessionId"],
        } | body,
        headers=lesson.headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def on_line(reply: dict[str, Any], lesson: Lesson, position: int) -> list[dict[str, Any]]:
    return [mistake for mistake in reply["mistakes"] if mistake["lineId"] == f"line-{lesson.version}-{position}"]


@pytest.fixture
def model() -> Any:
    with respx.mock:
        answer = Model()
        respx.post(CHAT).mock(side_effect=answer)
        yield answer


@pytest.fixture
def lesson(accounts_client: TestClient) -> Lesson:
    from tests.accounts_support import register
    from tests.test_lessons import unlock

    headers = register(accounts_client, "phase3-ipad@example.com")
    unlock(accounts_client, headers, "meet_the_variables")
    return Lesson(accounts_client, LEVEL, headers=headers)


class TestQuestionOne:
    """If x = 3, work out 5x + 4. The level's first question, as on the iPad."""

    def test_it_is_the_question_from_the_video(self, lesson: Lesson) -> None:
        assert lesson.question["prompt"] == "If x = 3, work out 5x + 4"

    @pytest.mark.parametrize(
        "maths",
        [
            # As written on the iPad, before anything was rubbed out (v30, v36).
            ["x=3", "= 5x+4", "= 5(3)+4", "= 15+4", "=19"],
            ["x = 3", "5(3)+4", "= 15+4", "= 19"],
            # The given left out.
            ["5(3)+4", "= 15+4", "= 19"],
            ["5(3) + 4 = 15 + 4", "= 19"],
            ["5x + 4", "= 5(3) + 4", "= 19"],
            ["5 * 3 + 4 = 19"],
            ["19"],
            ["= 19"],
            ["x = 3", "19"],
        ],
    )
    def test_right_working_is_right_with_or_without_the_given(self, lesson: Lesson, model: Model, maths: list[str]) -> None:
        reply = lesson.write(model, *maths)
        assert reply["mistakes"] == [], reply["mistakes"]
        assert reply["outcome"] == "correct", reply

    @pytest.mark.parametrize("maths", [["x = 3"], ["x = 3", "5(3)+4"], ["x = 3", "5x+4"], ["x"], ["5(3)+4", "="]])
    def test_unfinished_working_is_neither_wrong_nor_done(self, lesson: Lesson, model: Model, maths: list[str]) -> None:
        reply = lesson.write(model, *maths)
        assert reply["mistakes"] == []
        assert reply["outcome"] == "in_progress"

    def test_a_wrong_answer_is_flagged_where_it_goes_wrong(self, lesson: Lesson, model: Model) -> None:
        reply = lesson.write(model, "x = 3", "5(3)+4", "= 15+4", "= 18")
        assert reply["outcome"] == "incorrect"
        assert [m["lineId"] for m in reply["mistakes"]] == [f"line-{lesson.version}-3"]
        assert reply["mistakes"][0]["category"] == "arithmetic"

    def test_a_different_value_for_x_is_flagged_once_on_that_line(self, lesson: Lesson, model: Model) -> None:
        reply = lesson.write(model, "x = 4", "5(4)+4", "= 20+4", "= 24")
        assert reply["outcome"] == "incorrect"
        assert len(reply["mistakes"]) == 1, reply["mistakes"]
        flagged = reply["mistakes"][0]
        assert flagged["lineId"] == f"line-{lesson.version}-0"
        assert (flagged["category"], flagged["errorType"]) == ("substitution", "given_value_changed")

    def test_a_wrong_value_put_in_is_a_substitution_mistake(self, lesson: Lesson, model: Model) -> None:
        reply = lesson.write(model, "5x + 4", "= 5(4) + 4", "= 24")
        assert [(m["lineId"], m["category"], m["errorType"]) for m in reply["mistakes"]] == [
            (f"line-{lesson.version}-1", "substitution", "wrong_value_substituted")
        ]

    def test_a_correction_is_recognised_and_finishes_it(self, lesson: Lesson, model: Model) -> None:
        wrong = lesson.write(model, "5(3)+4", "= 15+4", "= 18")
        known = wrong["mistakes"][0]["id"]
        lesson.shown(known)
        fixed = lesson.write(model, "5(3)+4", "= 15+4", "= 19", acknowledged=[known])
        assert known in fixed["resolvedMistakeIds"]
        assert fixed["outcome"] == "correct"

    def test_right_alone_is_full_credit(self, lesson: Lesson, model: Model) -> None:
        lesson.write(model, "x = 3", "5(3)+4", "= 15+4", "= 19")
        mastery = lesson.client.get(f"/api/learn/sessions/{lesson.state['sessionId']}", headers=lesson.headers).json()
        assert mastery["mastery"]["credits"] == 1

    def test_rubbing_out_the_given_leaves_working_that_can_still_finish(self, lesson: Lesson, model: Model) -> None:
        """v57 to v86: `x = 3` erased, and the lines under it already agreed and remembered."""

        lines = [("given", True, "x = 3"), ("sub", True, "= 5(3)+4"), ("sum", True, "= 15+4"), ("answer", True, "= 19")]
        first = page(lesson, model, lines)
        assert first["mistakes"] == [] and first["outcome"] == "correct"

    def test_rubbing_out_the_given_after_it_was_remembered(self, lesson: Lesson, model: Model) -> None:
        lines = [("given", True, "x = 3"), ("sub", True, "= 5(3)+4"), ("sum", True, "= 15+4")]
        first = page(lesson, model, lines)
        assert first["mistakes"] == [] and first["outcome"] == "in_progress"
        # The given is gone, the two lines under it are remembered, and the answer is new.
        second = page(lesson, model, [("sub", False, None), ("sum", False, None), ("answer", True, "= 19")])
        assert second["mistakes"] == []
        assert second["outcome"] == "correct", second


class TestQuestionFour:
    """If x = −4, work out x² + 6x. The challenge question, as on the iPad."""

    @pytest.fixture(autouse=True)
    def _pose(self, lesson: Lesson) -> None:
        pose(lesson, "substitution.square_negative", value=4, a=6)

    def test_the_given_is_never_the_mistake_and_the_6_4_line_is(self, lesson: Lesson, model: Model) -> None:
        reply = lesson.write(model, "x = -4", "x^2+6x", "=(-4)^2+6(4)", "=16+24", "=40")
        assert reply["outcome"] == "incorrect"
        assert [m["lineId"] for m in reply["mistakes"]] == [f"line-{lesson.version}-2"], reply["mistakes"]
        flagged = reply["mistakes"][0]
        assert (flagged["category"], flagged["errorType"]) == ("substitution", "sign_lost_in_substitution")
        assert reply["misconception"]["id"] == "sign_error"

    def test_as_read_at_v17_v44_with_the_given_and_working_continued(self, lesson: Lesson, model: Model) -> None:
        reply = lesson.write(model, "x = -4", "= x^2+6x", "=(-4)^2+6(4)", "=16+24", "=40")
        assert [m["lineId"] for m in reply["mistakes"]] == [f"line-{lesson.version}-2"], reply["mistakes"]

    def test_as_read_at_v45_with_the_given_rubbed_out(self, lesson: Lesson, model: Model) -> None:
        reply = lesson.write(model, "= x^2+6x", "=(-4)^2+6(4)", "=16+24", "=40")
        assert [(m["lineId"], m["errorType"]) for m in reply["mistakes"]] == [
            (f"line-{lesson.version}-1", "sign_lost_in_substitution")
        ]

    def test_the_given_alone_is_fine(self, lesson: Lesson, model: Model) -> None:
        reply = lesson.write(model, "x = -4")
        assert reply["mistakes"] == [] and reply["outcome"] == "in_progress"

    @pytest.mark.parametrize(
        "maths",
        [
            ["x = -4", "x^2+6x", "=(-4)^2+6(-4)", "=16-24", "=-8"],
            ["(-4)^2 + 6(-4)", "= 16 - 24", "= -8"],
            ["x^2 + 6x = 16 - 24", "= -8"],
            ["-8"],
        ],
    )
    def test_right_working_is_right(self, lesson: Lesson, model: Model, maths: list[str]) -> None:
        reply = lesson.write(model, *maths)
        assert reply["mistakes"] == [], reply["mistakes"]
        assert reply["outcome"] == "correct"

    def test_a_slip_after_a_right_substitution_is_arithmetic(self, lesson: Lesson, model: Model) -> None:
        reply = lesson.write(model, "x^2+6x", "=(-4)^2+6(-4)", "=16-24", "=8")
        assert [(m["lineId"], m["category"]) for m in reply["mistakes"]] == [(f"line-{lesson.version}-3", "arithmetic")]

    def test_the_sign_put_right_resolves_it_and_finishes(self, lesson: Lesson, model: Model) -> None:
        wrong = lesson.write(model, "x^2+6x", "=(-4)^2+6(4)", "=16+24", "=40")
        known = wrong["mistakes"][0]["id"]
        lesson.shown(known)
        fixed = lesson.write(model, "x^2+6x", "=(-4)^2+6(-4)", "=16-24", "=-8", acknowledged=[known])
        assert known in fixed["resolvedMistakeIds"]
        assert fixed["mistakes"] == [] and fixed["outcome"] == "correct"

    def help_for(self, lesson: Lesson, model: Model) -> tuple[str, list[dict[str, Any]]]:
        reply = lesson.write(model, "x = -4", "x^2+6x", "=(-4)^2+6(4)", "=16+24", "=40")
        mistake = reply["mistakes"][0]["id"]
        lesson.shown(mistake)
        return mistake, []

    def test_hints_and_the_explanation_are_about_the_sign_put_in(self, lesson: Lesson, model: Model) -> None:
        mistake, _ = self.help_for(lesson, model)
        calls_before = len(model.calls)
        session = lesson.question["checkSessionId"]
        texts = []
        for level in (1, 2):
            reply = lesson.client.post(
                "/api/hint", json={"sessionId": session, "mistakeId": mistake, "level": level}, headers=lesson.headers
            ).json()
            assert reply["source"] == "template"
            texts.append(reply["text"])
        explained = lesson.client.post(
            "/api/explain", json={"sessionId": session, "mistakeId": mistake}, headers=lesson.headers
        ).json()
        assert explained["source"] == "template"
        # No model was asked: the verifier established all of it.
        assert len(model.calls) == calls_before
        first, second, explanation = texts[0], texts[1], explained["text"]
        for text in (first, second, explanation):
            lowered = text.lower()
            assert "coefficient" not in lowered and "like terms" not in lowered and "matching terms" not in lowered, text
        assert "-4" not in first and "−4" not in first, "the first hint only points"
        assert "6x" in second and ("-4" in second or "−4" in second), second
        assert "6(-4)" in explanation.replace("−", "-"), explanation
        assert "x^2 + 6x." not in explanation and "result is x" not in explanation
        # Neither hint gives the value away.
        assert "-8" not in first + second and "−8" not in first + second


class TestQuestionFive:
    """If x = −6, work out 6x + 8, the simpler question after the sign slip."""

    @pytest.fixture(autouse=True)
    def _pose(self, lesson: Lesson) -> None:
        pose(lesson, "substitution.negative", a=6, value=6, b=8)

    def test_the_wrong_sum_is_arithmetic_with_a_hint_about_that_sum(self, lesson: Lesson, model: Model) -> None:
        reply = lesson.write(model, "6(-6)+8", "=-34")
        assert [(m["lineId"], m["category"]) for m in reply["mistakes"]] == [(f"line-{lesson.version}-1", "arithmetic")]
        mistake = reply["mistakes"][0]["id"]
        lesson.shown(mistake)
        second = lesson.client.post(
            "/api/hint", json={"sessionId": lesson.question["checkSessionId"], "mistakeId": mistake, "level": 2},
            headers=lesson.headers,
        ).json()["text"]
        assert "6 × (−6)" in second or "6 × (-6)" in second, second
        assert "-28" not in second and "−28" not in second

    def test_the_right_answer_is_right(self, lesson: Lesson, model: Model) -> None:
        reply = lesson.write(model, "6(-6)+8", "=-28")
        assert reply["outcome"] == "correct" and reply["mistakes"] == []

    def test_a_lone_letter_while_writing_the_given_is_not_a_mistake(self, lesson: Lesson, model: Model) -> None:
        """v2: the start of `x = −6` read as `x`, then flagged as the answer."""

        reply = lesson.write(model, "x")
        assert reply["mistakes"] == []


class TestQuestionSix:
    def test_seven_fours_take_three(self, lesson: Lesson, model: Model) -> None:
        pose(lesson, "substitution.subtract", a=7, value=4, b=3)
        reply = lesson.write(model, "7(4)-3", "=25")
        assert reply["outcome"] == "correct" and reply["mistakes"] == []


class TestTwoValues:
    def test_both_givens_restated_and_used(self, lesson: Lesson, model: Model) -> None:
        pose(lesson, "substitution.two_values", a=3, p=2, q=5)
        assert lesson.row["prompt_display"] == "If x = 2 and y = −5, work out 3x − y"
        reply = lesson.write(model, "x = 2", "y = -5", "3x - y", "= 3(2) - (-5)", "= 6 + 5", "= 11")
        assert reply["mistakes"] == [] and reply["outcome"] == "correct"

    def test_the_sign_of_y_lost(self, lesson: Lesson, model: Model) -> None:
        pose(lesson, "substitution.two_values", a=3, p=2, q=5)
        reply = lesson.write(model, "3x - y", "= 3(2) - 5", "= 1")
        assert [(m["lineId"], m["errorType"]) for m in reply["mistakes"]] == [
            (f"line-{lesson.version}-1", "sign_lost_in_substitution")
        ]


def test_the_question_context_is_stored_with_the_question(lesson: Lesson) -> None:
    context = json.loads(lesson.row["context_json"])
    assert context == {"values": [["x", "3"]], "expression": "5x + 4"}


class TestExplainIsBounded:
    """On the iPad, Explain showed "Thinking..." for 75 s: 30 s timeouts tried again, twice (ledger 20:19 to 20:20).

    `2(x + 3) = 2x + 3` has no explanation the verifier can word in full, so it goes to the model.
    """

    @pytest.fixture
    def client(self, settings: Any) -> TestClient:
        from quack.main import build_services, create_app

        return TestClient(create_app(build_services(settings)))

    def test_a_timeout_isnt_tried_again_and_a_prepared_explanation_is_given(self, client: TestClient) -> None:
        import httpx
        import respx

        from tests.test_hints import report_mistake
        from tests.test_pipeline import CHAT, WRONG, reading

        with respx.mock:
            route = respx.post(CHAT).mock(return_value=reading(*WRONG))
            mistake = report_mistake(client)
            before = route.call_count
            route.mock(side_effect=httpx.ReadTimeout("slow"))
            reply = client.post("/api/explain", json={"sessionId": "s1", "mistakeId": mistake}).json()
        assert route.call_count == before + 1, "a timed-out explanation isn't paid for again"
        assert reply["source"] == "template" and "2x + 6" in reply["text"]
        assert reply["modelIssue"]["category"] == "timeout"

    def test_explaining_again_reuses_the_explanation(self, client: TestClient) -> None:
        import respx

        from tests.test_hints import help_reply, report_mistake
        from tests.test_pipeline import CHAT, WRONG, reading

        with respx.mock:
            route = respx.post(CHAT).mock(return_value=reading(*WRONG))
            mistake = report_mistake(client)
            route.mock(return_value=help_reply("Both terms inside get multiplied by 2, so 3 becomes 6.", 3))
            first = client.post("/api/explain", json={"sessionId": "s1", "mistakeId": mistake}).json()
            calls = route.call_count
            again = client.post("/api/explain", json={"sessionId": "s1", "mistakeId": mistake}).json()
        assert route.call_count == calls
        assert again["text"] == first["text"]

    def test_the_route_has_no_retry_on_timeout(self) -> None:
        from quack.llm.router import DEFAULT_ROUTES, Purpose

        route = DEFAULT_ROUTES[Purpose.EXPLAIN]
        assert route.retry_timeouts is False and route.timeout_s <= 20


def test_a_question_asked_before_its_context_was_stored_still_has_it(lesson: Lesson, model: Model) -> None:
    """The iPad's pending question was made by the old server. Its values come from its wording."""

    store_of(lesson.client).database.execute(
        "UPDATE practice_problems SET context_json = NULL WHERE id = ?", (lesson.question["id"],)
    )
    reply = lesson.write(model, "x = 3", "5(3)+4", "= 15+4", "= 19")
    assert reply["mistakes"] == [] and reply["outcome"] == "correct"


def state_of(lesson: Lesson) -> dict[str, Any]:
    return lesson.client.get(f"/api/learn/sessions/{lesson.state['sessionId']}", headers=lesson.headers).json()


def test_points_without_a_challenge_lead_to_a_challenge_and_passing_it_masters(lesson: Lesson, model: Model) -> None:
    """The video's path through Level 2, with Q1's false alert gone: 4 of 3 points, then a challenge."""

    lesson.answer_right(model)
    assert lesson.next()["question"]["tier"] == "apply"
    lesson.answer_right(model)
    assert lesson.next()["question"]["tier"] == "challenge"
    # Q4: x = -4 into x^2 + 6x, with the sign lost in 6x and never put right.
    pose(lesson, "substitution.square_negative", value=4, a=6)
    slip = lesson.write(model, "x = -4", "x^2+6x", "=(-4)^2+6(4)", "=16+24", "=40")
    lesson.shown(slip["mistakes"][0]["id"])
    simpler = lesson.next()
    assert (simpler["selection"]["kind"], simpler["selection"]["target"]["id"]) == ("simpler", "sign_error")
    assert simpler["question"]["tier"] == "apply"
    # Q5: a wrong value, shown, then put right.
    wrong = str(int(lesson.row["expected_answer"]) + 100)
    flagged = lesson.write(model, wrong)
    lesson.shown(flagged["mistakes"][0]["id"])
    assert lesson.write(model, lesson.row["expected_answer"], acknowledged=[flagged["mistakes"][0]["id"]])["outcome"] == "correct"
    # Q6: right alone.
    lesson.next()
    lesson.answer_right(model)
    standing = state_of(lesson)["mastery"]
    assert standing["credits"] >= standing["creditsNeeded"]
    assert standing["independentAtMet"] is False and standing["mastered"] is False
    # Only the challenge is left, so that's what comes next, and not more of the same.
    following = lesson.next()
    assert following["question"]["tier"] == "challenge", following["selection"]
    lesson.answer_right(model)
    finished = state_of(lesson)
    assert finished["status"] == "mastered"


def test_a_failed_challenge_has_a_way_back_to_one(lesson: Lesson, model: Model) -> None:
    lesson.answer_right(model)
    lesson.next()
    lesson.answer_right(model)
    lesson.next()
    pose(lesson, "substitution.square_negative", value=4, a=6)
    slip = lesson.write(model, "x^2+6x", "=(-4)^2+6(4)", "=40")
    lesson.shown(slip["mistakes"][0]["id"])
    assert lesson.next()["question"]["tier"] == "apply"
    lesson.answer_right(model)
    assert lesson.next()["question"]["tier"] == "challenge"


def verified(values: tuple[tuple[str, str], ...], *lines: str) -> list[tuple[int, str, str | None]]:
    """The verifier alone, on a practice page whose question is in front of the first line."""

    from quack.services.math_verifier import LineInput, verify_lines

    report = verify_lines(
        [LineInput(i, math, posed=True, values=values, continues="no" if i == 0 else "yes") for i, math in enumerate(lines)]
    )
    return [(step.line, step.status, step.error_type or step.reason) for step in report.steps]


class TestValuesInTheVerifier:
    @pytest.mark.parametrize(
        ("value", "working"),
        [
            ("0", ["5x + 4 = 5(0) + 4", "= 4"]),
            ("-2", ["5x + 4 = 5(-2) + 4", "= -10 + 4", "= -6"]),
            ("1/2", ["4x + 3 = 4(1/2) + 3", "= 2 + 3", "= 5"]),
            ("-1/2", ["4x + 3 = 4(-1/2) + 3", "= 1"]),
        ],
    )
    def test_zero_negative_and_fractional_values(self, value: str, working: list[str]) -> None:
        steps = verified((("x", value),), f"x = {value}", *working)
        assert all(status == "valid" for _, status, _ in steps), steps

    def test_a_value_left_out_of_one_term_is_found_in_that_term(self) -> None:
        steps = verified((("x", "-1/2"),), "4x + 3 = 4(1/2) + 3")
        assert steps == [(0, "invalid", "sign_lost_in_substitution")]

    def test_zero_has_no_sign_to_lose(self) -> None:
        steps = verified((("x", "0"),), "5x + 4 = 5(1) + 4")
        assert steps == [(0, "invalid", "wrong_value_substituted")]

    def test_one_letter_at_a_time_is_fine(self) -> None:
        assert verified((("x", "-4"),), "x^2 + 6x = (-4)^2 + 6x", "= 16 - 24") == [(0, "valid", None), (1, "valid", None)]
        assert verified((("x", "-4"),), "x^2 + 6x = x^2 + 6(4)") == [(0, "invalid", "sign_lost_in_substitution")]
        # (4)^2 is (-4)^2, so nothing about the value is wrong. Until round 4 Quack said nothing, which let
        # (3)^2 for x = -3 master a level on the iPad. Now the substitution is the slip, and is said to be,
        # with the value called right. See test_ipad_qa_round4.py.
        assert verified((("x", "-4"),), "x^2 + 6x = (4)^2 + 6x") == [(0, "invalid", "sign_lost_value_unchanged")]

    def test_the_given_restated_anywhere_isnt_part_of_the_working(self) -> None:
        steps = verified((("x", "3"),), "5x + 4 = 5(3) + 4", "x = 3", "= 15 + 4", "= 19")
        assert all(status == "valid" for _, status, _ in steps), steps

    def test_half_written_letters_abstain(self) -> None:
        assert verified((("x", "3"),), "5x + 4 = 5x") == [(0, "uncertain", "still_writing")]
        assert verified((("x", "3"),), "5x + 4 = 5x + 7x") == [(0, "uncertain", "letters_left_in")]
        assert verified((("x", "3"),), "5x + 4 = x") == [(0, "uncertain", "letter_alone")]

    def test_a_given_read_another_way_is_no_accusation(self) -> None:
        from quack.services.math_verifier import LineInput, verify_lines

        line = LineInput(0, "x = 4", posed=True, values=(("x", "-4"),), alternatives=("x = -4",))
        assert verify_lines([line]).status != "invalid"

    def test_without_values_nothing_changes(self) -> None:
        """The notebook and other practice pass no values, so `x = 3` is what it always was there."""

        from quack.services.math_verifier import LineInput, verify_lines

        plain = verify_lines([LineInput(0, "5x + 4"), LineInput(1, "= 5(3) + 4", continues="yes")])
        assert plain.status == "uncertain" and plain.steps[0].reason == "variables_changed"


def test_the_given_written_last_isnt_taken_for_the_answer(lesson: Lesson, model: Model) -> None:
    reply = lesson.write(model, "5(3)+4", "= 19", "x = 3")
    assert reply["mistakes"] == [] and reply["outcome"] == "correct"



class TestFromTheIndependentReview:
    """Defects found by an independent review of the first fix, each reproduced before it was fixed."""

    @pytest.mark.parametrize(
        ("values", "lines"),
        [
            ((("x", "3"),), ["5x + 4 = 5(3)"]),
            ((("x", "3"),), ["x = 3", "5x + 4 = 5x + 4", "= 5(3)"]),
            ((("x", "-4"),), ["x^2 + 6x = (-4)^2"]),
            ((("x", "-3"),), ["2x^2 - x = 2(-3)^2"]),
            ((("x", "4"), ("y", "-5")), ["3x - y = 3(4)"]),
        ],
    )
    def test_a_pause_part_way_through_putting_values_in_is_no_mistake(
        self, values: tuple[tuple[str, str], ...], lines: list[str]
    ) -> None:
        assert all(status != "invalid" for _, status, _ in verified(values, *lines))

    @pytest.mark.parametrize("given", ["x = 4 y = -5", "x = 4, y = -5", "x = 4 and y = -5", "x=4 y=-5"])
    def test_both_givens_on_one_line_are_the_question_restated(self, given: str) -> None:
        values = (("x", "4"), ("y", "-5"))
        steps = verified(values, given, "3x - y = 3(4) - (-5)", "= 12 + 5", "= 17")
        assert all(status == "valid" for _, status, _ in steps), steps

    def test_both_givens_on_one_line_finish_through_the_api(self, lesson: Lesson, model: Model) -> None:
        pose(lesson, "substitution.two_values", a=3, p=2, q=5)
        reply = lesson.write(model, "x = 2 and y = -5", "3(2) - (-5)", "= 11")
        assert reply["mistakes"] == [] and reply["outcome"] == "correct", reply

    def test_a_wrong_given_seen_is_still_one_slip(self) -> None:
        from quack.services.math_verifier import LineInput, verify_lines

        def lines(*maths: str) -> list[LineInput]:
            return [
                LineInput(i, m, posed=True, values=(("x", "3"),), continues="no" if i == 0 else "yes")
                for i, m in enumerate(maths)
            ]

        first = verify_lines(lines("x = 4"))
        known = first.first_invalid
        assert known is not None and known.error_type == "given_value_changed"
        later = verify_lines(lines("x = 4", "5x + 4 = 5(4) + 4", "= 24"), frozenset({known.fingerprint}))
        assert [step.error_type for step in later.invalid_steps] == ["given_value_changed"]

    def test_a_wrong_given_is_put_right_by_the_right_one_below(self, lesson: Lesson, model: Model) -> None:
        wrong = lesson.write(model, "x = 4")
        known = wrong["mistakes"][0]["id"]
        lesson.shown(known)
        fixed = lesson.write(model, "x = 4", "x = 3", "5(3) + 4", "= 19", acknowledged=[known])
        assert known in fixed["resolvedMistakeIds"], fixed
        assert fixed["mistakes"] == [] and fixed["outcome"] == "correct"

    @pytest.mark.parametrize(
        ("values", "line", "term"),
        [
            ((("x", "-4"),), "x^2 + 6x = x^2 + 6(4)", "6x"),
            ((("x", "4"), ("y", "-5")), "3x - y = 3x - 5", "y"),
        ],
    )
    def test_a_partial_substitution_names_the_term_that_went_wrong(
        self, values: tuple[tuple[str, str], ...], line: str, term: str
    ) -> None:
        from quack.services.math_verifier import LineInput, verify_lines

        report = verify_lines([LineInput(0, line, posed=True, values=values, continues="no")])
        assert report.first_invalid is not None
        assert report.first_invalid.evidence.split(", ")[1].startswith(f"{term} became"), report.first_invalid.evidence

    def test_a_sign_lost_on_the_way_through_arithmetic_is_still_a_lost_sign(self) -> None:
        values = (("x", "4"), ("y", "-5"))
        steps = verified(values, "3x - y = 3(4) - y", "= 12 - 5")
        assert steps[-1] == (1, "invalid", "sign_lost_in_substitution"), steps
