"""The iPad session of 2026-11-04, evening: the Algebra final challenge, replayed through the backend.

The readings are the real model's, from that session's development trace (`GET /api/dev/trace/{session}` on the
server that ran it, port 8200, saved before anything was restarted). Each reading is given here as the model
returned it, before the question was put in front of it. They're played back through a mocked Meta API, by
reasoning effort, so the two readings can differ as they did. This shows what Quack does with those readings. It
says nothing about how often the real model reads the same ink the same way.

What the trace showed:

- Q1, `Simplify 7(x + 6) - 4(x - 2)`. The student copied the question out first. At v24 and again at v47 both
  readings read that copy as `7(x+8)-4(x-2)`, and it was checked as a step from the question: "The first bracket is
  7(x + 6), not 7(x + 8)". At v31, v34 and v44 both readings said `7(x+6)-4(x-2)`, but the agreed `+8` from v24 was
  remembered for that ink and used instead, so the accusation stayed. The real mistake, `= 7x + 42 - 4x - 8`, was
  held back as "follows a new mistake" until the copy was rewritten.
- Q1, end. `= 3x + 34` struck through, `= 3x + 50` under it. Both readings of three checks saw the struck line as
  crossed out (read as `3x+34`, `5x+34` or illegible). Nothing was wrong, but the struck line was never "read", so
  the right answer couldn't finish the question until the student rubbed the old line out (19:59:32 to 20:00:11).
- Q2, `Simplify 7(x + 5) - 4(x - 2)`. The copy was read five ways across checks, `-4(x-8)` by both readings at v33
  (a second false accusation), and the right `= 7x + 35 - 4x + 8`, `= 3x + 43` under it, read clearly by both, was
  held as "answers the line above" for three and a half minutes.
"""

from __future__ import annotations

import random
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient

from quack.services.handwriting import HandwritingReading, ReadLine
from quack.services.lesson_questions import TEMPLATES
from quack.services.lessons import question_columns
from quack.services.practice import RESTATED_QUESTION, answer_lines, restates_the_question
from tests.accounts_support import register
from tests.test_ipad_qa_nov4 import ByEffort, check, line
from tests.test_lessons import Lesson, store_of, unlock
from tests.test_pipeline import CHAT

LEVEL = "brackets_and_factors"
EARLIER = ("meet_the_variables", "substitute_and_evaluate", "combine_like_terms", "simplify_expressions")


def lesson_for(client: TestClient, email: str, params: dict[str, int]) -> Lesson:
    headers = register(client, email)
    unlock(client, headers, *EARLIER)
    started = Lesson(client, LEVEL, headers=headers)
    question = TEMPLATES["brackets.two_brackets"].build(random.Random(0), params)
    user = client.get("/api/profile", headers=headers).json()["userId"]
    columns = question_columns(question)
    assignments = ", ".join(f"{name} = ?" for name in columns)
    store_of(client).database.execute(
        f"UPDATE practice_problems SET {assignments} WHERE user_id = ? AND id = ?", (*columns.values(), user, started.question["id"])
    )
    return started


@pytest.fixture
def model() -> Any:
    with respx.mock:
        answer = ByEffort()
        respx.post(CHAT).mock(side_effect=answer)
        yield answer


@pytest.fixture
def q1(accounts_client: TestClient) -> Lesson:
    return lesson_for(accounts_client, "final-q1@example.com", {"a": 7, "b": 6, "c": 4, "d": 2})


@pytest.fixture
def q2(accounts_client: TestClient) -> Lesson:
    return lesson_for(accounts_client, "final-q2@example.com", {"a": 7, "b": 5, "c": 4, "d": 2})


def crossed(math: str, legibility: str = "unsure") -> dict[str, Any]:
    return line(math, kind="crossed_out", legibility=legibility)


def texts(reply: dict[str, Any]) -> str:
    return " ".join(f"{m['previousStep']} -> {m['currentStep']}" for m in reply["mistakes"])


# Line ids from the trace.
COPY, COPY_TOUCHED, COPY_REWRITTEN = "dv02wqtrp7hs", "8x2j1kxh1p3", "2gn1hdetbz6v"
WRONG_SIGN, RIGHT_SIGN, WRONG_SUM, STRUCK, ANSWER = "4kq1k3xhf", "d4x553fysg29", "3c0zdgjk", "186vkt6dpahg8", "elyp6gj9hbii"


