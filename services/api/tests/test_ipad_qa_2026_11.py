"""The physical iPad session of 2026-11-02, as sequences of checks through the whole backend.

Meta's API is mocked with readings the test chooses, including the failures in the session's ledger
(second readings that ran out of tokens or time). This shows what the pipeline does with a reading.
It says nothing about how well the real model reads handwriting.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from quack.config import Settings
from quack.main import build_services, create_app
from tests.test_line_memory import check, compact
from tests.test_pipeline import CHAT, completion, png


def truncated() -> httpx.Response:
    body = {
        "id": "t", "object": "chat.completion", "created": 0, "model": "muse-spark-1.3",
        "choices": [{"index": 0, "finish_reason": "length", "message": {"role": "assistant", "content": ""}}],
        "usage": {"prompt_tokens": 2327, "completion_tokens": 4000, "total_tokens": 6327,
                  "completion_tokens_details": {"reasoning_tokens": 3998}},
    }
    return httpx.Response(200, json=body)


class ByEffort:
    """Readings by reasoning effort, as the two readings run at different ones."""

    def __init__(self) -> None:
        self.minimal: list[list[dict[str, Any]]] = []
        self.low: list[list[dict[str, Any]] | str] = []
        self.calls: list[str] = []

    def say(self, *lines: dict[str, Any]) -> None:
        self.minimal, self.low = [list(lines)], [list(lines)]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        effort = body["reasoning_effort"]
        self.calls.append(effort)
        queue: list[Any] = self.low if effort == "low" else self.minimal
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if answer == "truncated":
            return truncated()
        if answer == "timeout":
            raise httpx.ReadTimeout("slow")
        return completion({"lines": answer})


@pytest.fixture
def client(settings: Settings) -> TestClient:
    tuned = settings.model_copy(update={"confirm_alerts": True, "confirm_in_parallel": True, "env": "development"})
    return TestClient(create_app(build_services(tuned)))


@pytest.fixture
def model() -> Any:
    with respx.mock:
        answer = ByEffort()
        respx.post(CHAT).mock(side_effect=answer)
        yield answer


DERIVATIVE_C = "d/dx(x^3) = 3x^2 + C"
INTEGRAL_2 = "∫ x dx = 2"
SINE = "d/dx(sin(x)) = sin(x)"
REPORTED = [DERIVATIVE_C, INTEGRAL_2, SINE, "d/dx(sin x) = sin x", "∫ x dx = 2x"]


def line(math: str, **overrides: Any) -> dict[str, Any]:
    return compact(math, operation="other", continues_previous="no") | overrides


class TestSecondReadingRanOutOfRoom:
    """All six failed calls in the session were second readings: three truncated, three timed out."""

    @pytest.mark.parametrize("math", REPORTED)
    @pytest.mark.parametrize("failure", ["truncated", "timeout"])
    def test_one_check_still_finds_the_mistake(self, client: TestClient, model: ByEffort, math: str, failure: str) -> None:
        model.minimal, model.low = [[line(math)]], [failure]
        reply = check(client, 1, [("a", True)])
        assert reply["status"] == "confirmed_error", reply
        assert reply["modelIssue"] is None
        assert reply["intervention"]["shouldQuack"] is True
        assert reply["mistakes"][0]["lineId"] == "a"

    def test_it_is_still_two_readings(self, client: TestClient, model: ByEffort) -> None:
        model.minimal, model.low = [[line(INTEGRAL_2)], [line("∫ x dx = 2x")]], ["truncated"]
        reply = check(client, 1, [("a", True)])
        assert reply["status"] == "uncertain"
        assert reply["mistakes"] == []
        assert reply["reason"] == "readings_disagree"

    def test_a_shaky_stand_in_says_nothing(self, client: TestClient, model: ByEffort) -> None:
        """A stand-in reading is the same effort as the first, so it gets no benefit of the doubt."""

        model.minimal, model.low = [[line(SINE)], [line(SINE, legibility="unsure")]], ["truncated"]
        reply = check(client, 1, [("a", True)])
        assert reply["mistakes"] == []

    def test_a_correct_line_is_never_accused(self, client: TestClient, model: ByEffort) -> None:
        model.minimal, model.low = [[line("d/dx(sin(x)) = cos(x)")]], ["truncated"]
        reply = check(client, 1, [("a", True)])
        assert reply["status"] == "no_error"
        assert reply["mistakes"] == []

    def test_the_same_image_doesnt_pay_for_the_same_failure_again(self, client: TestClient, model: ByEffort) -> None:
        """At 02:02:18 and 02:02:40 the same reading of the same image ran out twice, at about 2c each."""

        model.minimal, model.low = [[line(SINE)], "truncated"], ["truncated"]
        for _ in range(2):
            image = png(size=(77, 20))
            response = client.post(
                "/api/analyze",
                json={"sessionId": "same", "snapshotVersion": 1, "image": image,
                      "inkLines": [{"id": "a", "inImage": True}], "attempt": 0},
            )
            assert response.status_code == 200
        assert model.calls.count("low") == 1

    def test_when_nothing_can_read_it_it_says_so_and_accuses_nobody(self, client: TestClient, model: ByEffort) -> None:
        model.minimal, model.low = [[line(SINE)], "truncated"], ["truncated"]
        reply = check(client, 1, [("a", True)])
        assert reply["status"] == "uncertain"
        assert reply["mistakes"] == []
        assert reply["modelIssue"]["category"] == "truncated"

    def test_circling_has_the_same_way_out(self, client: TestClient, model: ByEffort) -> None:
        model.minimal, model.low = [[line(SINE)]], ["truncated"]
        response = client.post(
            "/api/ask",
            json={"sessionId": "s", "action": "check", "image": png(size=(50, 20)),
                  "inkLines": [{"id": "a", "inImage": True}], "selectedLineIds": ["a"]},
        )
        assert response.json()["verdict"] == "incorrect"


class TestOneReadingIsVaguelyUnsure:
    """Written out in `_reconciled` for sums alone: one sure reading and one vague doubt was agreement."""

    @pytest.mark.parametrize("math", REPORTED)
    def test_a_calculus_claim_that_stands_alone_is_treated_like_a_sum(self, client: TestClient, model: ByEffort, math: str) -> None:
        model.minimal, model.low = [[line(math)]], [[line(math, legibility="unsure")]]
        reply = check(client, 1, [("a", True)])
        assert reply["status"] == "confirmed_error", reply
        assert reply["settledLineIds"] == ["a"]

    def test_a_named_other_reading_still_counts(self, client: TestClient, model: ByEffort) -> None:
        model.minimal, model.low = [[line(INTEGRAL_2)]], [[line(INTEGRAL_2, legibility="unsure", alternatives=["∫ x dx = x^2/2"])]]
        reply = check(client, 1, [("a", True)])
        assert reply["mistakes"] == []

    def test_two_unsure_readings_still_say_nothing(self, client: TestClient, model: ByEffort) -> None:
        model.say(line(SINE, legibility="unsure"))
        reply = check(client, 1, [("a", True)])
        assert reply["mistakes"] == []

    def test_different_words_still_say_nothing(self, client: TestClient, model: ByEffort) -> None:
        model.minimal, model.low = [[line(SINE)]], [[line("d/dx(sin(x)) = cos(x)", legibility="unsure")]]
        reply = check(client, 1, [("a", True)])
        assert reply["mistakes"] == []

    def test_a_right_answer_gets_no_such_allowance(self, client: TestClient, model: ByEffort) -> None:
        """It's only ever a way for a mistake to be seen. Saying something is fixed still takes two clear."""

        model.say(line(INTEGRAL_2))
        reply = check(client, 1, [("a", True)])
        model.minimal, model.low = [[line("∫ x dx = x^2/2 + C")]], [[line("∫ x dx = x^2/2 + C", legibility="unsure")]]
        again = check(client, 2, [("b", True)], known=[reply["mistakes"][0]["id"]], mistakeLineIds={reply["mistakes"][0]["id"]: ["b"]})
        assert again["resolvedMistakeIds"] == []

    def test_algebra_is_left_as_it_was(self, client: TestClient, model: ByEffort) -> None:
        """`3x + 2x = 5` depends on what the line's doing, so it's not a claim that stands alone."""

        model.minimal, model.low = [[line("3x + 2x = 5", operation="simplify")]], [[line("3x + 2x = 5", legibility="unsure", operation="simplify")]]
        reply = check(client, 1, [("a", True)], taskContext="simplify")
        assert reply["mistakes"] == []


