"""Defects the independent reviews reproduced, each pinned to what it should have done."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from quack.main import build_services, create_app
from quack.schemas import Recap, RecapGlance, Recommendation
from quack.services.hint_engine import CLAIMS_WHAT_THE_STUDENT_DID
from quack.services.math_verifier import LineInput, verify_lines
from quack.services.recap import phrased
from tests.accounts_support import account_settings
from tests.test_learning import Student
from tests.test_line_memory import compact
from tests.test_pipeline import CHAT, completion, png
from tests.test_practice import Practice


class Readings:
    """Each reading takes the next page of lines, and the last one repeats."""

    def __init__(self) -> None:
        self.pages: list[list[dict[str, Any]]] = []
        self.calls = 0
        # What the other tests' models answer with, for the helpers shared with them.
        self.reply: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        name = json.loads(request.content)["response_format"]["json_schema"]["name"]
        if name == "CompactReading":
            self.calls += 1
            if self.reply:
                return completion({"lines": self.reply})
            return completion({"lines": self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]})
        if name == "GeneratedAnswer":
            return completion({"text": "Look at each side."})
        return completion({"level": 1, "text": "Think about what the multiplication means.", "references_step": "x"})


@pytest.fixture
def model() -> Any:
    with respx.mock:
        readings = Readings()
        respx.post(CHAT).mock(side_effect=readings)
        yield readings


@pytest.fixture
def client(settings: Any) -> TestClient:
    tuned = settings.model_copy(update={"confirm_alerts": True, "confirm_in_parallel": True})
    return TestClient(create_app(build_services(tuned)))


@pytest.fixture
def accounts_client(tmp_path: Any) -> TestClient:
    return TestClient(create_app(build_services(account_settings(tmp_path).model_copy(update={"confirm_alerts": False}))))


def line(math: str, continues: str = "yes") -> dict[str, Any]:
    return compact(math, continues_previous=continues)


def ask(client: TestClient, ink: list[dict[str, Any]], selected: list[str], action: str = "check", **fields: Any) -> dict[str, Any]:
    body = {"sessionId": "page", "action": action, "image": png(), "inkLines": ink, "selectedLineIds": selected} | fields
    response = client.post("/api/ask", json=body)
    assert response.status_code == 200, response.text
    return response.json()


DRAWN = [{"id": "above", "inImage": True}, {"id": "circled", "inImage": True}]


class TestPartOfALine:
    @pytest.mark.parametrize("action", ["check", "hint", "explain"])
    def test_a_fragment_is_never_called_anything_and_costs_nothing(self, client: TestClient, model: Readings, action: str) -> None:
        model.pages = [[line("2x + 3x + 1", "no"), line("5x")]]
        reply = ask(client, DRAWN, ["circled"], action, partialLineIds=["circled"])
        assert reply["verdict"] == "unchecked"
        assert "only part of a line" in reply["text"]
        assert model.calls == 0

    def test_all_but_the_end_of_a_right_sum_isnt_a_wrong_sum(self, client: TestClient, model: Readings) -> None:
        model.pages = [[line("3 * 4 = 1", "no")]]
        reply = ask(client, [{"id": "circled", "inImage": True}], ["circled"], "question", question="Is this ok?", partialLineIds=["circled"])
        assert reply["verdict"] == "unchecked"


class TestBothReadingsHaveToSaySo:
    def test_a_second_reading_of_no_use_cant_confirm_an_accusation(self, client: TestClient, model: Readings) -> None:
        # The first ask leaves the circled line remembered and the line above it not.
        model.pages = [
            [line("2x + 3x", "no"), line("5x")], [line("2x + 8x", "no"), line("5x")],
            [line("2x + 8x", "no")], [line("2x +", "no"), line("3x")],
        ]
        ask(client, DRAWN, ["circled"])
        reply = ask(client, [DRAWN[0], {"id": "circled", "inImage": False}], ["circled"], image=png((41, 20)))
        assert reply["verdict"] != "incorrect"

    def test_a_disputed_line_above_can_make_it_neither(self, client: TestClient, model: Readings) -> None:
        model.pages = [[line("2x + 3x", "no"), line("5x")], [line("2x + 8x", "no"), line("5x")]]
        assert ask(client, DRAWN, ["circled"])["verdict"] == "unclear"

    def test_the_other_way_round_too(self, client: TestClient, model: Readings) -> None:
        model.pages = [[line("2x + 8x", "no"), line("5x")], [line("2x + 3x", "no"), line("5x")]]
        assert ask(client, DRAWN, ["circled"])["verdict"] == "unclear"

    def test_when_they_agree_it_can_still_be_right(self, client: TestClient, model: Readings) -> None:
        model.pages = [[line("2x + 3x", "no"), line("5x")]]
        assert ask(client, DRAWN, ["circled"])["verdict"] == "correct"


class TestHelpEachTimeItComesBack:
    @pytest.mark.parametrize("helped", ["hint", "explain"])
    def test_help_the_second_time_round_is_still_help(self, accounts_client: TestClient, model: Readings, helped: str) -> None:
        student = Student(accounts_client)
        mistake = student.wrong(model, "3 * 4 = 10")
        getattr(student, helped)(mistake)
        student.fix(model, "3 * 4 = 12", mistake)
        student.check(model, "3 * 4 = 10")
        assert not student.event("mistake_detected", mistake)["refused"]
        getattr(student, helped)(mistake)
        student.fix(model, "3 * 4 = 12", mistake)
        detail = accounts_client.get(f"/api/mistakes/{mistake}", headers=student.headers).json()
        assert detail["independent"] is False


class TestRecapCounts:
    def test_what_was_only_ever_misread_doesnt_recur(self, accounts_client: TestClient, model: Readings) -> None:
        student = Student(accounts_client)
        store = accounts_client.app.state.services.accounts.store
        user = accounts_client.get("/api/profile", headers=student.headers).json()["userId"]
        mistake = student.wrong(model, "3 * 4 = 10")
        student.event("mistake_invalidated", mistake, reason="misread")
        assert store.notebook_mistakes_before(user, "another-session", 0) == []


def recap_with(**counts: int) -> Recap:
    glance = RecapGlance(**({
        "found": 0, "corrected": 0, "unresolved": 0, "independent": 0, "assisted": 0, "hints": 0, "explanations": 0,
        "erased": 0, "tried_again": 0, "earlier_corrected": 0,
    } | counts))
    return Recap(session_id="s", started_at=0, finished_at=1, finish_reason="finished", glance=glance, topics=[], observations=[], recommendation=None)


class TestRecapWording:
    def test_nothing_at_all_claims_nothing(self) -> None:
        recap = phrased(recap_with(), 0)
        assert recap.note is None
        assert recap.observations == []

    def test_a_clean_page_says_so(self) -> None:
        assert "No particular trouble spot" in (phrased(recap_with(), 2).note or "")

    @pytest.mark.parametrize("field,count", [("erased", 2), ("tried_again", 3)])
    def test_several_arent_spoken_of_as_one(self, field: str, count: int) -> None:
        for sentence in phrased(recap_with(found=count, **{field: count}), 1).left_open:
            assert " it was " not in sentence
            assert "that try" not in sentence
            assert str(count) in sentence

    @pytest.mark.parametrize("field,count", [("erased", 1), ("tried_again", 1)])
    def test_one_is_spoken_of_as_one(self, field: str, count: int) -> None:
        sentences = phrased(recap_with(found=count, **{field: count}), 1).left_open
        assert sentences
        assert not any(" 1 " in sentence for sentence in sentences)

    def test_a_recap_read_later_doesnt_say_today(self) -> None:
        old = recap_with(found=1, unresolved=1).model_copy(
            update={"recommendation": Recommendation(topic="power_rule", label="Power rule", reason="You used a hint on this today, so a similar question could help.")}
        )
        recap = phrased(old, 1)
        assert "today" not in recap.opening
        assert recap.recommendation is not None
        assert recap.recommendation.reason == "You used a hint on this, so a similar question could help."


class TestAnswerAfterItWasShown:
    def test_copying_a_shown_answer_doesnt_count_as_right(self, accounts_client: TestClient, model: Readings) -> None:
        practice = Practice(accounts_client)
        expected = practice.row["expected_answer"]
        accounts_client.post(f"/api/practice/problems/{practice.problem['id']}/reveal", headers=practice.headers)
        assert practice.answer(model, expected)["outcome"] == "in_progress"
        summary = practice.summary()
        assert (summary["correct"], summary["revealed"]) == (0, 1)


class TestRemovingAMistake:
    def test_its_line_goes_from_a_recap_already_written(self, accounts_client: TestClient, model: Readings) -> None:
        student = Student(accounts_client)
        mistake = student.wrong(model, "3 * 4 = 10")
        recap = student.finish()
        assert recap["topics"][0]["topics"][0]["examples"]
        accounts_client.delete(f"/api/mistakes/{mistake}", headers=student.headers)
        after = accounts_client.get(f"/api/sessions/{student.session}/recap", headers=student.headers).json()["recap"]
        assert after["topics"][0]["topics"][0]["examples"] == []
        assert after["glance"]["found"] == 1


def page(*maths: str, continues: str = "unknown", **fields: Any) -> list[Any]:
    return list(verify_lines([LineInput(i, math, continues=continues, **fields) for i, math in enumerate(maths)]).steps)  # type: ignore[arg-type]


def invalid(steps: list[Any]) -> list[int]:
    return [step.line for step in steps if step.status == "invalid"]


class TestIntegralsOverSeveralLines:
    def test_a_wrong_answer_on_the_next_line_is_the_mistake(self) -> None:
        assert invalid(page("∫ 2x dx", "= x^3")) == [1]

    def test_putting_it_right_clears_it_and_isnt_accused(self) -> None:
        assert invalid(page("∫ 2x dx", "= x^3", "= x^2")) == []

    @pytest.mark.parametrize("working", [["∫_1^3 2x dx", "= x^2"], ["∫_0^2 x dx = x^2/2"]])
    def test_the_antiderivative_on_the_way_to_the_limits_isnt_wrong(self, working: list[str]) -> None:
        assert invalid(page(*working)) == []

    def test_a_wrong_value_for_a_definite_integral_still_is(self) -> None:
        assert invalid(page("∫_0^2 x dx = 3")) == [0]

    def test_something_else_with_the_variable_still_is(self) -> None:
        assert invalid(page("∫_0^2 x dx = x^3")) == [0]


class TestATaskComesToAnEnd:
    def test_the_next_exercise_isnt_held_to_it(self) -> None:
        assert invalid(page("Simplify", "3x + 2x = 5x", "2(x + 1) = 6", "2x + 2 = 6", "x = 2")) == []

    def test_a_page_setting_gives_way_to_the_working(self) -> None:
        assert invalid(page("3x + 2x = 10", "5x = 10", "x = 2", page_intent="simplify")) == []

    def test_until_then_it_holds(self) -> None:
        assert invalid(page("Simplify", "3x + 2x = 5")) == [1]

    def test_a_setting_on_its_own_still_counts(self) -> None:
        assert invalid(page("3x + 2x = 5", page_intent="simplify")) == [0]


class TestStackedExercises:
    @pytest.mark.parametrize(
        "working",
        [["2(x+1) = 6", "3(x+2) = 15"], ["4x + 2 = 10", "2x + 3 = 9"], ["(x+1)(x+2) = 0", "(x+3)(x+4) = 0"]],
    )
    def test_a_label_saying_they_join_doesnt_make_them_a_mistake(self, working: list[str]) -> None:
        assert invalid(page(*working, continues="yes")) == []

    @pytest.mark.parametrize(
        "working",
        [["2(x+1) = 6", "2x+2 = 7"], ["2x + 4 = 10", "x + 2 = 7"], ["2x + 4 = 10", "x + 4 = 5"], ["2(x+1) = 6", "2(x+1) = 7"]],
    )
    def test_a_real_slip_is_still_one(self, working: list[str]) -> None:
        assert invalid(page(*working, continues="yes")) == [1]


class TestNothingEscapes:
    @pytest.mark.parametrize(
        "working",
        [["f(x) = ∫_0^1 1/(x^3 + x + 1) dx"], ["2+2=5", "= ∫_0^1 1/(t^3 + t + 1) dt"]],
    )
    def test_a_root_sympy_cant_place_is_a_refusal(self, working: list[str]) -> None:
        assert page(*working) is not None


class TestAValueThatCouldntBeRead:
    @pytest.mark.parametrize("given", ["Let x = 1", "when x = 1", "x = 1 + 0"])
    def test_it_may_have_been_put_in(self, given: str) -> None:
        assert invalid(page(given, "3x + 2x", "= 5")) == []

    def test_with_nothing_above_it_lost_the_letter(self) -> None:
        assert invalid(page("3x + 2x", "= 5")) == [1]


class TestFractionsUnderSimplify:
    @pytest.mark.parametrize("line", ["(x^2-1)/(x-1) = x-1", "1/x + 1/y = 2/(x+y)", "(x+1)/x = 1"])
    def test_wrong_ones_are_wrong(self, line: str) -> None:
        assert invalid(page(line, intent="simplify")) == [0]

    @pytest.mark.parametrize("line", ["(x^2-1)/(x-1) = x+1", "1/x + 1/y = (x+y)/(x*y)", "1/x = 2", "x/2 = 3"])
    def test_right_or_nothing_to_do_arent(self, line: str) -> None:
        assert invalid(page(line, intent="simplify")) == []


class TestHintsDontTellStories:
    @pytest.mark.parametrize(
        "text",
        ["You also forgot the sign.", "It seems that you forgot the sign.", "You must have forgotten the sign.", "You made a sign error.", "You didn't flip the sign."],
    )
    def test_each_is_refused(self, text: str) -> None:
        assert CLAIMS_WHAT_THE_STUDENT_DID.search(text)


class TestHintsDontWait:
    def test_a_prepared_hint_is_quick_and_asks_no_model(self, accounts_client: TestClient, model: Readings) -> None:
        student = Student(accounts_client)
        mistake = student.wrong(model, "3 * 4 = 10")
        calls = model.calls
        started = time.perf_counter()
        response = accounts_client.post(
            "/api/hint",
            json={"sessionId": student.page, "mistakeId": mistake, "level": 1, "studySessionId": student.session},
            headers=student.headers,
        )
        assert response.status_code == 200, response.text
        assert time.perf_counter() - started < 0.5
        assert response.json()["source"] == "template"
        assert model.calls == calls


class TestSlipsAroundBrackets:
    @pytest.mark.parametrize(
        "working",
        [
            ["2(x+1) + 3 = 9", "2(x+1) = 12"],
            ["3(x-2) - 4 = 5", "3(x-2) = 1"],
            ["(x+1)^2 + 1 = 5", "(x+1)^2 = 6"],
            ["2(x+1) + 3(x+2) = 10", "2(x+1) + 3x + 2 = 10"],
            ["2(x+1) = 6", "2x+1 = 6"],
            ["3(x+2) = 12", "x+2 = 5"],
        ],
    )
    def test_each_is_still_flagged(self, working: list[str]) -> None:
        assert invalid(page(*working, continues="yes")) == [1]

    @pytest.mark.parametrize("working", [["2(x+1) = 6", "x + 5 = 9"], ["2x + 3 = 7", "4(x+1) = 20"]])
    def test_brackets_on_one_line_dont_join_two_exercises(self, working: list[str]) -> None:
        assert invalid(page(*working, continues="yes")) == []


class TestAHeadingOverSeveralExercises:
    @pytest.mark.parametrize(
        "working", [["Simplify", "3x + 2x = 5x", "4x + x = 6x"], ["Expand", "2(x+1) = 2x+2", "3(x+1) = 3x+1"]]
    )
    def test_it_still_holds_for_the_next_answer(self, working: list[str]) -> None:
        assert invalid(page(*working)) == [2]


class TestASlipBelowWhatTheWorkingSettled:
    def test_with_a_setting(self) -> None:
        assert invalid(page("3x + 2x = 10", "5x = 10", "x = 3", continues="yes", page_intent="simplify")) == [2]

    def test_with_a_heading(self) -> None:
        assert invalid(page("Simplify", "3x + 2x = 10", "5x = 10", "x = 3", continues="yes")) == [3]


class TestOnTheWayThere:
    def test_a_running_total_over_two_lines(self) -> None:
        assert invalid(page("5 + 3 = 8 - 2", "= 6")) == []

    def test_a_false_sum_still_is_one(self) -> None:
        assert invalid(page("5 + 3 = 9")) == [0]

    def test_a_letter_with_a_value_from_an_equation_above(self) -> None:
        assert invalid(page("x + 1 = 2", "3x + 2x", "= 5")) == []


class TestATotalCarriedDown:
    @pytest.mark.parametrize(
        "working",
        [["12 / 4 = 3", "+ 2 = 5"], ["20 / 4 = 5", "- 2 = 3"], ["3 + 4 = 7", "+ 2 = 9", "- 1 = 8"]],
    )
    def test_the_right_total_isnt_accused(self, working: list[str]) -> None:
        assert invalid(page(*working)) == []

    def test_the_wrong_total_still_is_wrong(self) -> None:
        assert invalid(page("12 / 4 = 3", "+ 2 = 6")) == [1]

    def test_alone_on_a_page_it_says_what_it_says(self) -> None:
        assert invalid(page("- 2 = 3")) == [0]

    def test_a_new_exercise_carries_nothing(self) -> None:
        assert invalid(page("12 / 4 = 3", "+ 2 = 5", continues="no")) == [1]
