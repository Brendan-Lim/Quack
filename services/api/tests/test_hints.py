"""Hint validation and the hint endpoints, with the model mocked."""

from __future__ import annotations

import asyncio
import io
import json
import time
import wave
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from quack.main import build_services, create_app
from quack.services import hint_engine
from quack.services.hint_engine import _TEMPLATES, PreparedHelp, find_problems, template_text
from quack.services.mistake_manager import TrackedMistake
from tests.test_pipeline import BASE, CHAT, WRONG, analyze, completion, line, png, reading


def tracked(**overrides: Any) -> TrackedMistake:
    fields = {
        "id": "m_1",
        "category": "algebra_expansion",
        "error_type": "incorrect_distribution",
        "operation_type": "expression_equivalence",
        "previous_step": "2(x + 3)",
        "current_step": "2x + 3",
        "expected": "2x + 6",
        "evidence": "difference 3",
    }
    return TrackedMistake(**(fields | overrides))


class TestHintValidation:
    def test_accepts_the_examples_from_the_brief(self) -> None:
        assert find_problems(1, "Check whether you multiplied both terms inside the bracket.", tracked()) == []
        assert find_problems(2, "Look at the 3 inside the bracket. What should the 2 do to it?", tracked()) == []
        assert (
            find_problems(
                3, "Both terms inside the bracket get multiplied by 2. So 2(x + 3) becomes 2x + 6, not 2x + 3.", tracked()
            )
            == []
        )

    def test_hint_cannot_contain_the_answer(self) -> None:
        assert find_problems(1, "It should be 2x + 6.", tracked())
        assert find_problems(2, "It should be 2x+6.", tracked())

    @pytest.mark.parametrize(
        "text",
        [
            "The last term should be six.",
            "The last term should be ６.",
            "It should be $2x+6$.",
            "It should be 2x + 6.",
            "Try 6 + 2x.",
        ],
    )
    def test_spoilers_are_caught_however_they_are_written(self, text: str) -> None:
        assert find_problems(2, text, tracked())

    def test_a_missing_term_counts_as_a_spoiler(self) -> None:
        mistake = tracked(previous_step="(x + 1)^2", current_step="x^2 + 1", expected="x^2 + 2x + 1")
        assert find_problems(2, "The missing piece is the middle term, 2x.", mistake)
        assert find_problems(2, "What about the middle term?", mistake) == []

    def test_stating_the_solution_is_a_spoiler(self) -> None:
        mistake = tracked(previous_step="2x = 4", current_step="x = 8", expected="x = 2")
        assert find_problems(2, "So x equals 2.", mistake)
        assert find_problems(2, "What undoes multiplying by 2?", mistake) == []

    @pytest.mark.parametrize("text", ["So x equals 2.", "x = 2", "x is 2, not 8.", "The answer is 2.", "The solution is two."])
    def test_a_solution_is_recognised_in_a_sentence(self, text: str) -> None:
        mistake = tracked(previous_step="2x = 4", current_step="x = 8", expected="x = 2")
        assert find_problems(2, text, mistake)

    @pytest.mark.parametrize("text", ["So x equals -2.", "x is minus 2.", "The answer is negative two."])
    def test_a_negative_solution_is_a_spoiler(self, text: str) -> None:
        mistake = tracked(previous_step="2x = -4", current_step="x = 2", expected="x = -2")
        assert find_problems(2, text, mistake)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("It should be 3x².", "3x^2 + 2"),
            ("It should be cos x.", "cos(x)"),
            ("x = 0.5", "x = 1/2"),
            ("It should be 6.0.", "x = 6"),
            ("x becomes 2", "x = 2"),
            ("You should get 2.", "x = 2"),
            ("x would be 2", "x = 2"),
            ("x should equal 2", "x = 2"),
            ("It's one.", "x = 1"),
            ("It is 2.", "x = 2"),
            ("2 is the answer.", "x = 2"),
            ("x: 2", "x = 2"),
        ],
    )
    def test_more_ways_to_give_it_away(self, text: str, expected: str) -> None:
        mistake = tracked(previous_step="2x = 4", current_step="x = 8", expected=expected)
        assert find_problems(1, text, mistake)

    @pytest.mark.parametrize(
        "text",
        [
            "Check what rule applies when two functions are multiplied.",
            "Try again in line 4.",
            "There are four things to check.",
            "Check the step is right.",
            "Make sure the result is right.",
        ],
    )
    def test_ordinary_hints_are_allowed(self, text: str) -> None:
        mistake = tracked(previous_step="d/dx(sin(x)cos(x))", current_step="cos(x)", expected="2 + 4")
        assert find_problems(1, text, mistake) == []

    @pytest.mark.parametrize("text", ["Your work is correct.", "That's correct.", "Correct."])
    def test_praise_for_a_wrong_step_is_refused(self, text: str) -> None:
        assert find_problems(1, text, tracked())

    def test_no_solutions_reads_properly(self) -> None:
        mistake = tracked(error_type="solution_set_changed", expected="no real solutions")
        assert "should keep" not in template_text(3, mistake)
        assert find_problems(3, template_text(3, mistake), mistake) == []

    def test_a_longer_number_is_not_the_solution(self) -> None:
        mistake = tracked(previous_step="2x = 4", current_step="x = 8", expected="x = 2")
        assert find_problems(2, "It isn't that x is 2.5, is it?", mistake) == []

    @pytest.mark.parametrize("text", ["Which one changed?", "Did every one get multiplied?", "Look at one side."])
    def test_one_as_a_pronoun_is_not_a_number(self, text: str) -> None:
        mistake = tracked(previous_step="2x = 2", current_step="x = 2", expected="x = 1")
        assert find_problems(2, text, mistake) == []

    def test_one_as_a_value_is_a_spoiler(self) -> None:
        mistake = tracked(previous_step="2x = 2", current_step="x = 2", expected="x = 1")
        assert find_problems(2, "The last term should be one.", mistake)

    def test_reordered_factors_are_a_spoiler(self) -> None:
        mistake = tracked(previous_step="x^2 - 4", current_step="(x-2)^2", expected="(x - 2)(x + 2)")
        assert find_problems(2, "Try (x+2)(x-2).", mistake)

    def test_a_short_answer_does_not_reject_ordinary_hints(self) -> None:
        mistake = tracked(previous_step="d/dx(x^2)/2", current_step="2x", expected="x")
        assert find_problems(1, "Check the x term.", mistake) == []

    def test_hint_cannot_contain_a_value_only_in_the_answer(self) -> None:
        problems = find_problems(2, "The last term should be 6, so look again.", tracked())
        assert any("reveals 6" in problem for problem in problems)

    def test_explanation_must_state_the_verified_result(self) -> None:
        problems = find_problems(3, "It becomes 2x + 5, not 2x + 3.", tracked())
        assert any("correct result" in problem for problem in problems)

    def test_rejects_filler(self) -> None:
        assert find_problems(1, "Great question! Let's dive into brackets.", tracked())

    def test_rejects_claims_that_the_step_is_fine(self) -> None:
        assert find_problems(1, "Your working is correct, carry on.", tracked())

    def test_rejects_rambling(self) -> None:
        assert find_problems(1, "word " * 26, tracked())

    def test_rejects_empty(self) -> None:
        assert find_problems(1, "  ", tracked())

    @pytest.mark.parametrize("error_type", sorted(_TEMPLATES))
    @pytest.mark.parametrize("level", [1, 2])
    def test_no_template_hint_gives_the_answer_away(self, error_type: str, level: int) -> None:
        mistake = tracked(error_type=error_type)
        assert find_problems(level, template_text(level, mistake), mistake) == []

    def test_template_never_says_a_line_becomes_itself(self) -> None:
        mistake = tracked(error_type="sign_error", previous_step="x - 3", current_step="x + 3", expected="x - 3")
        text = template_text(3, mistake)
        assert "becomes" not in text
        assert "x - 3" in text and "x + 3" in text

    def test_division_template_says_division(self) -> None:
        mistake = tracked(error_type="inverse_of_division", previous_step="x/2 = 3", current_step="x = 3/2", expected="x = 6")
        assert "divided" in template_text(2, mistake)
        assert "multiplying" in template_text(3, mistake)

    @pytest.mark.parametrize("error_type", sorted(_TEMPLATES))
    def test_template_explanations_are_valid(self, error_type: str) -> None:
        mistake = tracked(error_type=error_type)
        assert find_problems(3, template_text(3, mistake), mistake) == []