class TestTheQuestions:
    def test_they_are_the_questions_from_the_video(self, q1: Lesson, q2: Lesson) -> None:
        assert q1.row["prompt_math"] == "7(x + 6) - 4(x - 2)"
        assert q1.row["expected_answer"] == "3x + 50"
        assert q2.row["prompt_math"] == "7(x + 5) - 4(x - 2)"
        assert q2.row["expected_answer"] == "3x + 43"


class TestTheCopyIsNotAccused:
    def test_v24_both_readings_saw_an_8_and_nothing_is_said_about_it(self, q1: Lesson, model: ByEffort) -> None:
        model.read([line("7(x+8)-4(x-2)"), line("= 7x+")])
        reply = check(q1, [(COPY, True), ("lifsr3", True)])
        assert reply["mistakes"] == []
        assert "x + 8" not in texts(reply) and "x+8" not in texts(reply)

    def test_v47_the_8_again_and_the_real_sign_mistake_is_found_on_its_own_line(self, q1: Lesson, model: ByEffort) -> None:
        model.read([line("7(x+8)-4(x-2)"), line("= 7x+42-4x-8"), line("= 3x+34")])
        reply = check(q1, [(COPY_TOUCHED, True), (WRONG_SIGN, True), (WRONG_SUM, True)])
        assert len(reply["mistakes"]) == 1, texts(reply)
        mistake = reply["mistakes"][0]
        # From the question Quack set to the student's expansion: the -4 times -2.
        assert mistake["previousStep"] == "7(x + 6) - 4(x - 2)"
        assert mistake["currentStep"].replace(" ", "") == "7x+42-4x-8"
        assert reply["outcome"] == "incorrect"

    def test_conflicting_6_and_8_readings_are_never_an_accusation(self, q1: Lesson, model: ByEffort) -> None:
        model.read([line("7(x+6)-4(x-2)")], [line("7(x+8)-4(x-2)")])
        reply = check(q1, [(COPY, True)])
        assert reply["mistakes"] == []
        assert reply["outcome"] == "in_progress"

    def test_a_copy_deliberately_changed_is_neither_accused_nor_accepted(self, q1: Lesson, model: ByEffort) -> None:
        model.read([line("7(x+8)-4(x-2)")])
        assert check(q1, [(COPY, True)])["mistakes"] == []
        # Worked through from the changed copy: the working doesn't follow from the question, and that's said there.
        model.read([line("7(x+8)-4(x-2)"), line("= 7x+56-4x+8"), line("= 3x+64")])
        reply = check(q1, [(COPY, True), ("w1", True), ("w2", True)], trigger="manual")
        assert reply["outcome"] != "correct"
        assert reply["mistakes"] and all("x + 8)" not in m["currentStep"] for m in reply["mistakes"])
        assert q1.row["status"] != "correct"


class TestARememberedReadingDoesNotOutliveNewOnes:
    """v31, v34, v44: the remembered agreed reading was used, though both new readings said otherwise."""

    def test_ink_read_as_two_answers_is_neither_right_nor_wrong(self, q1: Lesson, model: ByEffort) -> None:
        # The same ink, a line of working this time: agreed as `-4x-3` (wrong), then agreed as `-4x+8` (right).
        model.read([line("7(x+6)-4(x-2)"), line("= 7x+42-4x-3")])
        first = check(q1, [(COPY_REWRITTEN, True), (RIGHT_SIGN, True)])
        assert len(first["mistakes"]) == 1
        known = [first["mistakes"][0]["id"]]
        model.read([line("7(x+6)-4(x-2)"), line("= 7x+42-4x+8")])
        second = check(q1, [(COPY_REWRITTEN, True), (RIGHT_SIGN, True)], known=known, scopes={known[0]: [RIGHT_SIGN]})
        # Before: the remembered `-4x-3` was checked again and the alert stayed.
        assert second["mistakes"] == []
        assert second["outcome"] != "correct"


