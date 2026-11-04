"""Reading only the lines that changed, and tracking each mistake on its own line.

Meta's API is mocked. This pins down what's sent and what's kept, not how fast the model is.
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
from tests.test_pipeline import CHAT, completion, png


def compact(math: str, **overrides: Any) -> dict[str, Any]:
    return {
        "math": math,
        "kind": "math",
        "legibility": "clear",
        "alternatives": [],
        "continues_previous": "unknown",
        "operation": "arithmetic",
    } | overrides


class Model:
    """Answers each reading with whatever lines the test says are being read."""

    def __init__(self) -> None:
        self.lines: list[list[dict[str, Any]]] = []
        self.calls: list[dict[str, Any]] = []

    def say(self, *lines: dict[str, Any]) -> None:
        self.lines = [list(lines)]

    def say_in_turn(self, *readings: list[dict[str, Any]]) -> None:
        self.lines = list(readings)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        lines = self.lines.pop(0) if len(self.lines) > 1 else self.lines[0]
        return completion({"lines": lines})

    @property
    def counts(self) -> list[str]:
        return [call["messages"][1]["content"][0]["text"] for call in self.calls]


@pytest.fixture
def client(settings: Settings) -> TestClient:
    tuned = settings.model_copy(update={"confirm_alerts": True, "confirm_in_parallel": True})
    return TestClient(create_app(build_services(tuned)))


@pytest.fixture
def model() -> Any:
    with respx.mock:
        answer = Model()
        respx.post(CHAT).mock(side_effect=answer)
        yield answer


def check(
    client: TestClient,
    version: int,
    lines: list[tuple[str, bool]],
    known: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    response = client.post(
        "/api/analyze",
        json={
            "sessionId": "s",
            "snapshotVersion": version,
            # A different image each time, as each check draws different ink.
            "image": png(size=(40 + version, 20)),
            "acknowledgedMistakeIds": known or [],
            "inkLines": [{"id": line_id, "inImage": drawn} for line_id, drawn in lines],
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestReadingWhatChanged:
    def test_the_reading_asks_for_only_what_is_used(self, client: TestClient, model: Model) -> None:
        model.say(compact("2 × 3 = 6"))
        check(client, 1, [("a", True)])
        schema = model.calls[0]["response_format"]["json_schema"]
        assert schema["name"] == "CompactReading"
        fields = schema["schema"]["$defs"]["CompactLine"]["properties"]
        assert "raw_text" not in fields and "y_top" not in fields
        assert model.counts[0].endswith("It has 1 written line.")

    def test_lines_two_readings_agree_on_are_not_read_again(self, client: TestClient, model: Model) -> None:
        model.say(compact("2 × 3 = 6"))
        body = check(client, 1, [("a", True)])
        assert body["settledLineIds"] == ["a"]
        calls = len(model.calls)

        model.say(compact("1 ÷ 2 = 1/2"))
        body = check(client, 2, [("a", False), ("b", True)])
        assert body["status"] == "no_error"
        assert sorted(body["settledLineIds"]) == ["a", "b"]
        assert len(model.calls) == calls + 2
        assert model.counts[-1].endswith("It has 1 written line.")
        assert [line["text"] for line in body["lines"]] == ["2 × 3 = 6", "1 ÷ 2 = 1/2"]

    @pytest.mark.parametrize(
        ("one", "other"),
        [
            (compact("2 × 3 = 6"), compact("2 × 3 = 8")),
            (compact("2 × 3 = 6", legibility="unsure"), compact("2 × 3 = 6", legibility="unsure")),
            (compact("2 × 3 = 6", alternatives=["2 × 3 = 8"]), compact("2 × 3 = 6")),
        ],
    )
    def test_a_line_either_reading_doubts_is_read_again(self, client: TestClient, model: Model, one: Any, other: Any) -> None:
        model.say_in_turn([one], [other])
        assert check(client, 1, [("a", True)])["settledLineIds"] == []

    def test_a_line_that_was_only_read_once_is_not_remembered(self, client: TestClient, model: Model) -> None:
        # Anything remembered is what later lines get checked against, so one reading isn't enough.
        model.say_in_turn([compact("2 × 3 = 6")], [compact("2 × 3 = 8")])
        check(client, 1, [("a", True)])
        calls = len(model.calls)
        body = check(client, 2, [("a", False), ("b", True)])
        assert body["reason"] == "lines_unread"
        assert body["unreadLineIds"] == ["a"]
        assert len(model.calls) == calls

    def test_forgotten_lines_cost_nothing_and_say_so(self, client: TestClient, model: Model) -> None:
        body = check(client, 1, [("a", False), ("b", True)])
        assert (body["status"], body["reason"], body["unreadLineIds"]) == ("uncertain", "lines_unread", ["a"])
        assert model.calls == []

    def test_a_page_all_remembered_is_checked_without_reading(self, client: TestClient, model: Model) -> None:
        model.say(compact("3 × 4 = 10"))
        mistake = check(client, 1, [("a", True)])["mistakes"][0]
        calls = len(model.calls)
        body = check(client, 2, [("a", False)], known=[mistake["id"]])
        assert len(model.calls) == calls
        assert body["status"] == "confirmed_error"
        assert [m["isNew"] for m in body["mistakes"]] == [False]
        assert body["resolvedMistakeIds"] == []
        assert body["settledLineIds"] == ["a"]

    def test_a_new_alert_can_come_from_memory_without_a_reading(self, client: TestClient, model: Model) -> None:
        # The server's record of what it alerted about was lost, or the client threw that reply away.
        model.say(compact("3 × 4 = 10"))
        check(client, 1, [("a", True)])
        calls = len(model.calls)
        body = check(client, 2, [("a", False)])
        assert len(model.calls) == calls
        assert body["intervention"]["shouldQuack"] is True

    def test_asking_directly_rereads_what_was_remembered(self, client: TestClient, model: Model) -> None:
        model.say(compact("3 × 4 = 10"))
        check(client, 1, [("a", True)])
        model.say(compact("3 × 4 = 10"))
        check(client, 2, [("a", True)], trigger="manual")
        assert len([call for call in model.calls if call["reasoning_effort"] == "minimal"]) == 2

    def test_ink_read_as_two_answers_is_neither_right_nor_wrong(self, client: TestClient, model: Model) -> None:
        """Until the iPad session of 2026-11-02, asking directly made the new reading win, so `= 12` read
        after `= 10` was right. The same rule made `con(x)` read as `cos(x)` right. Neither can be known,
        so the alert is withdrawn, and the line isn't called right either."""

        model.say(compact("3 × 4 = 10"))
        mistake = check(client, 1, [("a", True)])["mistakes"][0]["id"]
        model.say(compact("3 × 4 = 12"))
        body = check(client, 2, [("a", True)], trigger="manual", known=[mistake], mistakeLineIds={mistake: ["a"]})
        assert body["status"] == "uncertain"
        assert body["reason"] == "reading_contested"
        assert body["resolvedMistakeIds"] == [mistake]
        assert body["verifiedLineIds"] == []
        assert body["contestedLineIds"] == ["a"]

    def test_a_rewrite_read_two_ways_is_no_fix(self, client: TestClient, model: Model) -> None:
        """Withdrawing is only for the ink the mistake was found on. New ink read two ways fixes nothing."""

        model.say(compact("3 × 4 = 10"))
        mistake = check(client, 1, [("a", True)])["mistakes"][0]["id"]
        model.say_in_turn([compact("3 × 4 = 17", legibility="unsure")], [compact("3 × 4 = 12")])
        assert check(client, 2, [("b", True)], known=[mistake], mistakeLineIds={mistake: ["b"]})["resolvedMistakeIds"] == []
        model.say(compact("3 × 4 = 12"))
        body = check(client, 3, [("b", True)], trigger="manual", known=[mistake], mistakeLineIds={mistake: ["b"]})
        assert body["reason"] == "reading_contested"
        assert body["resolvedMistakeIds"] == []

    def test_an_automatic_check_does_not_overrule_what_was_remembered(self, client: TestClient, model: Model) -> None:
        model.say(compact("3 × 4 = 10"))
        check(client, 1, [("a", True)])
        model.say(compact("3 × 4 = 12"))
        body = check(client, 2, [("a", True)])
        # Not overruled: never called right on the new readings. But not wrong on the remembered one either, now
        # that both of this check's readings contradict it. That's how `7(x+8)` stayed an accusation on the iPad on
        # 2026-11-04 (evening), after the student's `7(x+6)` had been read as written three checks running.
        assert body["status"] != "no_error"
        assert body["mistakes"] == []

    def test_remembered_lines_belong_to_their_session(self, client: TestClient, model: Model) -> None:
        model.say(compact("2 × 3 = 6"))
        check(client, 1, [("a", True)])
        response = client.post(
            "/api/analyze",
            json={"sessionId": "other", "snapshotVersion": 1, "image": png(), "inkLines": [{"id": "a", "inImage": False}]},
        )
        assert response.json()["reason"] == "lines_unread"

    def test_a_redrawn_agreed_line_stays_as_agreed(self, client: TestClient, model: Model) -> None:
        model.say(compact("2(x + 3)", operation="expand"))
        check(client, 1, [("a", True)])
        # A misreading of the line drawn only for context can't change what it was agreed to say.
        model.say(compact("2(x + 5)", operation="expand"), compact("= 2x + 6", continues_previous="yes", operation="expand"))
        body = check(client, 2, [("a", True), ("b", True)])
        assert body["status"] == "no_error"

    def test_a_misaligned_partial_reading_says_nothing(self, client: TestClient, model: Model) -> None:
        model.say(compact("2 × 3 = 6"))
        check(client, 1, [("a", True)])
        model.say(compact("3 × 4 = 10"), compact("7 + 5 = 11"))
        body = check(client, 2, [("a", False), ("b", True)])
        assert (body["status"], body["reason"]) == ("uncertain", "lines_misaligned")
        assert body["mistakes"] == []

    def test_a_wide_gap_stops_a_label_joining_lines(self, client: TestClient, model: Model) -> None:
        model.say(compact("2x = 4", operation="solve_equation"))
        check(client, 1, [("a", True)])
        model.say(compact("x = 3", continues_previous="yes", operation="solve_equation"))
        lines = [{"id": "a", "inImage": False}, {"id": "b", "inImage": True, "spaced": True}]
        response = client.post(
            "/api/analyze", json={"sessionId": "s", "snapshotVersion": 2, "image": png(size=(50, 20)), "inkLines": lines}
        )
        assert response.json()["mistakes"] == []


