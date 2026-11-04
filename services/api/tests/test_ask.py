"""Circling some working and asking. Meta's API is mocked, and the verifier is real."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from quack.main import build_services, create_app
from quack.services.ask import SelectionFinding, help_for, problems_with_answer
from tests.accounts_support import account_settings, register
from tests.test_line_memory import compact
from tests.test_pipeline import CHAT, completion, png


class Model:
    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []
        self.answers: list[str] = []
        self.readings = 0
        self.asks: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        name = body["response_format"]["json_schema"]["name"]
        if name == "CompactReading":
            self.readings += 1
            return completion({"lines": self.lines})
        if name == "GeneratedAnswer":
            self.asks.append(json.loads(body["messages"][1]["content"].split("\n\n")[0]))
            return completion({"text": self.answers.pop(0) if self.answers else "Look at what each side says."})
        return completion({"level": 1, "text": "Look at this step again.", "references_step": "x"})


@pytest.fixture
def model() -> Any:
    with respx.mock:
        answer = Model()
        respx.post(CHAT).mock(side_effect=answer)
        yield answer


@pytest.fixture
def client(settings: Any) -> TestClient:
    tuned = settings.model_copy(update={"confirm_alerts": True, "confirm_in_parallel": True})
    return TestClient(create_app(build_services(tuned)))


def ask(client: TestClient, model: Model, math: str | list[str], action: str = "hint", **fields: Any) -> dict[str, Any]:
    texts = [math] if isinstance(math, str) else math
    model.lines = [compact(text, continues_previous="no" if position == 0 else "yes") for position, text in enumerate(texts)]
    ids = [f"line-{position}" for position in range(len(texts))]
    body = {
        "sessionId": "page", "action": action, "image": png(),
        "inkLines": [{"id": line, "inImage": True} for line in ids], "selectedLineIds": ids,
    } | fields
    response = client.post("/api/ask", json=body)
    assert response.status_code == 200, response.text
    return response.json()


class TestCorrectWork:
    @pytest.mark.parametrize("action", ["hint", "check", "explain"])
    def test_no_mistake_is_made_up(self, client: TestClient, model: Model, action: str) -> None:
        reply = ask(client, model, "3 * 4 = 12", action)
        assert reply["verdict"] == "correct"
        assert reply["source"] == "template"
        assert "wrong" not in reply["text"].lower()
        assert "mistake" not in reply["text"].lower()

    def test_a_hint_says_there_is_nothing_to_fix(self, client: TestClient, model: Model) -> None:
        assert "nothing to fix" in ask(client, model, "d/dx(x^3) = 3x^2")["text"]

    def test_a_condition_is_mentioned(self, client: TestClient, model: Model) -> None:
        reply = ask(client, model, "x/x = 1", "check", taskContext="simplify")
        assert reply["disposition"] == "valid_under_assumptions"
        assert "isn't zero" in reply["text"]


class TestWrongWork:
    def test_journey_c_a_hint_about_the_power_rule(self, client: TestClient, model: Model) -> None:
        reply = ask(client, model, "d/dx(x^3) = 2x")
        assert reply["verdict"] == "incorrect"
        assert reply["level"] == 1
        assert reply["source"] == "template"
        assert "3x^2" not in reply["text"]
        assert "power" in reply["text"].lower() or "derivative" in reply["text"].lower()

    def test_a_second_hint_goes_further(self, client: TestClient, model: Model) -> None:
        one = ask(client, model, "d/dx(x^3) = 2x", level=1)
        two = ask(client, model, "d/dx(x^3) = 2x", level=2)
        assert one["text"] != two["text"]
        assert two["level"] == 2

    def test_explaining_gives_the_verified_result(self, client: TestClient, model: Model) -> None:
        reply = ask(client, model, "∫ x dx = 2", "explain")
        assert "x^2/2 + C" in reply["text"]
        assert reply["level"] == 3

    def test_checking_says_so_without_the_answer(self, client: TestClient, model: Model) -> None:
        reply = ask(client, model, "∫ x dx = 2", "check")
        assert reply["verdict"] == "incorrect"
        assert "x^2" not in reply["text"]


class TestNothingIsRecorded:
    def test_no_mistake_on_record_and_no_alert_later_is_used_up(self, client: TestClient, model: Model) -> None:
        ask(client, model, "3 * 4 = 10", "check")
        services = client.app.state.services  # type: ignore[attr-defined]
        assert services.mistakes.get_session("page") is None
        # The automatic check still finds it, and still quacks once.
        reply = client.post(
            "/api/analyze",
            json={"sessionId": "page", "snapshotVersion": 1, "image": png(), "inkLines": [{"id": "line-0", "inImage": True}]},
        ).json()
        assert reply["intervention"]["shouldQuack"] is True

    def test_no_event_or_count_with_an_account(self, tmp_path: Path, model: Model) -> None:
        client = TestClient(create_app(build_services(account_settings(tmp_path))))
        headers = register(client)
        session = client.post("/api/sessions/current", headers=headers).json()["session"]["id"]
        model.lines = [compact("3 * 4 = 10", continues_previous="no")]
        for action in ("hint", "check", "explain"):
            response = client.post(
                "/api/ask",
                json={"sessionId": "page", "action": action, "image": png(), "inkLines": [{"id": "l", "inImage": True}], "selectedLineIds": ["l"]},
                headers=headers,
            )
            assert response.status_code == 200
        recap = client.post(f"/api/sessions/{session}/finish", headers=headers).json()
        assert recap["glance"]["found"] == 0
        assert client.get("/api/mistakes", headers=headers).json()["entries"] == []

    def test_it_needs_signing_in_where_accounts_are_on(self, tmp_path: Path, model: Model) -> None:
        client = TestClient(create_app(build_services(account_settings(tmp_path))))
        response = client.post(
            "/api/ask", json={"sessionId": "p", "action": "hint", "inkLines": [{"id": "l", "inImage": False}], "selectedLineIds": ["l"]}
        )
        assert response.status_code == 401


class TestWhatIsRead:
    def test_a_line_already_agreed_on_costs_nothing(self, client: TestClient, model: Model) -> None:
        model.lines = [compact("3 * 4 = 10", continues_previous="no")]
        client.post(
            "/api/analyze",
            json={"sessionId": "page", "snapshotVersion": 1, "image": png(), "inkLines": [{"id": "known", "inImage": True}]},
        )
        before = model.readings
        reply = client.post(
            "/api/ask",
            json={"sessionId": "page", "action": "check", "inkLines": [{"id": "known", "inImage": False}], "selectedLineIds": ["known"]},
        ).json()
        assert model.readings == before
        assert reply["verdict"] == "incorrect"
        assert reply["timings"]["modelRequests"] == 0

    def test_an_answer_can_build_memory_for_later(self, client: TestClient, model: Model) -> None:
        ask(client, model, "3 * 4 = 12", "check")
        before = model.readings
        reply = client.post(
            "/api/ask",
            json={"sessionId": "page", "action": "check", "inkLines": [{"id": "line-0", "inImage": False}], "selectedLineIds": ["line-0"]},
        ).json()
        assert model.readings == before
        assert reply["verdict"] == "correct"

    def test_a_forgotten_line_is_asked_for_again(self, client: TestClient, model: Model) -> None:
        reply = client.post(
            "/api/ask",
            json={"sessionId": "page", "action": "check", "inkLines": [{"id": "gone", "inImage": False}], "selectedLineIds": ["gone"]},
        ).json()
        assert reply["unreadLineIds"] == ["gone"]
        assert model.readings == 0

    def test_only_what_is_selected_is_judged(self, client: TestClient, model: Model) -> None:
        model.lines = [compact("3 * 4 = 10", continues_previous="no"), compact("2 + 2 = 4", continues_previous="no")]
        reply = client.post(
            "/api/ask",
            json={
                "sessionId": "page", "action": "check", "image": png(),
                "inkLines": [{"id": "a", "inImage": True}, {"id": "b", "inImage": True}], "selectedLineIds": ["b"],
            },
        ).json()
        assert reply["verdict"] == "correct"

    def test_the_line_above_is_context(self, client: TestClient, model: Model) -> None:
        model.lines = [compact("2(x + 3)", continues_previous="no"), compact("= 2x + 3", continues_previous="yes")]
        reply = client.post(
            "/api/ask",
            json={
                "sessionId": "page", "action": "check", "image": png(),
                "inkLines": [{"id": "a", "inImage": True}, {"id": "b", "inImage": True}], "selectedLineIds": ["b"],
            },
        ).json()
        assert reply["verdict"] == "incorrect"
        assert [line["text"] for line in reply["lines"]] == ["= 2x + 3"]

    def test_a_bad_image_is_refused(self, client: TestClient, model: Model) -> None:
        response = client.post(
            "/api/ask",
            json={"sessionId": "p", "action": "hint", "image": "data:image/png;base64,AAAA", "inkLines": [{"id": "l", "inImage": True}], "selectedLineIds": ["l"]},
        )
        assert response.status_code == 400


class TestWhenItCantBeSure:
    def test_two_readings_disagree(self, client: TestClient, model: Model) -> None:
        sequence = iter([[compact("3 * 4 = 10")], [compact("3 * 4 = 12")]])

        def turn(request: httpx.Request) -> httpx.Response:
            return completion({"lines": next(sequence)})

        respx.post(CHAT).mock(side_effect=turn)
        reply = client.post(
            "/api/ask",
            json={"sessionId": "z", "action": "check", "image": png(), "inkLines": [{"id": "l", "inImage": True}], "selectedLineIds": ["l"]},
        ).json()
        assert reply["verdict"] == "unclear"
        assert "read" in reply["text"]

    def test_a_doubtful_reading_names_the_doubt(self, client: TestClient, model: Model) -> None:
        model.lines = [compact("3 * 4 = 10", legibility="unsure", alternatives=["3 * 4 = 18"])]
        reply = client.post(
            "/api/ask",
            json={"sessionId": "z", "action": "hint", "image": png(), "inkLines": [{"id": "l", "inImage": True}], "selectedLineIds": ["l"]},
        ).json()
        assert reply["verdict"] == "unclear"
        assert "3 * 4 = 18" in reply["text"]

    def test_a_reading_that_doesnt_line_up_isnt_called_words(self, client: TestClient, model: Model) -> None:
        model.lines = [compact("3 * 4 = 12"), compact("3 * 4 = 12")]
        reply = client.post(
            "/api/ask",
            json={"sessionId": "z", "action": "check", "image": png(), "inkLines": [{"id": "l", "inImage": True}], "selectedLineIds": ["l"]},
        ).json()
        assert reply["verdict"] == "unclear"

    def test_unsupported_maths_is_called_that(self, client: TestClient, model: Model) -> None:
        reply = ask(client, model, "lim(x) = 3", "check")
        assert reply["verdict"] == "unsupported"
        assert "beyond" in reply["text"]

    def test_an_ambiguous_line_gets_the_question(self, client: TestClient, model: Model) -> None:
        reply = ask(client, model, "3x + 2x = 5", "check")
        assert reply["verdict"] == "ambiguous_intent"
        assert reply["clarification"]["lineId"] == "line-0"

    def test_an_expression_claims_nothing(self, client: TestClient, model: Model) -> None:
        assert ask(client, model, "3x + 2x", "check")["verdict"] == "unchecked"

    def test_words(self, client: TestClient, model: Model) -> None:
        model.lines = [compact("", kind="prose")]
        reply = client.post(
            "/api/ask",
            json={"sessionId": "z", "action": "check", "image": png(), "inkLines": [{"id": "l", "inImage": True}], "selectedLineIds": ["l"]},
        ).json()
        assert reply["verdict"] == "not_math"

    def test_a_model_failure_reading_is_no_verdict(self, client: TestClient, model: Model) -> None:
        respx.post(CHAT).mock(return_value=httpx.Response(500, json={"error": {"message": "no"}}))
        reply = client.post(
            "/api/ask",
            json={"sessionId": "z", "action": "check", "image": png(), "inkLines": [{"id": "l", "inImage": True}], "selectedLineIds": ["l"]},
        ).json()
        assert reply["verdict"] == "unclear"
        assert reply["modelIssue"]


class TestAFreeQuestion:
    def test_the_model_is_only_told_the_finding(self, client: TestClient, model: Model) -> None:
        model.answers = ["The power rule says what happens to the exponent."]
        reply = ask(client, model, "d/dx(x^3) = 2x", "question", question="What rule should I use?")
        assert reply["source"] == "model"
        said = model.asks[0]
        assert said["finding"] == "incorrect"
        assert "3x^2" not in json.dumps(said)

    def test_it_may_not_call_correct_work_wrong(self, client: TestClient, model: Model) -> None:
        model.answers = ["That is wrong.", "This is a mistake."]
        reply = ask(client, model, "3 * 4 = 12", "question", question="Is this right?")
        assert reply["source"] == "template"
        assert "holds up" in reply["text"]

    def test_it_may_not_call_unchecked_work_right(self, client: TestClient, model: Model) -> None:
        model.answers = ["That is correct.", "That's right."]
        reply = ask(client, model, "lim(x) = 3", "question", question="Is this right?")
        assert reply["source"] == "template"
        assert "beyond" in reply["text"]

    def test_it_may_not_give_the_answer_away(self, client: TestClient, model: Model) -> None:
        model.answers = ["It should be 3x^2.", "The answer is 3x^2."]
        reply = ask(client, model, "d/dx(x^3) = 2x", "question", question="What's the answer?")
        assert reply["source"] == "template"
        assert "3x^2" not in reply["text"]

    def test_nothing_readable_doesnt_ask_the_model(self, client: TestClient, model: Model) -> None:
        model.lines = [compact("", kind="prose")]
        client.post(
            "/api/ask",
            json={
                "sessionId": "z", "action": "question", "question": "What's this?", "image": png(),
                "inkLines": [{"id": "l", "inImage": True}], "selectedLineIds": ["l"],
            },
        )
        assert model.asks == []

    @pytest.mark.parametrize(
        ("text", "verdict"),
        [("That's a mistake.", "correct"), ("That is correct.", "incorrect"), ("You forgot the x.", "incorrect"), ("", "correct"), ("word " * 60, "correct")],
    )
    def test_what_is_refused(self, text: str, verdict: Any) -> None:
        assert problems_with_answer(text, verdict, None)

    def test_a_known_mistake_uses_what_is_on_record(self, client: TestClient, model: Model) -> None:
        model.lines = [compact("3 * 4 = 10", continues_previous="no")]
        found = client.post(
            "/api/analyze",
            json={"sessionId": "page", "snapshotVersion": 1, "image": png(), "inkLines": [{"id": "k", "inImage": True}]},
        ).json()["mistakes"][0]
        before = model.readings
        reply = client.post(
            "/api/ask",
            json={
                "sessionId": "page", "action": "hint", "inkLines": [{"id": "k", "inImage": False}], "selectedLineIds": ["k"],
                "mistakeId": found["id"],
            },
        ).json()
        assert reply["verdict"] == "incorrect"
        assert model.readings == before


class TestWhereWorkingStarts:
    def test_an_equation_to_solve_is_not_called_empty_or_right(self) -> None:
        finding = SelectionFinding("unchecked", working=("3x + 2x = 5",))
        text = asyncio.run(help_for(None, "check", finding)).text  # type: ignore[arg-type]
        assert "where this working starts" in text
        assert "holds up" not in text

    def test_an_expression_still_says_nothing_is_claimed(self) -> None:
        finding = SelectionFinding("unchecked", working=("3x + 2x",))
        text = asyncio.run(help_for(None, "check", finding)).text  # type: ignore[arg-type]
        assert "doesn't say what anything equals" in text
