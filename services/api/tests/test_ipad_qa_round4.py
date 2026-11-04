"""Phase 3 iPad QA, round 4, 2026-11-03 at 23:06: Level 2's last challenge, then Level 3, replayed through the backend.

The readings are the real model's, from that session's development trace (`GET /api/dev/trace/{user}:practice-
{problem}` on the API that served it, saved under `services/api/var/ipad_qa_round4_2026-11-03/`). They're played
back through a mocked Meta API, so this shows what Quack does with them. It says nothing about how often the real
model reads the same ink the same way, and nothing here is a measure of handwriting recognition.

What the session showed:

- `If x = -3, work out x² + 3x`: `= (3)^2 + 3(-3)`, read the same by both readings, was valid, as its value is
  right, and the level was mastered. 3 isn't the value the question gives x: squaring hid the sign.
- `Simplify 8a - 5a`: `= 3x` was read as `3a` by one reading and `3x` by the other, five checks running, so Quack
  rightly said nothing. When both read `3x` it was flagged, but as an unnamed slip, with the hint for adding up
  matching terms. The numbers were right, and the letter changed.
- `Simplify 8a + 3b - 4a - 7b`: `= 8a - 4a + 3b - 7a`, read the same by both, was flagged with the same general
  hint. Every term was copied right but one, and that one kept its number and changed its letter.
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
from tests.test_lessons import Lesson, store_of
from tests.test_line_memory import compact
from tests.test_practice import CHAT, Model, completion


class TwoReaders(Model):
    """Both readings the same, unless `second` is set, as when the two read one line differently."""

    def __init__(self) -> None:
        super().__init__()
        self.second: list[dict[str, Any]] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["response_format"]["json_schema"]["name"] == "CompactReading" and self.second is not None:
            self.calls.append(body)
            # The confirming reading runs at low effort, the first at minimal. See ModelRouter.
            confirming = body.get("reasoning_effort") == "low"
            return completion({"lines": self.second if confirming else self.reply})
        return super().__call__(request)


@pytest.fixture
def model() -> Any:
    with respx.mock:
        answer = TwoReaders()
        respx.post(CHAT).mock(side_effect=answer)
        yield answer


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


def disagreeing(lesson: Lesson, model: TwoReaders, first: list[dict[str, Any]], second: list[dict[str, Any]]) -> dict[str, Any]:
    model.second = second
    try:
        return lesson.write(model, *[line["math"] for line in first], **{})
    finally:
        model.second = None


def help_texts(lesson: Lesson, model: Model, mistake: str) -> tuple[str, str, str]:
    """Hints 1 and 2 and the explanation, each of which has to be Quack's own wording, with no model asked."""

    lesson.shown(mistake)
    calls = len(model.calls)
    session = lesson.question["checkSessionId"]
    texts = []
    for level in (1, 2):
        reply = lesson.client.post(
            "/api/hint", json={"sessionId": session, "mistakeId": mistake, "level": level}, headers=lesson.headers
        ).json()
        assert reply["source"] == "template", reply
        texts.append(reply["text"])
    explained = lesson.client.post("/api/explain", json={"sessionId": session, "mistakeId": mistake}, headers=lesson.headers).json()
    assert explained["source"] == "template", explained
    assert len(model.calls) == calls, "help for a mistake the verifier has named in full needs no model"
    return texts[0], texts[1], explained["text"]


GENERIC = ("added up the matching terms", "coefficients of terms with the same power")


@pytest.fixture
def substitution(accounts_client: TestClient) -> Lesson:
    from tests.accounts_support import register
    from tests.test_lessons import unlock

    headers = register(accounts_client, "round4-substitution@example.com")
    unlock(accounts_client, headers, "meet_the_variables")
    lesson = Lesson(accounts_client, "substitute_and_evaluate", headers=headers)
    pose(lesson, "substitution.square_negative", value=3, a=3)
    return lesson


@pytest.fixture
def like_terms(accounts_client: TestClient) -> Lesson:
    return Lesson(accounts_client, "combine_like_terms")