class TestTheStruckLineDoesNotHoldBackTheAnswer:
    def test_v66_a_right_answer_under_a_crossed_out_line_finishes_the_question(self, q1: Lesson, model: ByEffort) -> None:
        # As at v66: struck line unsure in one reading and illegible in the other, both crossed out.
        model.read(
            [line("7(x+6)-4(x-2)"), line("=7x+42-4x+8"), crossed("=3x+34", "unsure"), line("=3x+50")],
            [line("7(x+6)-4(x-2)"), line("= 7x+42-4x+8"), crossed("=3x+34", "illegible"), line("= 3x+50")],
        )
        reply = check(q1, [(COPY_REWRITTEN, True), (RIGHT_SIGN, True), (STRUCK, True), (ANSWER, True)], trigger="manual")
        assert reply["mistakes"] == []
        assert STRUCK in reply["settledLineIds"]
        assert reply["outcome"] == "correct"
        assert q1.row["status"] == "correct"

    def test_and_with_the_struck_line_rubbed_out_too(self, q1: Lesson, model: ByEffort) -> None:
        model.read([line("7(x+6)-4(x-2)"), line("= 7x+42-4x+8"), line("= 3x+50")])
        reply = check(q1, [(COPY_REWRITTEN, True), (RIGHT_SIGN, True), (ANSWER, True)], trigger="manual")
        assert reply["outcome"] == "correct"

    def test_an_answer_struck_out_with_nothing_under_it_is_not_finished(self, q1: Lesson, model: ByEffort) -> None:
        model.read([line("7(x+6)-4(x-2)"), line("= 7x+42-4x+8"), crossed("= 3x+50")])
        reply = check(q1, [(COPY_REWRITTEN, True), (RIGHT_SIGN, True), (STRUCK, True)], trigger="manual")
        assert reply["outcome"] == "in_progress"
        assert q1.row["status"] != "correct"

    def test_one_reading_sure_it_is_working_is_not_crossed_out(self, q1: Lesson, model: ByEffort) -> None:
        model.read(
            [line("7(x+6)-4(x-2)"), line("= 7x+42-4x+8"), crossed("= 3x+34"), line("= 3x+50")],
            [line("7(x+6)-4(x-2)"), line("= 7x+42-4x+8"), line("= 3x+34"), line("= 3x+50")],
        )
        reply = check(q1, [(COPY_REWRITTEN, True), (RIGHT_SIGN, True), (STRUCK, True), (ANSWER, True)], trigger="manual")
        assert reply["outcome"] != "correct"
        assert STRUCK not in reply["settledLineIds"]

    def test_a_wrong_sum_is_still_a_mistake(self, q1: Lesson, model: ByEffort) -> None:
        model.read([line("7(x+6)-4(x-2)"), line("= 7x+42-4x+8"), line("= 3x+34")])
        reply = check(q1, [(COPY_REWRITTEN, True), (RIGHT_SIGN, True), (WRONG_SUM, True)])
        assert len(reply["mistakes"]) == 1
        assert reply["mistakes"][0]["currentStep"].replace(" ", "") == "3x+34"


class TestQuestionTwo:
    def test_v33_both_readings_saw_x_minus_8_and_nothing_is_said_about_it(self, q2: Lesson, model: ByEffort) -> None:
        model.read([line("7(x+5)-4(x-8)"), line("= 7x+35-4x+")])
        assert check(q2, [("1d309nxj43wvf", True), ("uep2m", True)])["mistakes"] == []

    def test_v47_the_right_working_under_a_copy_read_two_ways_finishes_the_question(self, q2: Lesson, model: ByEffort) -> None:
        # v44: the copy read as `7(2x+5)` and `7(x+5)`, and the last line `3x+43` / `3x+7`, both unsure.
        model.read(
            [line("7(2x+5)-4(x-2)"), line("=7x+35-4x+8"), line("=3x+43", legibility="unsure", alternatives=["=3x+27"])],
            [line("7(x+5)-4(x-2)"), line("=7x+35-4x+8"), line("=3x+7", legibility="unsure", alternatives=["=3x+43"])],
        )
        first = check(q2, [("fi9qlz1kzv8i5", True), ("je7sk", True), ("loxru", True)])
        assert first["mistakes"] == [] and first["outcome"] != "correct"
        # v47, manual: both readings now `7(2x+5)-4(x-2)` for the copy, and `= 3x + 43` clearly. Before: uncertain,
        # `corrected_further_down`, and the question stayed open until v59.
        model.read([line("7(2x+5)-4(x-2)"), line("= 7x+35-4x+8"), line("= 3x+43")])
        reply = check(q2, [("fi9qlz1kzv8i5", True), ("je7sk", True), ("pvm3s", True)], trigger="manual")
        assert reply["mistakes"] == []
        assert reply["outcome"] == "correct"

    def test_a_correct_alternative_form_is_accepted(self, q2: Lesson, model: ByEffort) -> None:
        model.read([line("7(x+5)-4(x-2)"), line("= 7x+35-4x+8"), line("= 43+3x")])
        assert check(q2, [("a", True), ("b", True), ("c", True)], trigger="manual")["outcome"] == "correct"

    def test_a_wrong_answer_is_never_accepted(self, q2: Lesson, model: ByEffort) -> None:
        model.read([line("7(x+5)-4(x-2)"), line("= 7x+35-4x+8"), line("= 3x+27")])
        reply = check(q2, [("a", True), ("b", True), ("c", True)], trigger="manual")
        assert reply["outcome"] == "incorrect"