def flag(client: TestClient, model: ByEffort, math: str, line_id: str = "a", version: int = 1) -> str:
    model.say(line(math))
    reply = check(client, version, [(line_id, True)])
    assert reply["status"] == "confirmed_error", reply
    return reply["mistakes"][0]["id"]


class TestEditingAFlaggedLine:
    """Regression cases A to D. The new ink has a new id, as the id is made from the strokes."""

    @pytest.mark.parametrize(
        ("wrong", "right"),
        [
            (DERIVATIVE_C, "d/dx(x^3) = 3x^2"),
            ("∫ x dx = 2x", "∫ x dx = x^2/2 + C"),
            (SINE, "d/dx(sin(x)) = cos(x)"),
            ("d/dx(sin x) = sin x", "d/dx(sin x) = cos x"),
        ],
    )
    def test_wrong_to_right_resolves_it(self, client: TestClient, model: ByEffort, wrong: str, right: str) -> None:
        known = flag(client, model, wrong)
        model.say(line(right))
        reply = check(client, 2, [("b", True)], known=[known], mistakeLineIds={known: ["b"]})
        assert reply["resolvedMistakeIds"] == [known]
        assert reply["mistakes"] == []
        assert reply["verifiedLineIds"] == ["b"]

    def test_wrong_to_different_wrong_replaces_it(self, client: TestClient, model: ByEffort) -> None:
        known = flag(client, model, INTEGRAL_2)
        model.say(line("∫ x dx = 2x"))
        reply = check(client, 2, [("b", True)], known=[known], mistakeLineIds={known: ["b"]})
        assert reply["resolvedMistakeIds"] == [known]
        assert [m["currentStep"] for m in reply["mistakes"]] == ["2x"]
        assert reply["mistakes"][0]["isNew"] is True
        assert reply["mistakes"][0]["errorType"] == "integral_exponent"

    def test_an_unsure_line_elsewhere_doesnt_hold_up_a_fix(self, client: TestClient, model: ByEffort) -> None:
        """A busy page used to need every line on it clear before any one mistake could be fixed."""

        known = flag(client, model, DERIVATIVE_C)
        other = line("2 + 1 = 3", legibility="unsure")
        model.say(line("d/dx(x^3) = 3x^2"), other)
        reply = check(client, 2, [("b", True), ("c", True)], known=[known], mistakeLineIds={known: ["b"]})
        assert reply["resolvedMistakeIds"] == [known]

    def test_an_unsure_line_elsewhere_doesnt_leave_the_old_mistake_beside_the_new(self, client: TestClient, model: ByEffort) -> None:
        known = flag(client, model, INTEGRAL_2)
        model.say(line("∫ x dx = 2x"), line("2 + 1 = 3", legibility="unsure"))
        reply = check(client, 2, [("b", True), ("c", True)], known=[known], mistakeLineIds={known: ["b"]})
        assert reply["resolvedMistakeIds"] == [known]
        assert len(reply["mistakes"]) == 1

    def test_the_lines_own_doubt_still_holds_it(self, client: TestClient, model: ByEffort) -> None:
        known = flag(client, model, DERIVATIVE_C)
        model.say(line("d/dx(x^3) = 3x^2", legibility="unsure"))
        reply = check(client, 2, [("b", True)], known=[known], mistakeLineIds={known: ["b"]})
        assert reply["resolvedMistakeIds"] == []

    def test_a_line_above_it_can_depend_on_holds_it_too(self, client: TestClient, model: ByEffort) -> None:
        known = flag(client, model, DERIVATIVE_C)
        model.say(line("2x + 3", legibility="unsure"), line("= 2x + 3", continues_previous="yes"))
        reply = check(client, 2, [("c", True), ("b", True)], known=[known], mistakeLineIds={known: ["b"]})
        assert reply["resolvedMistakeIds"] == []

    def test_one_reading_of_a_fix_isnt_enough(self, client: TestClient, model: ByEffort) -> None:
        known = flag(client, model, DERIVATIVE_C)
        model.minimal, model.low = [[line("d/dx(x^3) = 3x^2")]], [[line(DERIVATIVE_C)]]
        reply = check(client, 2, [("b", True)], known=[known], mistakeLineIds={known: ["b"]})
        assert reply["resolvedMistakeIds"] == []

    @pytest.mark.parametrize(
        "unfinished",
        [line("∫ x dx =", kind="incomplete"), line("∫ x dx"), line("∫ x dx =")],
        ids=["incomplete", "no_claim", "dangling"],
    )
    def test_erasing_the_answer_is_no_fix(self, client: TestClient, model: ByEffort, unfinished: dict[str, Any]) -> None:
        """Case D. With nothing claimed, nothing has been put right."""

        known = flag(client, model, INTEGRAL_2)
        model.say(unfinished)
        reply = check(client, 2, [("b", True)], known=[known], mistakeLineIds={known: ["b"]})
        assert reply["resolvedMistakeIds"] == []
        assert reply["mistakes"] == []

    def test_a_new_problem_nearby_leaves_the_old_mistake_alone(self, client: TestClient, model: ByEffort) -> None:
        """Case F. Its line isn't touched, and it's still wrong."""

        known = flag(client, model, INTEGRAL_2)
        model.say(line(INTEGRAL_2), line("2 + 1 = 1"))
        reply = check(client, 2, [("a", True), ("c", True)], known=[known], mistakeLineIds={known: ["a"]})
        assert reply["resolvedMistakeIds"] == []
        assert sorted(m["lineId"] for m in reply["mistakes"]) == ["a", "c"]
        assert [m["isNew"] for m in reply["mistakes"] if m["lineId"] == "a"] == [False]

    def test_without_saying_which_line_it_is_it_stays_as_careful_as_before(self, client: TestClient, model: ByEffort) -> None:
        known = flag(client, model, DERIVATIVE_C)
        model.say(line("d/dx(x^3) = 3x^2"), line("2 + 1 = 3", legibility="unsure"))
        reply = check(client, 2, [("b", True), ("c", True)], known=[known])
        assert reply["resolvedMistakeIds"] == []

    def test_undoing_a_fix_brings_it_back_from_memory_at_no_cost(self, client: TestClient, model: ByEffort) -> None:
        """Case H."""

        known = flag(client, model, DERIVATIVE_C)
        model.say(line("d/dx(x^3) = 3x^2"))
        check(client, 2, [("b", True)], known=[known], mistakeLineIds={known: ["b"]})
        calls = len(model.calls)
        reply = check(client, 3, [("a", False)])
        assert [m["id"] for m in reply["mistakes"]] == [known]
        assert len(model.calls) == calls