class TestASignHiddenByASquare:
    """If x = -3, work out x² + 3x. The level's last challenge in the video, at 02:00 to 03:25."""

    def test_the_videos_working_is_one_slip_on_the_substitution(self, substitution: Lesson, model: Model) -> None:
        # v36 and v39: every line read the same by both readings.
        reply = substitution.write(model, "x = -3", "x^2 + 3x", "= (3)^2+3(-3)", "= 9+(-9)", "= 0")
        assert reply["outcome"] == "incorrect"
        assert len(reply["mistakes"]) == 1, reply["mistakes"]
        slip = reply["mistakes"][0]
        assert slip["lineId"] == f"line-{substitution.version}-2"
        assert (slip["category"], slip["errorType"]) == ("substitution", "sign_lost_value_unchanged")
        # The next question still practises signs, as a lost sign should.
        assert reply["misconception"]["id"] == "sign_error"

    def test_as_soon_as_the_substitution_is_written_it_is_said(self, substitution: Lesson, model: Model) -> None:
        # v27, the first check to have the line.
        reply = substitution.write(model, "x = -3", "x^2 + 3x", "= (3)^2+3(-3)")
        assert [m["errorType"] for m in reply["mistakes"]] == ["sign_lost_value_unchanged"]

    def test_help_says_the_value_is_right_and_the_substitution_is_not(self, substitution: Lesson, model: Model) -> None:
        reply = substitution.write(model, "x = -3", "x^2 + 3x", "= (3)^2+3(-3)", "= 9+(-9)", "= 0")
        first, second, explanation = help_texts(substitution, model, reply["mistakes"][0]["id"])
        assert "x = −3" in first and "x^2" in first, first
        assert "(−3)^2" in second and "same value" in second, second
        assert "same value" in explanation and "still works" in explanation, explanation
        for text in (first, second, explanation):
            # Nothing may say the value or the answer is wrong, because it isn't.
            lowered = text.lower()
            assert not any(word in lowered for word in ("wrong", "incorrect", "not equal", "isn't equal")), text
            assert not any(phrase in text for phrase in GENERIC), text

    def test_putting_the_sign_in_puts_it_right(self, substitution: Lesson, model: Model) -> None:
        wrong = substitution.write(model, "x = -3", "x^2 + 3x", "= (3)^2+3(-3)", "= 9+(-9)", "= 0")
        known = wrong["mistakes"][0]["id"]
        substitution.shown(known)
        fixed = substitution.write(model, "x = -3", "x^2 + 3x", "= (-3)^2+3(-3)", "= 9+(-9)", "= 0", acknowledged=[known])
        assert known in fixed["resolvedMistakeIds"]
        assert fixed["mistakes"] == [] and fixed["outcome"] == "correct"

    def test_the_substitution_written_again_below_puts_it_right(self, substitution: Lesson, model: Model) -> None:
        reply = substitution.write(model, "x^2 + 3x", "= (3)^2+3(-3)", "= (-3)^2+3(-3)", "= 0")
        assert reply["mistakes"] == [], reply["mistakes"]

    def test_a_true_line_below_does_not_put_it_right(self, substitution: Lesson, model: Model) -> None:
        # 9 + (-9) follows from (-3)^2 + 3(-3) as well, so it would otherwise count as the fix, and hide the slip.
        reply = substitution.write(model, "x^2 + 3x", "= (3)^2+3(-3)", "= 9+(-9)")
        assert [m["errorType"] for m in reply["mistakes"]] == ["sign_lost_value_unchanged"]

    @pytest.mark.parametrize(
        "working",
        [
            ["x = -3", "x^2 + 3x", "= (-3)^2+3(-3)", "= 9+(-9)", "= 0"],
            ["(-3)^2 + 3(-3)", "= 9 - 9", "= 0"],
            # (-3)^2 is 3^2, and once the letters have gone that's arithmetic, not substitution.
            ["(-3)^2 + 3(-3)", "= 3^2 - 9", "= 0"],
            # Working that never writes the substitution out says nothing about it, right or wrong.
            ["9 - 9", "= 0"],
            ["0"],
            ["= 0"],
        ],
    )
    def test_faithful_or_unwritten_substitution_is_right(self, substitution: Lesson, model: Model, working: list[str]) -> None:
        reply = substitution.write(model, *working)
        assert reply["mistakes"] == [], reply["mistakes"]
        assert reply["outcome"] == "correct"

    def test_a_sign_lost_where_it_changes_the_value_is_still_the_other_slip(self, substitution: Lesson, model: Model) -> None:
        reply = substitution.write(model, "x^2 + 3x", "= (-3)^2+3(3)", "= 9+9", "= 18")
        assert [m["errorType"] for m in reply["mistakes"]] == ["sign_lost_in_substitution"]


