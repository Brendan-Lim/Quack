"""What a line is doing, from the request to the reply. Meta's API is mocked. The verifier is real."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.test_line_memory import client, compact, model  # noqa: F401
from tests.test_pipeline import png


def check(client: TestClient, model: Any, math: str, version: int = 1, session: str = "page", line_id: str = "line-1", **fields: Any) -> dict[str, Any]:  # noqa: F811
    model.say(compact(math, continues_previous="no", operation="combine_like_terms"))
    response = client.post(
        "/api/analyze",
        json={"sessionId": session, "snapshotVersion": version, "image": png(), "inkLines": [{"id": line_id, "inImage": True}]} | fields,
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestAmbiguousAlgebra:
    def test_it_asks_rather_than_accuses(self, client: TestClient, model: Any) -> None:  # noqa: F811
        reply = check(client, model, "3x + 2x = 5")
        assert reply["status"] == "uncertain"
        assert reply["reason"] == "ambiguous_intent"
        assert reply["mistakes"] == []
        assert reply["intervention"]["shouldQuack"] is False
        assert reply["clarification"] == {"lineId": "line-1", "step": "3x + 2x = 5", "question": "Are you simplifying this, or solving it?"}

    def test_only_about_a_line_two_readings_agree_on(self, client: TestClient, model: Any) -> None:  # noqa: F811
        model.say_in_turn(
            [compact("3x + 2x = 5", continues_previous="no")],
            [compact("3x + 2x = 5x", continues_previous="no")],
        )
        response = client.post(
            "/api/analyze",
            json={"sessionId": "p", "snapshotVersion": 1, "image": png(), "inkLines": [{"id": "l", "inImage": True}]},
        )
        assert response.json()["clarification"] is None

    def test_answering_simplifying_makes_it_a_mistake_without_reading_again(self, client: TestClient, model: Any) -> None:  # noqa: F811
        check(client, model, "3x + 2x = 5")
        calls = len(model.calls)
        reply = client.post(
            "/api/analyze",
            json={
                "sessionId": "page", "snapshotVersion": 2, "image": png(), "inkLines": [{"id": "line-1", "inImage": False}],
                "lineIntents": {"line-1": "simplify"},
            },
        ).json()
        assert len(model.calls) == calls
        assert reply["mistakes"][0]["errorType"] == "lost_variable"
        assert reply["mistakes"][0]["isNew"] is True
        assert reply["intervention"]["shouldQuack"] is True
        assert reply["clarification"] is None

    def test_answering_solving_is_the_end_of_it(self, client: TestClient, model: Any) -> None:  # noqa: F811
        check(client, model, "3x + 2x = 5")
        reply = client.post(
            "/api/analyze",
            json={
                "sessionId": "page", "snapshotVersion": 2, "image": png(), "inkLines": [{"id": "line-1", "inImage": False}],
                "lineIntents": {"line-1": "solve"},
            },
        ).json()
        assert reply["status"] == "no_error"
        assert reply["mistakes"] == []
        assert reply["clarification"] is None

    def test_changing_your_mind_clears_the_mistake(self, client: TestClient, model: Any) -> None:  # noqa: F811
        wrong = check(client, model, "3x + 2x = 5", lineIntents={"line-1": "simplify"})
        mistake = wrong["mistakes"][0]["id"]
        reply = client.post(
            "/api/analyze",
            json={
                "sessionId": "page", "snapshotVersion": 2, "image": png(), "inkLines": [{"id": "line-1", "inImage": False}],
                "acknowledgedMistakeIds": [mistake], "lineIntents": {"line-1": "solve"},
            },
        ).json()
        assert reply["resolvedMistakeIds"] == [mistake]

    @pytest.mark.parametrize("context", ["simplify", "solve"])
    def test_the_page_setting(self, client: TestClient, model: Any, context: str) -> None:  # noqa: F811
        reply = check(client, model, "3x + 2x = 5", taskContext=context)
        assert bool(reply["mistakes"]) == (context == "simplify")
        assert reply["clarification"] is None

    def test_a_line_answer_outranks_the_page_setting(self, client: TestClient, model: Any) -> None:  # noqa: F811
        reply = check(client, model, "3x + 2x = 5", taskContext="simplify", lineIntents={"line-1": "solve"})
        assert reply["mistakes"] == []

    def test_an_intent_cant_be_anything_else(self, client: TestClient, model: Any) -> None:  # noqa: F811
        model.say(compact("3x + 2x = 5"))
        for bad in ({"taskContext": "mistake"}, {"lineIntents": {"a": "wrong"}}):
            response = client.post(
                "/api/analyze", json={"sessionId": "p", "snapshotVersion": 1, "image": png()} | bad
            )
            assert response.status_code == 422

    def test_a_prose_line_saying_the_task_counts(self, client: TestClient, model: Any) -> None:  # noqa: F811
        model.say(compact("Simplify", kind="prose"), compact("3x + 2x = 5", continues_previous="no"))
        reply = client.post(
            "/api/analyze",
            json={
                "sessionId": "p", "snapshotVersion": 1, "image": png(),
                "inkLines": [{"id": "a", "inImage": True}, {"id": "b", "inImage": True}],
            },
        ).json()
        assert reply["mistakes"][0]["errorType"] == "lost_variable"
        assert reply["mistakes"][0]["lineId"] == "b"

    def test_a_task_written_as_math(self, client: TestClient, model: Any) -> None:  # noqa: F811
        model.say(compact("Simplify:"), compact("3x + 2x = 5", continues_previous="no"))
        reply = client.post(
            "/api/analyze",
            json={
                "sessionId": "p", "snapshotVersion": 1, "image": png(),
                "inkLines": [{"id": "a", "inImage": True}, {"id": "b", "inImage": True}],
            },
        ).json()
        assert reply["mistakes"][0]["lineId"] == "b"


class TestIntegrationThroughThePipeline:
    def test_a_wrong_integral_quacks_with_a_hint_that_keeps_the_answer_back(self, client: TestClient, model: Any) -> None:  # noqa: F811
        reply = check(client, model, "∫ x dx = 2", session="i")
        mistake = reply["mistakes"][0]
        assert mistake["category"] == "integration"
        assert reply["intervention"]["shouldQuack"] is True
        hint = client.post("/api/hint", json={"sessionId": "i", "mistakeId": mistake["id"], "level": 1}).json()
        assert hint["source"] == "template"
        assert "x^2" not in hint["text"]

    def test_the_answer_with_no_constant_gets_no_quack_or_reminder(self, client: TestClient, model: Any) -> None:  # noqa: F811
        reply = check(client, model, "∫ 2x dx = x^2", session="j")
        assert reply["status"] == "no_error"
        assert reply["reminder"] is None

    def test_the_fix_clears_it(self, client: TestClient, model: Any) -> None:  # noqa: F811
        wrong = check(client, model, "∫ x dx = 2", session="k")["mistakes"][0]["id"]
        reply = check(client, model, "∫ x dx = x^2/2 + C", 2, session="k", line_id="line-1b", acknowledgedMistakeIds=[wrong], image=png(colour="black"))
        assert reply["resolvedMistakeIds"] == [wrong]

    def test_it_is_not_beyond_what_can_be_checked(self, client: TestClient, model: Any) -> None:  # noqa: F811
        assert check(client, model, "∫ x dx = x^2/2 + C", session="l")["status"] == "no_error"