@pytest.fixture
def client(settings):
    return TestClient(create_app(build_services(settings)))


def report_mistake(client: TestClient) -> str:
    return analyze(client)["mistake"]["id"]


def help_reply(text: str, level: int = 1) -> httpx.Response:
    return completion({"level": level, "text": text, "references_step": "2x + 3"})


@pytest.fixture
def worded_by_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mistake with no fixed hint, whose hints the model words when they're asked for."""

    monkeypatch.setattr(hint_engine, "has_prepared_hints", lambda _mistake: False)
    monkeypatch.setattr(hint_engine.PreparedHelp, "prepare", lambda *_args: None)
    # The mocked model answers at once, but a slow test machine shouldn't make this flaky.
    monkeypatch.setattr(hint_engine.PreparedHelp.get, "__defaults__", (30.0,))


class TestInstantHints:
    """A hint must not wait for a model when a verified one is already written."""

    @respx.mock
    @pytest.mark.parametrize(
        "lines, first, second",
        [
            (("2 * 3 = 5",), "Check that multiplication again.", "Try adding 3 twice. Does that give you 5?"),
            (("3 * 4 = 10",), "Check that multiplication again.", "Try adding 4 three times. Does that give you 10?"),
            (("6 / 2 = 2",), "Check that division again.", "Try multiplying your answer by 2. Do you get 6?"),
            (("7 + 5 = 11",), "Check that addition again.", "Try taking 5 away from 11. Do you get 7?"),
            (("d/dx(x^3) = 3x",), None, None),
            (("2(x + 3) = 2x + 3",), None, None),
        ],
    )
    def test_no_model_call_for_either_hint(self, client: TestClient, lines: tuple[str, ...], first: str | None, second: str | None) -> None:
        route = respx.post(CHAT).mock(
            return_value=reading(*(line(math, continues_previous="no", operation="arithmetic") for math in lines))
        )
        mistake_id = report_mistake(client)
        reads = route.call_count
        one = client.post("/api/hint", json={"sessionId": "s1", "mistakeId": mistake_id, "level": 1}).json()
        two = client.post("/api/hint", json={"sessionId": "s1", "mistakeId": mistake_id, "level": 2}).json()
        assert route.call_count == reads
        assert (one["source"], two["source"]) == ("template", "template")
        if first:
            assert (one["text"], two["text"]) == (first, second)

    @respx.mock
    def test_hints_never_give_the_answer(self, client: TestClient) -> None:
        respx.post(CHAT).mock(return_value=reading(line("3 * 4 = 10", continues_previous="no", operation="arithmetic")))
        mistake_id = report_mistake(client)
        for level in (1, 2):
            body = client.post("/api/hint", json={"sessionId": "s1", "mistakeId": mistake_id, "level": level}).json()
            assert "12" not in body["text"]

    @respx.mock
    def test_asking_again_repeats_the_hint(self, client: TestClient, worded_by_model: None) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = report_mistake(client)
        route.mock(return_value=help_reply("Look at the bracket."))
        one = client.post("/api/hint", json={"sessionId": "s1", "mistakeId": mistake_id, "level": 1}).json()
        calls = route.call_count
        again = client.post("/api/hint", json={"sessionId": "s1", "mistakeId": mistake_id, "level": 1}).json()
        assert route.call_count == calls
        assert again["text"] == one["text"]

    def test_every_operation_gets_a_way_to_check_that_gives_nothing_away(self) -> None:
        for op, value in (("*", lambda a, b: a * b), ("+", lambda a, b: a + b), ("-", lambda a, b: a - b)):
            for a in range(0, 25):
                for b in range(0, 25):
                    right = value(a, b)
                    mistake = tracked(
                        error_type="arithmetic_error", category="arithmetic", operation_type="arithmetic",
                        previous_step=f"{a} {op} {b}", current_step=str(right + 1), expected=str(right),
                    )
                    for level in (1, 2):
                        assert find_problems(level, template_text(level, mistake), mistake) == []

    @pytest.mark.parametrize("previous", ["2^3", "sin(pi)", "sqrt(16)", "6"])
    def test_order_of_operations_only_comes_up_where_it_applies(self, previous: str) -> None:
        mistake = tracked(error_type="arithmetic_error", category="arithmetic", previous_step=previous, current_step="5", expected="8")
        assert "multiplying and dividing" not in template_text(2, mistake)

    def test_times_zero_or_one_does_not_state_the_answer(self) -> None:
        for sum_, right in (("0 * 7", "0"), ("1 * 7", "7")):
            mistake = tracked(
                error_type="arithmetic_error", category="arithmetic", previous_step=sum_, current_step="8", expected=right
            )
            assert "What happens when you multiply" in template_text(2, mistake)