def verified(values: tuple[tuple[str, str], ...], *lines: str) -> list[tuple[int, str, str | None]]:
    from quack.services.math_verifier import LineInput, verify_lines

    report = verify_lines(
        [LineInput(i, math, posed=True, values=values, continues="no" if i == 0 else "yes") for i, math in enumerate(lines)]
    )
    return [(step.line, step.status, step.error_type or step.reason) for step in report.steps]


class TestSignsAndPowers:
    """The rule for any power and value, through the verifier alone, so it isn't fitted to one question."""

    @pytest.mark.parametrize(
        ("value", "line", "expected"),
        [
            # Even powers hide the sign.
            ("-3", "x^2 = (3)^2", "sign_lost_value_unchanged"),
            ("-4", "x^2 = (4)^2", "sign_lost_value_unchanged"),
            ("-3", "2x^2 = 2(3)^2", "sign_lost_value_unchanged"),
            ("-3", "-x^2 = -(3)^2", "sign_lost_value_unchanged"),
            ("-1/2", "4x^2 = 4(1/2)^2", "sign_lost_value_unchanged"),
            # A positive value given a minus is the same slip the other way.
            ("3", "x^2 + 3x = (-3)^2 + 3(3)", "sign_lost_value_unchanged"),
            # Odd powers don't, and the value is wrong as well.
            ("-3", "x^3 = (3)^3", "sign_lost_in_substitution"),
            ("-3", "x^2 + 3x = (3)^2 + 3(3)", "sign_lost_in_substitution"),
            # Right.
            ("-3", "x^2 = (-3)^2", None),
            ("-3", "x^3 = (-3)^3", None),
            ("-3", "-x^2 = -(-3)^2", None),
            ("3", "x^2 = (3)^2", None),
            ("0", "x^2 = (0)^2", None),
            ("-1/2", "4x^2 = 4(-1/2)^2", None),
        ],
    )
    def test_each(self, value: str, line: str, expected: str | None) -> None:
        steps = verified((("x", value),), line)
        assert steps == [(0, "invalid" if expected else "valid", expected)], steps

    def test_the_notebook_has_no_values_and_is_unchanged(self) -> None:
        from quack.services.math_verifier import LineInput, verify_lines

        report = verify_lines([LineInput(0, "x^2 + 3x"), LineInput(1, "= (3)^2 + 3(-3)", continues="yes")])
        assert all(step.error_type != "sign_lost_value_unchanged" for step in report.steps)