def _page(*maths: str) -> HandwritingReading:
    return HandwritingReading(lines=[
        ReadLine(raw_text=m, math=m, kind="math", legibility="clear", alternatives=[], continues_previous="unknown",
                 operation="other", y_top=0, y_bottom=1)
        for m in maths
    ])


class TestWhatCountsAsACopy:
    @pytest.mark.parametrize(("question", "written"), [
        ("7(x + 6) - 4(x - 2)", "7(x+6)-4(x-2)"),
        ("7(x + 6) - 4(x - 2)", "7(x+8)-4(x-2)"),
        ("7(x + 5) - 4(x - 2)", "7(2x+5)-4(2x-8)"),
        ("4(x + 6) = x + 48", "4(x+6)=x+43"),
        ("3x + 2x", "3x+2x"),
    ])
    def test_copies(self, question: str, written: str) -> None:
        assert restates_the_question(written, question)

    @pytest.mark.parametrize(("question", "written"), [
        ("7(x + 6) - 4(x - 2)", "7x+42-4x+8"),       # the first step
        ("7(x + 6) - 4(x - 2)", "7(x+6)-4x-2"),      # a bracket opened wrongly: a real mistake
        ("7(x + 6) - 4(x - 2)", "7(x+6)+4(x-2)"),    # a sign changed: checked as a step
        ("4(x + 6) = x + 48", "4x+6=x+48"),          # the classic slip, two characters away
        ("4(x + 6) = x + 48", "4(x+6)-x=48"),        # a term moved
        ("2x = 10", "x=10"),                          # an answer
        ("3x + 2x", "5"),                             # the demo's missing letter
        ("17 + 26", "43"),
        ("6/9", "6/8"),
        ("2x^2 - x", "2(-4)^2-x"),                    # a substitution
    ])
    def test_not_copies(self, question: str, written: str) -> None:
        assert not restates_the_question(written, question)

    def test_only_the_first_line_and_the_working_then_starts_from_the_question(self) -> None:
        filled = answer_lines("7(x + 6) - 4(x - 2)")(_page("7(x+8)-4(x-2)", "= 7x+42-4x+8", "7(x+6)-4(x-2)"))
        assert filled.lines[0].kind == "prose" and filled.lines[0].raw_text == RESTATED_QUESTION
        assert filled.lines[1].math == "7(x + 6) - 4(x - 2) = 7x+42-4x+8"
        # Further down, the same text is a line of working like any other.
        assert filled.lines[2].kind == "math"

    def test_filling_twice_changes_nothing(self) -> None:
        fill = answer_lines("7(x + 6) - 4(x - 2)")
        once = fill(_page("7(x+8)-4(x-2)", "= 7x+42-4x+8"))
        assert fill(once) == once

    def test_an_equation_copy_and_the_step_under_it(self) -> None:
        filled = answer_lines("4(x + 6) = x + 48")(_page("4(x+6)=x+43", "4x+24=x+48", "4(x+6)=x+48"))
        assert [line.kind for line in filled.lines] == ["prose", "math", "math"]