class TestAutomaticAndCircledAgree:
    @pytest.mark.parametrize(
        ("math", "verdict", "status"),
        [
            (DERIVATIVE_C, "incorrect", "confirmed_error"),
            (INTEGRAL_2, "incorrect", "confirmed_error"),
            ("∫ x dx = 2x", "incorrect", "confirmed_error"),
            (SINE, "incorrect", "confirmed_error"),
            ("d/dx(x^3) = 3x^2", "correct", "no_error"),
            ("∫ x dx = x^2/2 + C", "correct", "no_error"),
            ("d/dx(sin(x)) = cos(x)", "correct", "no_error"),
            ("2 + 1 = 1", "incorrect", "confirmed_error"),
            ("3 * 4 = 12", "correct", "no_error"),
        ],
    )
    def test_the_same_reading_gets_the_same_answer(self, client: TestClient, model: ByEffort, math: str, verdict: str, status: str) -> None:
        model.say(line(math))
        automatic = check(client, 1, [("a", True)])
        manual = check(client, 2, [("a", True)], trigger="manual")
        circled = client.post(
            "/api/ask",
            json={"sessionId": "other", "action": "check", "image": png(size=(60, 20)),
                  "inkLines": [{"id": "a", "inImage": True}], "selectedLineIds": ["a"]},
        ).json()
        assert automatic["status"] == manual["status"] == status
        assert circled["verdict"] == verdict
        if status == "confirmed_error":
            assert automatic["mistakes"][0]["id"] == manual["mistakes"][0]["id"]

    def test_circling_records_nothing(self, client: TestClient, model: ByEffort) -> None:
        model.say(line(SINE))
        for action in ("check", "explain", "hint"):
            client.post(
                "/api/ask",
                json={"sessionId": "s", "action": action, "image": png(size=(60, 20)),
                      "inkLines": [{"id": "a", "inImage": True}], "selectedLineIds": ["a"]},
            )
        reply = check(client, 1, [("a", False)])
        assert reply["intervention"]["shouldQuack"] is True