class TestALetterThatChanged:
    """Simplify 8a − 5a, at 05:10 to 07:30 in the video."""

    @pytest.fixture(autouse=True)
    def question(self, like_terms: Lesson) -> None:
        pose(like_terms, "like_terms.subtract_two", a=8, b=5, letter="a")

    def test_both_readings_say_3x_and_it_is_a_changed_letter(self, like_terms: Lesson, model: Model) -> None:
        # v15: `=3x`, clear, from both.
        reply = like_terms.write(model, "8a - 5a", "=3x")
        assert reply["outcome"] == "incorrect"
        [slip] = reply["mistakes"]
        assert slip["errorType"] == "variable_changed", slip
        assert reply["misconception"]["id"] == "letter_changed"

    def test_its_help_is_about_the_letter_and_not_adding_up(self, like_terms: Lesson, model: Model) -> None:
        reply = like_terms.write(model, "8a - 5a", "=3x")
        first, second, explanation = help_texts(like_terms, model, reply["mistakes"][0]["id"])
        assert "number correctly" in first and "letter" in first, first
        assert "keeps a" in second and "x" in second, second
        assert "never the letter" in explanation and "3a" in explanation, explanation
        for text in (first, second):
            assert "3a" not in text, text
        for text in (first, second, explanation):
            assert not any(phrase in text for phrase in GENERIC), text

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            # v10, v10 again: one reading sure of 3a, the other unsure, saying 3x.
            (compact("= 3a"), compact("= 3x", legibility="unsure", alternatives=["= 3a"])),
            (compact("= 3a", legibility="unsure", alternatives=["= 3x"]), compact("= 3x")),
            # v10 later, and v13: the other way round.
            (compact("= 3x", legibility="unsure", alternatives=["= 3a"]), compact("=3a")),
        ],
    )
    def test_when_the_readings_disagree_it_is_neither_wrong_nor_right(
        self, like_terms: Lesson, model: TwoReaders, first: dict[str, Any], second: dict[str, Any]
    ) -> None:
        """3a is the expected answer. That's no reason to believe a reading of it, and Quack doesn't."""

        reply = disagreeing(like_terms, model, [compact("8a - 5a"), first], [compact("8a - 5a"), second])
        assert reply["mistakes"] == []
        assert reply["outcome"] == "in_progress", reply
        # Not read yet, which is what the lesson tells the student, rather than "nothing looks wrong".
        # The status can be no_error, as on the iPad, when the reading used is the valid one.
        assert f"line-{like_terms.version}-1" not in reply["settledLineIds"]
        assert reply["allRead"] is False

    @pytest.mark.parametrize(
        ("answer", "outcome", "error_type"),
        [
            ("= 3a", "correct", None),
            ("= 3x", "incorrect", "variable_changed"),
            ("= 3b", "incorrect", "variable_changed"),
            ("= 3", "incorrect", "lost_variable"),
            ("= 13a", "incorrect", "combining_like_terms"),
            ("= -3a", "incorrect", "sign_error"),
            # A capital is most likely the same letter written bigger. Not a mistake, and not a finished answer.
            ("= 3A", "in_progress", None),
        ],
    )
    def test_each_answer(self, like_terms: Lesson, model: Model, answer: str, outcome: str, error_type: str | None) -> None:
        reply = like_terms.write(model, "8a - 5a", answer)
        assert reply["outcome"] == outcome, reply
        assert [m["errorType"] for m in reply["mistakes"]] == ([error_type] if error_type else [])

    def test_in_x_it_is_right_in_x(self, like_terms: Lesson, model: Model) -> None:
        pose(like_terms, "like_terms.subtract_two", a=8, b=5, letter="x")
        reply = like_terms.write(model, "8x - 5x", "= 3x")
        assert reply["outcome"] == "correct" and reply["mistakes"] == []
        assert reply["allRead"] is True

    def test_disagreeing_readings_cost_no_credit(self, like_terms: Lesson, model: TwoReaders) -> None:
        disagreeing(like_terms, model, [compact("8a - 5a"), compact("= 3a")], [compact("8a - 5a"), compact("= 3x", legibility="unsure")])
        reply = like_terms.write(model, "8a - 5a", "= 3a")
        assert reply["outcome"] == "correct"
        assert like_terms.next()["mastery"]["credits"] == 1


class TestATermThatChangedWhileMoving:
    """Simplify 8a + 3b − 4a − 7b, at 10:00 to 11:30 in the video."""

    @pytest.fixture(autouse=True)
    def question(self, like_terms: Lesson) -> None:
        pose(like_terms, "like_terms.two_letters_negative", a=8, b=3, c=4, d=7, letters=("a", "b"))

    def test_the_videos_line_is_one_letter_changed_in_one_term(self, like_terms: Lesson, model: Model) -> None:
        # v28: `=8a-4a+3b-7a`, from both readings.
        reply = like_terms.write(model, "8a + 3b - 4a - 7b", "=8a-4a+3b-7a")
        [slip] = reply["mistakes"]
        assert slip["errorType"] == "term_letter_changed", slip
        assert reply["misconception"]["id"] == "letter_changed"

    def test_its_help_names_the_term(self, like_terms: Lesson, model: Model) -> None:
        reply = like_terms.write(model, "8a + 3b - 4a - 7b", "=8a-4a+3b-7a")
        first, second, explanation = help_texts(like_terms, model, reply["mistakes"][0]["id"])
        assert "keep its letter" in first and "7" not in first, first
        assert "−7b" in second and "−7a" in second, second
        assert "8a - 4a + 3b - 7b" in explanation, explanation
        for text in (first, second, explanation):
            # Neither the simplified answer nor the general hint.
            assert "4a - 4b" not in text and "4a − 4b" not in text, text
            assert not any(phrase in text for phrase in GENERIC), text

    def test_moving_terms_around_is_right(self, like_terms: Lesson, model: Model) -> None:
        reply = like_terms.write(model, "8a + 3b - 4a - 7b", "= 8a-4a+3b-7b")
        assert reply["mistakes"] == [] and reply["outcome"] == "in_progress"
        done = like_terms.write(model, "8a + 3b - 4a - 7b", "= 8a-4a+3b-7b", "= 4a-4b")
        assert done["mistakes"] == [] and done["outcome"] == "correct"

    def test_a_sign_that_changed_while_moving_is_named_with_its_term(self, like_terms: Lesson, model: Model) -> None:
        reply = like_terms.write(model, "8a + 3b - 4a - 7b", "= 8a-4a+3b+7b")
        [slip] = reply["mistakes"]
        assert slip["errorType"] == "sign_error"
        first, second, _ = help_texts(like_terms, model, slip["id"])
        assert "keep its sign" in first, first
        assert "−7b" in second and "+7b" in second, second

    def test_terms_left_out_and_carried_on_from_are_named(self, like_terms: Lesson, model: Model) -> None:
        reply = like_terms.write(model, "8a + 3b - 4a - 7b", "= 8a-4a", "= 4a")
        [slip] = reply["mistakes"]
        assert slip["errorType"] == "terms_left_out" and slip["lineId"] == f"line-{like_terms.version}-1"
        first, second, _ = help_texts(like_terms, model, slip["id"])
        assert "every term still there" in first, first
        assert "3b" in second, second

    @pytest.mark.parametrize(
        "lines",
        [
            # A pause while copying the question out, on the page's last line.
            ["8a + 3b"],
            ["8a + 3b - 4a"],
            # And while rearranging it.
            ["8a + 3b - 4a - 7b", "= 8a - 4a"],
            ["8a + 3b - 4a - 7b", "= 8a - 4a + 3b"],
        ],
    )
    def test_a_line_part_way_through_being_copied_is_not_a_mistake(self, like_terms: Lesson, model: Model, lines: list[str]) -> None:
        reply = like_terms.write(model, *lines)
        assert reply["mistakes"] == [], reply["mistakes"]
        assert reply["outcome"] == "in_progress"

    def test_a_wrong_final_answer_is_still_a_mistake(self, like_terms: Lesson, model: Model) -> None:
        reply = like_terms.write(model, "8a + 3b - 4a - 7b", "= 4a - 10b")
        assert reply["outcome"] == "incorrect"