class TestPreparedHints:
    """For a mistake with no fixed hint, the model words one as soon as the alert goes up."""

    @staticmethod
    def unnamed() -> TrackedMistake:
        return tracked(error_type="something_without_a_template")

    @staticmethod
    def helper(settings: Any) -> PreparedHelp:
        return PreparedHelp(build_services(settings).router)

    @respx.mock
    async def test_ready_hints_are_served_without_another_call(self, settings: Any) -> None:
        route = respx.post(CHAT).mock(side_effect=[help_reply("Look at the bracket."), help_reply("What about the 3?", 2)])
        help_ = self.helper(settings)
        help_.prepare("s", self.unnamed())
        await asyncio.sleep(0.2)
        assert route.call_count == 2
        assert await help_.get("s", self.unnamed(), 1, 0.01) == ("Look at the bracket.", "model", None)
        assert await help_.get("s", self.unnamed(), 2, 0.01) == ("What about the 3?", "model", None)
        assert route.call_count == 2

    @respx.mock
    async def test_the_second_hint_is_told_about_the_first(self, settings: Any) -> None:
        route = respx.post(CHAT).mock(side_effect=[help_reply("Look at the bracket."), help_reply("What about the 3?", 2)])
        help_ = self.helper(settings)
        help_.prepare("s", self.unnamed())
        await asyncio.sleep(0.2)
        facts = json.loads(json.loads(route.calls.last.request.content)["messages"][1]["content"])
        assert facts["earlier_hints"] == ["Look at the bracket."]

    @respx.mock
    async def test_a_slow_model_doesnt_keep_the_student_waiting(self, settings: Any) -> None:
        async def slowly(_request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(5)
            return help_reply("Too late.")

        respx.post(CHAT).mock(side_effect=slowly)
        help_ = self.helper(settings)
        help_.prepare("s", self.unnamed())
        started = time.perf_counter()
        text, source, _ = await help_.get("s", self.unnamed(), 1, 0.05)
        assert time.perf_counter() - started < 1
        assert source == "template"
        assert text == template_text(1, self.unnamed())

    @respx.mock
    async def test_a_mistake_with_a_fixed_hint_costs_nothing(self, settings: Any) -> None:
        route = respx.post(CHAT).mock(return_value=help_reply("unused"))
        help_ = self.helper(settings)
        help_.prepare("s", tracked())
        await asyncio.sleep(0.05)
        assert route.call_count == 0

    @respx.mock
    async def test_a_prepared_hint_that_gives_away_the_answer_is_replaced(self, settings: Any) -> None:
        respx.post(CHAT).mock(return_value=help_reply("The answer is 2x + 6."))
        help_ = self.helper(settings)
        help_.prepare("s", self.unnamed())
        await asyncio.sleep(0.2)
        text, source, _ = await help_.get("s", self.unnamed(), 1, 0.01)
        assert source == "template"
        assert "2x + 6" not in text


@pytest.mark.usefixtures("worded_by_model")
class TestEndpoints:
    @respx.mock
    def test_hint_uses_the_verified_facts(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = report_mistake(client)
        route.mock(return_value=help_reply("Check whether you multiplied both terms inside the bracket."))
        response = client.post("/api/hint", json={"sessionId": "s1", "mistakeId": mistake_id, "level": 1})
        assert response.status_code == 200
        body = response.json()
        assert {key: body[key] for key in ("level", "text", "source", "modelIssue")} == {
            "level": 1,
            "text": "Check whether you multiplied both terms inside the bracket.",
            "source": "model",
            "modelIssue": None,
        }
        sent = json.loads(route.calls.last.request.content)
        facts = json.loads(sent["messages"][1]["content"])
        assert facts["student_wrote"] == "2x + 3"
        assert facts["previous_step"] == "2(x + 3)"
        assert facts["error_type"] == "incorrect_distribution"
        assert facts["requested_level"] == 1

    @respx.mock
    @pytest.mark.parametrize("level", [1, 2])
    def test_hints_are_never_told_the_answer(self, client: TestClient, level: int) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = report_mistake(client)
        route.mock(return_value=help_reply("Look at the bracket.", level))
        client.post("/api/hint", json={"sessionId": "s1", "mistakeId": mistake_id, "level": level})
        sent = route.calls.last.request.content.decode()
        assert "2x + 6" not in sent
        assert "correct_result" not in json.loads(sent)["messages"][1]["content"]

    @respx.mock
    def test_only_the_explanation_gets_the_verified_result(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = report_mistake(client)
        route.mock(return_value=help_reply("Both terms. It becomes 2x + 6, not 2x + 3.", 3))
        client.post("/api/explain", json={"sessionId": "s1", "mistakeId": mistake_id})
        facts = json.loads(json.loads(route.calls.last.request.content)["messages"][1]["content"])
        assert facts["correct_result"] == "2x + 6"

    @respx.mock
    def test_spoiler_is_retried_then_replaced(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = report_mistake(client)
        route.mock(return_value=help_reply("The answer is 2x + 6."))
        before = route.call_count
        body = client.post("/api/hint", json={"sessionId": "s1", "mistakeId": mistake_id, "level": 1}).json()
        assert route.call_count == before + 2
        assert body["source"] == "template"
        assert "6" not in body["text"]

    @respx.mock
    def test_rejection_reason_is_fed_back(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = report_mistake(client)
        route.mock(side_effect=[help_reply("The answer is 2x + 6."), help_reply("Look at the second term.")])
        body = client.post("/api/hint", json={"sessionId": "s1", "mistakeId": mistake_id, "level": 1}).json()
        assert (body["level"], body["text"], body["source"], body["modelIssue"]) == (1, "Look at the second term.", "model", None)
        assert "rejected" in json.loads(route.calls.last.request.content)["messages"][1]["content"]

    @respx.mock
    def test_second_hint_knows_about_the_first(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = report_mistake(client)
        route.mock(return_value=help_reply("Look at the bracket."))
        client.post("/api/hint", json={"sessionId": "s1", "mistakeId": mistake_id, "level": 1})
        route.mock(return_value=help_reply("What should happen to the 3?", 2))
        client.post("/api/hint", json={"sessionId": "s1", "mistakeId": mistake_id, "level": 2})
        facts = json.loads(json.loads(route.calls.last.request.content)["messages"][1]["content"])
        assert facts["earlier_hints"] == ["Look at the bracket."]

    @respx.mock
    def test_explanation_includes_the_student_question_as_data(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = report_mistake(client)
        route.mock(return_value=help_reply("Both terms get multiplied. So it becomes 2x + 6, not 2x + 3.", 3))
        body = client.post(
            "/api/explain", json={"sessionId": "s1", "mistakeId": mistake_id, "question": "Why is this wrong?"}
        ).json()
        assert body["level"] == 3
        assert body["source"] == "model"
        sent = json.loads(route.calls.last.request.content)
        assert json.loads(sent["messages"][1]["content"])["student_question"] == "Why is this wrong?"
        assert "Why is this wrong?" not in sent["messages"][0]["content"]

    @respx.mock
    def test_wrong_explanation_never_reaches_the_student(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = report_mistake(client)
        route.mock(return_value=help_reply("It becomes 2x + 9.", 3))
        body = client.post("/api/explain", json={"sessionId": "s1", "mistakeId": mistake_id}).json()
        assert body["source"] == "template"
        assert "2x + 6" in body["text"]
        assert "2x + 9" not in body["text"]

    @respx.mock
    def test_model_failure_falls_back_and_says_so(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = report_mistake(client)
        route.mock(return_value=httpx.Response(401, json={"error": {"message": "x"}}))
        body = client.post("/api/hint", json={"sessionId": "s1", "mistakeId": mistake_id, "level": 1}).json()
        assert body["source"] == "template"
        assert body["modelIssue"]["category"] == "auth"

    def test_unknown_mistake_is_refused(self, client: TestClient) -> None:
        response = client.post("/api/hint", json={"sessionId": "s1", "mistakeId": "m_forged", "level": 1})
        assert response.status_code == 404

    @respx.mock
    def test_cannot_ask_about_another_sessions_mistake(self, client: TestClient) -> None:
        respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = report_mistake(client)
        response = client.post("/api/hint", json={"sessionId": "other", "mistakeId": mistake_id, "level": 1})
        assert response.status_code == 404

    def test_level_three_needs_the_explain_endpoint(self, client: TestClient) -> None:
        response = client.post("/api/hint", json={"sessionId": "s1", "mistakeId": "m", "level": 3})
        assert response.status_code == 422

    @respx.mock
    def test_session_summary_records_resolution(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = report_mistake(client)
        summary = client.get("/api/session/s1").json()
        assert summary["knownMistakes"][0]["id"] == mistake_id
        assert "expected" not in summary["knownMistakes"][0]
        assert summary["alerts"] == 1
        assert summary["resolved"] == []

        route.mock(return_value=reading(WRONG[0], {**WRONG[1], "math": "= 2x + 6"}))
        assert analyze(client, 2, image=png(colour="black"), active=mistake_id)["status"] == "no_error"
        summary = client.get("/api/session/s1").json()
        assert summary["knownMistakes"] == []
        assert [event["mistakeId"] for event in summary["resolved"]] == [mistake_id]
        assert summary["resolved"][0]["category"] == "algebra_expansion"


def wav(seconds: float = 1.0, rate: int = 24000, channels: int = 1, width: int = 2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(b"\0" * int(seconds * rate) * channels * width)
    return buffer.getvalue()


ASR = f"{BASE}/asr/transcribe"


class TestVoice:
    def post(self, client: TestClient, audio: bytes, mistake_id: str | None = None) -> httpx.Response:
        data = {"sessionId": "s1"} | ({"mistakeId": mistake_id} if mistake_id else {})
        return client.post("/api/voice", data=data, files={"audio": ("a.wav", audio, "audio/wav")})

    @respx.mock
    def test_request_explain_flow(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = report_mistake(client)
        asr = respx.post(ASR).mock(
            return_value=httpx.Response(200, json={"sessionId": "x", "transcript": "Hey Quack, explain my mistake", "audioDurationMs": 1000, "turns": []})
        )
        route.mock(
            side_effect=[
                completion({"intent": "explain", "question": "explain my mistake"}),
                help_reply("Both terms get multiplied, so it becomes 2x + 6, not 2x + 3.", 3),
            ]
        )
        body = self.post(client, wav(), mistake_id).json()
        assert body["transcript"] == "Hey Quack, explain my mistake"
        assert body["intent"] == "explain"
        assert body["level"] == 3
        assert "2x + 6" in body["text"]

        request = asr.calls.last.request
        assert request.headers["authorization"] == "Bearer test-key"
        assert b"muse-voice-transcribe-1.0" in request.content
        assert b"PUSH_TO_TALK" in request.content

    @pytest.mark.parametrize(
        "audio", [b"not audio", wav(channels=2), wav(rate=44100), wav(seconds=31, rate=16000), b"RIFF\x10\0\0\0WAVEfmt \xff\xff\xff\xff"]
    )
    def test_refuses_audio_meta_would_reject(self, client: TestClient, audio: bytes) -> None:
        assert self.post(client, audio).status_code == 400

    @respx.mock
    def test_only_declared_audio_is_sent(self, client: TestClient) -> None:
        asr = respx.post(ASR).mock(return_value=httpx.Response(200, json={"transcript": "hi"}))
        respx.post(CHAT).mock(return_value=completion({"intent": "other", "question": "hi"}))
        padded = wav(seconds=1) + b"\xaa" * 1_000_000
        assert self.post(client, padded).status_code == 200
        assert len(asr.calls.last.request.content) < 100_000

    @respx.mock
    def test_budget_exhaustion_is_reported_not_a_crash(self, settings) -> None:
        limited = TestClient(create_app(build_services(settings.model_copy(update={"budget_project_usd": 0.0}))))
        body = self.post(limited, wav()).json()
        assert body["modelIssue"]["category"] == "budget_exceeded"

    @respx.mock
    def test_malformed_transcription_response_is_reported(self, client: TestClient) -> None:
        respx.post(ASR).mock(return_value=httpx.Response(200, json=["not", "an", "object"]))
        assert self.post(client, wav()).json()["modelIssue"]["category"] == "invalid_output"

    @respx.mock
    def test_transcription_failure_is_reported(self, client: TestClient) -> None:
        respx.post(ASR).mock(return_value=httpx.Response(401))
        body = self.post(client, wav()).json()
        assert body["modelIssue"]["category"] == "auth"

    @respx.mock
    def test_without_a_mistake_it_only_transcribes(self, client: TestClient) -> None:
        respx.post(ASR).mock(return_value=httpx.Response(200, json={"transcript": "give me a hint"}))
        respx.post(CHAT).mock(return_value=completion({"intent": "hint", "question": "give me a hint"}))
        body = self.post(client, wav()).json()
        assert body["intent"] == "hint"
        assert body["text"] is None