class TestDiagnosticTimeline:
    def test_says_what_became_of_a_check(self, client: TestClient, model: ByEffort) -> None:
        model.minimal, model.low = [[line(SINE)]], ["truncated"]
        check(client, 1, [("a", True)], requestId="r1")
        trace = client.get("/api/dev/trace/s").json()[-1]
        assert trace["requestId"] == "r1"
        assert trace["trigger"] == "auto"
        assert trace["drawnLineIds"] == ["a"]
        assert trace["secondReading"] == "stand_in_after_truncated"
        assert trace["newMistakeIds"]
        assert trace["imageDigest"] and "data:" not in trace["imageDigest"]

    def test_circling_is_on_it_too(self, client: TestClient, model: ByEffort) -> None:
        model.say(line(SINE))
        client.post(
            "/api/ask",
            json={"sessionId": "s", "action": "check", "image": png(size=(60, 20)),
                  "inkLines": [{"id": "a", "inImage": True}], "selectedLineIds": ["a"]},
        )
        trace = client.get("/api/dev/trace/s").json()[-1]
        assert trace["trigger"] == "circle"
        assert trace["status"] == "incorrect"


def asked(client: TestClient, action: str, question: str | None = None) -> dict[str, Any]:
    response = client.post(
        "/api/ask",
        json={"sessionId": "s", "action": action, "question": question, "image": png(size=(60, 20)),
              "inkLines": [{"id": "a", "inImage": True}], "selectedLineIds": ["a"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestWhatEachCircledActionIsFor:
    def test_explain_on_the_reported_integral_teaches_it(self, client: TestClient, model: ByEffort) -> None:
        model.say(line("∫ x dx = x^2/2 + C"))
        reply = asked(client, "explain")
        text = reply["text"]
        assert reply["verdict"] == "correct"
        assert "holds up" not in text
        # As short steps since the second iPad session, in which a paragraph of this was too much to read.
        for idea in ("Raise the power by one", "new power", "+ C", "constant", "derivative of x^2/2 + C is x"):
            assert idea in text, text
        assert reply["explanation"]["title"] == "The power rule for integrals"
        assert len(reply["explanation"]["steps"]) == 3
        assert reply["source"] == "template"

    def test_check_on_it_says_whether_it_holds(self, client: TestClient, model: ByEffort) -> None:
        model.say(line("∫ x dx = x^2/2 + C"))
        text = asked(client, "check")["text"]
        assert "holds up" in text
        assert "undoes" not in text

    @pytest.mark.parametrize(
        ("math", "idea"),
        [
            ("d/dx(x^3) = 3x^2", "power rule"),
            ("d/dx(sin(x)) = cos(x)", "slope of the sine curve"),
            ("d/dx(sin x) = cos x", "slope of the sine curve"),
            ("d/dx(cos(x)) = -sin(x)", "minus sign"),
            ("d/dx(sin(2x)) = 2cos(2x)", "chain rule"),
            ("d/dx(x sin(x)) = sin(x) + x cos(x)", "product rule"),
            ("d/dx(sin(x)/x) = cos(x)/x - sin(x)/x^2", "quotient rule"),
            ("d/dx(x^2 + 3x) = 2x + 3", "one term at a time"),
            ("∫ cos(x) dx = sin(x) + C", "Differentiating sin(x) gives cos(x)"),
            ("∫ 2x dx = x^2 + C", "Raise the power by one"),
            ("∫ 3 dx = 3x + C", "constant"),
            ("3 * 4 = 12", "3 lots of 4"),
            ("1 / 4 = 0.25", "multiplied by 4"),
        ],
    )
    def test_explain_names_the_rule(self, client: TestClient, model: ByEffort, math: str, idea: str) -> None:
        model.say(line(math))
        reply = asked(client, "explain")
        assert reply["verdict"] == "correct", reply
        assert idea in reply["text"]
        assert "holds up" not in reply["text"]

    def test_explain_on_wrong_working_teaches_the_rule_and_says_so(self, client: TestClient, model: ByEffort) -> None:
        model.say(line(SINE))
        reply = asked(client, "explain")
        assert reply["verdict"] == "incorrect"
        assert "slope of the sine curve" in reply["text"]
        assert "cos(x), not sin(x)" in reply["text"]

    def test_explain_asserts_nothing_about_what_it_cant_read(self, client: TestClient, model: ByEffort) -> None:
        model.say(line("∫ x dx = x^2/2 + C", legibility="unsure"))
        reply = asked(client, "explain")
        assert reply["verdict"] == "unclear"
        assert "undoes" not in reply["text"]
        assert "can't read" in reply["text"]

    def test_explain_on_a_question_with_no_answer_gives_the_rule_and_no_result(self, client: TestClient, model: ByEffort) -> None:
        model.say(line("∫ x^3 dx"))
        reply = asked(client, "explain")
        assert "Raise the power by one" in reply["text"]
        assert "x^4" not in reply["text"]

    def test_a_free_question_goes_to_the_model_with_only_the_selected_working(self, client: TestClient, model: ByEffort) -> None:
        answers: list[dict[str, Any]] = []
        real = model.__call__

        def spy(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if body["response_format"]["json_schema"]["name"] == "GeneratedAnswer":
                answers.append(body)
                return completion({"text": "The power goes up by one."})
            return real(request)

        respx.post(CHAT).mock(side_effect=spy)
        model.say(line("∫ x dx = x^2/2 + C"))
        reply = asked(client, "question", "Why is it over 2?")
        facts = json.loads(answers[0]["messages"][1]["content"])
        assert reply["text"] == "The power goes up by one."
        assert facts["working"] == ["∫ x dx = x^2/2 + C"]
        assert facts["finding"] == "correct"


class TestHintsSayWhatTheRuleIs:
    @staticmethod
    def hints(client: TestClient, model: ByEffort, math: str) -> list[str]:
        known = flag(client, model, math)
        session = "s"
        hints = [client.post("/api/hint", json={"sessionId": session, "mistakeId": known, "level": level}).json()["text"] for level in (1, 2)]
        hints.append(client.post("/api/explain", json={"sessionId": session, "mistakeId": known}).json()["text"])
        return hints

    def test_the_sine_derivative_goes_rule_then_slope_then_answer(self, client: TestClient, model: ByEffort) -> None:
        first, second, third = self.hints(client, model, SINE)
        assert first == "Which derivative rule applies to sin(x)?"
        assert second == "Think about how the slope of the sine curve relates to cosine."
        assert "cos(x), not sin(x)" in third
        assert "one term" not in first + second + third
        assert "cos(x)" not in first + second

    def test_written_without_brackets_its_the_same(self, client: TestClient, model: ByEffort) -> None:
        first, second, _ = self.hints(client, model, "d/dx(sin x) = sin x")
        assert first == "Which derivative rule applies to sin(x)?"
        assert "sine curve" in second

    def test_the_cosine_derivative_is_about_the_sign(self, client: TestClient, model: ByEffort) -> None:
        first, second, third = self.hints(client, model, "d/dx(cos(x)) = sin(x)")
        assert "cos(x)" in first
        assert "sign" in second
        assert "-sin(x), not sin(x)" in third

    def test_no_model_is_paid_for_any_of_them(self, client: TestClient, model: ByEffort) -> None:
        self.hints(client, model, SINE)
        assert len(model.calls) == 2

    @pytest.mark.parametrize(
        ("math", "word"),
        [
            ("d/dx(sin(2x)) = cos(2x)", "inside"),
            ("d/dx(x sin(x)) = cos(x)", "product"),
            ("d/dx(sin(x)/x) = cos(x)", "quotient"),
            ("d/dx(x^3) = 3x^3", "power"),
            ("d/dx(x^3) = x^2", "power rule"),
            ("∫ x dx = 2", "variable"),
            ("∫ x dx = 2x", "power"),
            ("∫ x^2 dx = x^3", "number in front"),
            ("∫ x^2 dx = 2x", "Differentiate"),
            (DERIVATIVE_C, "constant"),
        ],
    )
    def test_each_kind_has_its_own_first_or_second_hint(self, client: TestClient, model: ByEffort, math: str, word: str) -> None:
        first, second, _ = self.hints(client, model, math)
        assert word in first + second, (first, second)
        assert "one term at a time" not in first + second

    def test_a_sum_still_goes_one_term_at_a_time(self, client: TestClient, model: ByEffort) -> None:
        first, second, _ = self.hints(client, model, "d/dx(x^2 + 2x) = 3x + 3")
        assert "term" in first + second

    def test_no_first_hint_gives_the_answer_away(self, client: TestClient, model: ByEffort) -> None:
        for math in (SINE, "d/dx(cos(x)) = sin(x)", "d/dx(tan(x)) = sin(x)", "d/dx(ln(x)) = x", "d/dx(e^x) = x"):
            first, _, third = self.hints(client, model, math)
            expected = third.split("is ")[-1].split(", not")[0]
            assert expected not in first, (math, first)
            model.calls.clear()


class TestAFixDoesntHaveToBeOnTheSameLine:
    def test_the_next_line_putting_it_right_still_counts(self, client: TestClient, model: ByEffort) -> None:
        known = flag(client, model, INTEGRAL_2)
        model.say(line(INTEGRAL_2), line("∫ x dx = x^2/2 + C"))
        reply = check(client, 2, [("a", True), ("b", True)], known=[known], mistakeLineIds={known: ["a"]})
        assert reply["resolvedMistakeIds"] == [known]

    def test_saying_the_page_is_for_solving_clears_a_simplifying_mistake(self, client: TestClient, model: ByEffort) -> None:
        model.say(line("3x + 2x = 5"))
        known = check(client, 1, [("a", True)], taskContext="simplify")["mistakes"][0]["id"]
        reply = check(client, 2, [("a", False)], known=[known], mistakeLineIds={known: ["a"]}, taskContext="solve")
        assert reply["resolvedMistakeIds"] == [known]


class TestStandInsAreOnlyPaidForOnce:
    def test_a_slow_second_reading_is_raced_and_the_winner_is_kept(self, settings: Settings) -> None:
        tuned = settings.model_copy(update={"confirm_alerts": True, "confirm_in_parallel": True, "second_reading_patience_s": 0.05})
        slow_client = TestClient(create_app(build_services(tuned)))

        efforts: list[str] = []

        async def answer(request: httpx.Request) -> httpx.Response:
            import asyncio

            body = json.loads(request.content)
            efforts.append(body["reasoning_effort"])
            if body["reasoning_effort"] == "low":
                await asyncio.sleep(0.4)
            return completion({"lines": [line(SINE)]})

        with respx.mock:
            respx.post(CHAT).mock(side_effect=answer)
            image = png(size=(71, 20))
            payload = {"sessionId": "s", "snapshotVersion": 1, "image": image, "inkLines": [{"id": "a", "inImage": True}]}
            first = slow_client.post("/api/analyze", json=payload).json()
            assert first["status"] == "confirmed_error"
            assert sorted(efforts) == ["low", "minimal", "minimal"]
            second = slow_client.post("/api/analyze", json=payload | {"snapshotVersion": 2}).json()
            assert second["status"] == "confirmed_error"
            assert sorted(efforts) == ["low", "minimal", "minimal"]

    def test_a_reading_that_doesnt_line_up_can_still_show_a_fix_as_it_did(self, client: TestClient, model: ByEffort) -> None:
        known = flag(client, model, DERIVATIVE_C)
        model.say(line("d/dx(x^3) = 3x^2"))
        reply = check(client, 2, [("b", True), ("c", True)], known=[known], mistakeLineIds={known: ["b"]})
        assert reply["resolvedMistakeIds"] == [known]
        assert [m for m in reply["mistakes"]] == []