class TestWhatCheckCanSayWhenNothingIsWrongYet:
    """Found continuing round 4 on 2026-11-04: two holds the round added, on a page asked to be checked.

    `3x + 2x`, `= 3x` may be half-written, or may be a wrong answer meant as the last line. `= 3A` may be the
    right letter written large. Quack rightly calls neither wrong. The lesson had said "Nothing looks wrong" to
    both, which claims too much, so it now tells them apart by these codes. See `notYetRight` in learn.ts.
    """

    @pytest.mark.parametrize(
        ("template", "params", "lines", "reason"),
        [
            ("like_terms.add_two", {"a": 3, "b": 2, "letter": "x"}, ["3x + 2x", "= 3x"], "still_writing"),
            ("like_terms.two_letters_negative", {"a": 8, "b": 3, "c": 4, "d": 7, "letters": ("a", "b")},
             ["8a + 3b - 4a - 7b", "= 8a - 4a"], "still_writing"),
            ("like_terms.subtract_two", {"a": 8, "b": 5, "letter": "a"}, ["8a - 5a", "= 3A"], "letter_case"),
        ],
    )
    def test_the_hold_is_said_and_nothing_is_right_or_wrong(
        self, like_terms: Lesson, model: Model, template: str, params: dict[str, Any], lines: list[str], reason: str
    ) -> None:
        pose(like_terms, template, **params)
        reply = like_terms.write(model, *lines)
        assert reply["mistakes"] == [] and reply["outcome"] == "in_progress"
        assert (reply["status"], reply["reason"], reply["allRead"]) == ("uncertain", reason, True)
        assert like_terms.next()["mastery"]["credits"] == 0

    def test_a_half_copied_line_is_checked_once_anything_is_written_below(self, like_terms: Lesson, model: Model) -> None:
        pose(like_terms, "like_terms.add_two", a=3, b=2, letter="x")
        reply = like_terms.write(model, "3x + 2x", "= 3x", "= 3x")
        assert reply["outcome"] == "incorrect"
        assert [m["errorType"] for m in reply["mistakes"]] == ["terms_left_out"]