class TestSeparateMistakes:
    def test_two_mistakes_are_two_records_each_on_its_own_line(self, client: TestClient, model: Model) -> None:
        model.say(compact("3 × 4 = 10"), compact("1 ÷ 2 = 2"))
        body = check(client, 1, [("a", True), ("b", True)])
        assert body["status"] == "confirmed_error"
        assert body["intervention"]["shouldQuack"] is True
        found = {m["lineId"]: m for m in body["mistakes"]}
        assert set(found) == {"a", "b"}
        assert found["a"]["parsedOperation"] == "multiplication"
        assert found["b"]["parsedOperation"] == "division"
        assert all(m["isNew"] for m in body["mistakes"])
        assert found["a"]["id"] != found["b"]["id"]

    def test_each_knows_which_piece_of_working_it_belongs_to(self, client: TestClient, model: Model) -> None:
        model.say(compact("3 × 4 = 10"), compact("1 ÷ 2 = 2"))
        found = check(client, 1, [("a", True), ("b", True)])["mistakes"]
        assert {m["lineId"]: m["problemId"] for m in found} == {"a": "a", "b": "b"}

    def test_only_the_wrong_line_is_owned(self, client: TestClient, model: Model) -> None:
        model.say(compact("2(x + 3) = 2x + 6", operation="expand"), compact("d/dx(x^2 + 2x) = 3x + 2", operation="differentiate"))
        body = check(client, 1, [("right", True), ("wrong", True)])
        assert [m["lineId"] for m in body["mistakes"]] == ["wrong"]

    def test_a_right_division_is_not_given_the_multiplications_mistake(self, client: TestClient, model: Model) -> None:
        model.say(compact("3 × 4 = 10"), compact("1 ÷ 6 = 1/6"), compact("1 ÷ 2 = 1/2"))
        body = check(client, 1, [("a", True), ("b", True), ("c", True)])
        assert [(m["lineId"], m["parsedOperation"]) for m in body["mistakes"]] == [("a", "multiplication")]

    def test_a_known_mistake_is_not_new_again(self, client: TestClient, model: Model) -> None:
        model.say(compact("3 × 4 = 10"))
        first = check(client, 1, [("a", True)])["mistakes"][0]
        model.say(compact("1 ÷ 2 = 2"))
        body = check(client, 2, [("a", False), ("b", True)], known=[first["id"]])
        flags = {m["lineId"]: m["isNew"] for m in body["mistakes"]}
        assert flags == {"a": False, "b": True}
        assert body["intervention"]["shouldQuack"] is True
        assert body["mistake"]["lineId"] == "b"

    def test_nothing_new_means_no_quack(self, client: TestClient, model: Model) -> None:
        model.say(compact("3 × 4 = 10"))
        first = check(client, 1, [("a", True)])["mistakes"][0]
        model.say(compact("7 + 5 = 12"))
        body = check(client, 2, [("a", False), ("b", True)], known=[first["id"]])
        assert body["intervention"]["shouldQuack"] is False
        assert [m["isNew"] for m in body["mistakes"]] == [False]

    def test_fixing_one_leaves_the_other(self, client: TestClient, model: Model) -> None:
        model.say(compact("3 × 4 = 10"), compact("1 ÷ 2 = 2"))
        mistakes = check(client, 1, [("a", True), ("b", True)])["mistakes"]
        ids = {m["lineId"]: m["id"] for m in mistakes}
        model.say(compact("3 × 4 = 12"))
        body = check(client, 2, [("a2", True), ("b", False)], known=list(ids.values()))
        assert body["resolvedMistakeIds"] == [ids["a"]]
        assert [m["id"] for m in body["mistakes"]] == [ids["b"]]
        assert body["intervention"]["shouldQuack"] is False

    def test_an_unclear_correction_claims_nothing(self, client: TestClient, model: Model) -> None:
        model.say(compact("3 × 4 = 10"))
        mistake = check(client, 1, [("a", True)])["mistakes"][0]
        model.say_in_turn([compact("3 × 4 = 12")], [compact("3 × 4 = 12", legibility="unsure")])
        body = check(client, 2, [("a2", True)], known=[mistake["id"]])
        assert body["resolvedMistakeIds"] == []
        assert body["status"] == "uncertain"

    def test_a_disputed_correction_claims_nothing(self, client: TestClient, model: Model) -> None:
        model.say(compact("3 × 4 = 10"))
        mistake = check(client, 1, [("a", True)])["mistakes"][0]
        model.say_in_turn([compact("3 × 4 = 12")], [compact("3 × 4 = 10")])
        body = check(client, 2, [("a2", True)], known=[mistake["id"]])
        assert body["resolvedMistakeIds"] == []
        assert body["reason"] == "fix_disputed"

    def test_one_disputed_line_does_not_silence_another(self, client: TestClient, model: Model) -> None:
        model.say_in_turn(
            [compact("3 × 4 = 10"), compact("1 ÷ 2 = 2")],
            [compact("3 × 4 = 10"), compact("1 ÷ 2 = 1/2")],
        )
        body = check(client, 1, [("a", True), ("b", True)])
        assert [m["lineId"] for m in body["mistakes"]] == ["a"]

    def test_each_mistake_has_its_own_hints(self, client: TestClient, model: Model) -> None:
        model.say(compact("3 × 4 = 10"), compact("1 ÷ 2 = 2"))
        found = {m["lineId"]: m["id"] for m in check(client, 1, [("a", True), ("b", True)])["mistakes"]}
        def hint(mistake_id: str) -> str:
            reply = client.post("/api/hint", json={"sessionId": "s", "mistakeId": mistake_id, "level": 1})
            return reply.json()["text"]
        assert hint(found["a"]) == "Check that multiplication again."
        assert hint(found["b"]) == "Check that division again."

    def test_the_power_rule_explanation_is_instant_and_precise(self, client: TestClient, model: Model) -> None:
        model.say(compact("d/dx(x^2 + 2x) = 3x + 2", operation="differentiate"))
        mistake = check(client, 1, [("a", True)])["mistakes"][0]
        calls = len(model.calls)
        reply = client.post("/api/explain", json={"sessionId": "s", "mistakeId": mistake["id"]}).json()
        assert reply["text"] == "The derivative of x^2 is 2x, not 3x. The derivative of 2x is 2, so the correct result is 2x + 2."
        assert reply["source"] == "template"
        assert len(model.calls) == calls