class TestALetterThatChangedOutsideLikeTerms:
    """Found continuing round 4 on 2026-11-04. `variable_changed` is right wherever a letter changes, as in
    brackets, and with two letters. Its help had said "combining like terms" and "8a + 3b - 4a - 7b is in b"."""

    def test_two_letters_say_which_one_has_no_place(self, like_terms: Lesson, model: Model) -> None:
        pose(like_terms, "like_terms.two_letters_negative", a=8, b=3, c=4, d=7, letters=("a", "b"))
        reply = like_terms.write(model, "8a + 3b - 4a - 7b", "= 4a - 4x")
        [slip] = reply["mistakes"]
        assert slip["errorType"] == "variable_changed"
        first, second, explanation = help_texts(like_terms, model, slip["id"])
        assert "numbers correctly" in first and "which letters" in first, first
        assert "no x in 8a + 3b - 4a - 7b" in second, second
        assert "4a - 4b" not in first + second
        assert "is in b" not in explanation and "4a - 4b" in explanation, explanation

    @pytest.mark.parametrize(
        ("lines", "answer"),
        [(["2(x + 3) = 2y + 6"], "2x + 6"), (["(x + 1)^2 = y^2 + 2y + 1"], "x^2 + 2x + 1")],
    )
    def test_in_brackets_it_is_named_without_talk_of_like_terms(self, lines: list[str], answer: str) -> None:
        from types import SimpleNamespace

        from quack.services.hint_engine import template_text
        from quack.services.math_verifier import LineInput, verify_lines

        [step] = verify_lines([LineInput(0, lines[0], posed=True, continues="no")]).steps
        assert (step.status, step.error_type) == ("invalid", "variable_changed")
        mistake = SimpleNamespace(
            category=step.error_category, error_type=step.error_type, evidence=step.evidence,
            previous_step=step.previous_step, current_step=step.current_step, expected=step.expected,
        )
        texts = [template_text(level, mistake) for level in (1, 2, 3)]  # type: ignore[arg-type]
        assert all("like terms" not in text and "ombining" not in text for text in texts), texts
        assert answer not in texts[0] + texts[1] and answer in texts[2], texts


class TestTheIndependentReview:
    """Found by the independent review on 2026-11-04, each reproduced, then fixed."""

    @pytest.mark.parametrize(
        ("values", "lines", "wrong"),
        [
            # A finished wrong part before the part being written isn't held back with it.
            ((), ["5x + 2x + 3 = 8x + 3 = 8x"], "combining_like_terms"),
            ((("x", "-3"),), ["x^2 + 3x", "= (-3)^2 + 3(-3) = 9 + 9 = 9"], "arithmetic_error"),
            ((), ["8a + 3b - 4a - 7b = 5a - 4b = 5a"], "combining_like_terms"),
        ],
    )
    def test_still_writing_holds_back_only_the_part_being_written(
        self, values: tuple[tuple[str, str], ...], lines: list[str], wrong: str
    ) -> None:
        steps = verified(values, *lines)
        assert any(status == "invalid" and kind == wrong for _, status, kind in steps), steps

    @pytest.mark.parametrize(
        "lines",
        [
            ["x^2 + 3x", "= (3)^2 + 3(-3)", "= (-3)^2 + 3(-3) = 0"],
            ["x^2 + 3x", "= (3)^2 + 3(-3)", "= (-3)^2 + 3(-3) = 9 - 9"],
            ["x^2 + 3x", "= (3)^2 + 3(-3)", "= 9 - 9", "= (-3)^2 + 3(-3)", "= 0"],
            ["x^2 + 3x", "= (3)^2 + 3(-3)", "= (-3)^2 + 3(-3)", "= 0"],
        ],
    )
    def test_a_hidden_sign_put_right_below_is_put_right(self, lines: list[str]) -> None:
        steps = verified((("x", "-3"),), *lines)
        assert all(status != "invalid" for _, status, _ in steps), steps
        assert (1, "uncertain", "sign_lost_value_unchanged") in steps

    def test_a_hidden_sign_only_worked_out_below_is_still_a_slip(self) -> None:
        steps = verified((("x", "-3"),), "x^2 + 3x", "= (3)^2 + 3(-3)", "= 9 - 9", "= 0")
        assert (1, "invalid", "sign_lost_value_unchanged") in steps

    @pytest.mark.parametrize(
        ("lines", "expected"),
        [
            # Written the other way round, which is right, and swapped, which isn't a lost sign.
            (["xy", "= (2)(-2)"], "valid"),
            (["x + y", "= 2 + (-2)"], "valid"),
        ],
    )
    def test_another_letters_value_is_not_a_lost_sign(self, lines: list[str], expected: str) -> None:
        steps = verified((("x", "-2"), ("y", "2")), *lines)
        assert steps == [(1, expected, None)], steps

    def test_a_lost_sign_beside_another_letter_is_still_said(self) -> None:
        assert verified((("x", "-3"), ("y", "2")), "x^2 + y", "= (3)^2 + 2") == [(1, "invalid", "sign_lost_value_unchanged")]
