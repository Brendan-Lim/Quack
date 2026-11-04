"""Physical iPad test, round 3, level B: the reported page through the whole backend.

Meta's API is mocked with readings the test chooses, so this shows what the pipeline does with a
reading. It says nothing about how well the real model reads handwriting.
"""

from __future__ import annotations

from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient

from quack.config import Settings
from quack.main import build_services, create_app
from tests.test_line_memory import Model, check, compact
from tests.test_pipeline import CHAT, png

SEQUENCE = ["3 × 4 = 10", "1 ÷ 2 = 1/2", "3 × 4 = 9", "1 ÷ 3 = 0", "1 ÷ 2 = 3", "1 ÷ 4 = 0", "1 ÷ 5 = 0", "1 × 4 = 4"]
WRONG = {"a": "3 * 4", "c": "3 * 4", "d": "1/3", "e": "1/2", "f": "1/4", "g": "1/5"}
IDS = "abcdefgh"


@pytest.fixture
def client(settings: Settings) -> TestClient:
    tuned = settings.model_copy(update={"confirm_alerts": True, "confirm_in_parallel": True, "env": "development"})
    return TestClient(create_app(build_services(tuned)))


@pytest.fixture
def model() -> Any:
    with respx.mock:
        answer = Model()
        respx.post(CHAT).mock(side_effect=answer)
        yield answer


def write_the_page(client: TestClient, model: Model, lines: list[str], read: Any = compact) -> list[dict[str, Any]]:
    """As a client would: each new line drawn with the one above it, and known mistakes reported."""

    known: list[str] = []
    replies = []
    for position in range(len(lines)):
        drawn = {position, position - 1}
        model.say(*[read(lines[index]) for index in sorted(drawn) if index >= 0])
        scope = [(IDS[index], index in drawn) for index in range(position + 1)]
        reply = check(client, position + 1, scope, known=list(known))
        for mistake in reply["mistakes"]:
            if mistake["id"] not in known:
                known.append(mistake["id"])
        replies.append(reply)
    return replies


class TestTheReportedPage:
    @pytest.mark.parametrize("position", range(len(SEQUENCE)))
    def test_each_line_alone_on_a_clean_page(self, client: TestClient, model: Model, position: int) -> None:
        model.say(compact(SEQUENCE[position]))
        reply = check(client, 1, [("only", True)])
        if IDS[position] in WRONG:
            assert reply["status"] == "confirmed_error"
            assert reply["mistakes"][0]["previousStep"] == WRONG[IDS[position]]
            assert reply["mistakes"][0]["lineId"] == "only"
            assert reply["intervention"]["shouldQuack"] is True
        else:
            assert reply["status"] == "no_error"
            assert reply["mistakes"] == []

    def test_written_a_line_at_a_time_each_mistake_gets_its_own_record(self, client: TestClient, model: Model) -> None:
        replies = write_the_page(client, model, SEQUENCE)
        found = {m["lineId"]: m for reply in replies for m in reply["mistakes"]}
        assert sorted(found) == sorted(WRONG)
        assert {line: found[line]["previousStep"] for line in found} == WRONG
        assert len({m["id"] for m in found.values()}) == len(WRONG)
        # A quack for each line that's wrong, on the check that first saw it, and no other.
        assert [reply["intervention"]["shouldQuack"] for reply in replies] == [True, False, True, True, True, True, True, False]
        assert [len(reply["mistakes"]) for reply in replies] == [1, 1, 2, 3, 4, 5, 6, 6]

    def test_read_all_at_once_it_comes_to_the_same(self, client: TestClient, model: Model) -> None:
        model.say(*[compact(text) for text in SEQUENCE])
        reply = check(client, 1, [(IDS[i], True) for i in range(len(SEQUENCE))])
        assert sorted(m["lineId"] for m in reply["mistakes"]) == sorted(WRONG)
        assert reply["intervention"]["shouldQuack"] is True
        assert reply["resolvedMistakeIds"] == []

    def test_no_line_in_memory_loses_its_mistake(self, client: TestClient, model: Model) -> None:
        write_the_page(client, model, SEQUENCE)
        calls = len(model.calls)
        # An undo back to all of this: nothing to read, and it's all still known from what's written.
        reply = check(client, 20, [(IDS[i], False) for i in range(len(SEQUENCE))])
        assert len(model.calls) == calls
        assert sorted(m["lineId"] for m in reply["mistakes"]) == sorted(WRONG)

    def test_each_mistake_keeps_its_own_hints(self, client: TestClient, model: Model) -> None:
        model.say(*[compact(text) for text in SEQUENCE])
        reply = check(client, 1, [(IDS[i], True) for i in range(len(SEQUENCE))])
        by_line = {m["lineId"]: m["id"] for m in reply["mistakes"]}
        hints = {}
        for line, mistake_id in by_line.items():
            response = client.post("/api/hint", json={"sessionId": "s", "mistakeId": mistake_id, "level": 1})
            assert response.status_code == 200, response.text
            hints[line] = response.json()["text"]
        assert "multipl" in hints["a"].lower()
        assert "multipl" in hints["c"].lower()
        assert all("divi" in hints[line].lower() for line in "defg")

    def test_a_rounding_mistake_gets_a_hint_straight_away(self, client: TestClient, model: Model) -> None:
        model.say(compact("1/3 ≈ 0.34 (2dp)"))
        mistake = check(client, 1, [("a", True)])["mistakes"][0]
        assert mistake["errorType"] == "rounding_error"
        calls = len(model.calls)
        for level, wanted in ((1, "rounded"), (2, "rounding")):
            response = client.post("/api/hint", json={"sessionId": "s", "mistakeId": mistake["id"], "level": level})
            assert wanted in response.json()["text"]
            assert response.json()["source"] == "template"
        assert len(model.calls) == calls
        assert "0.33" in client.post("/api/explain", json={"sessionId": "s", "mistakeId": mistake["id"]}).json()["text"]

    def test_a_verifier_change_applies_to_what_is_already_remembered(self, client: TestClient, model: Model) -> None:
        # Memory holds what was written, not what was decided about it.
        model.say(compact("1 ÷ 3 = 0"))
        check(client, 1, [("a", True)])
        memory = client.app.state.services.analysis._memory  # type: ignore[attr-defined]
        remembered = memory.get("s", "a")
        assert remembered is not None
        assert remembered.math == "1 ÷ 3 = 0"
        assert not {"verdict", "status", "fingerprint"} & set(remembered.model_dump())


class TestWhatReadingsDo:
    def test_one_sure_reading_and_one_vague_doubt_is_agreement_on_a_wrong_sum(self, client: TestClient, model: Model) -> None:
        for number, pair in enumerate([("clear", "unsure"), ("unsure", "clear")]):
            model.say_in_turn([compact("3 × 4 = 10", legibility=pair[0])], [compact("3 × 4 = 10", legibility=pair[1])])
            reply = check(client, number + 1, [(f"line{number}", True)])
            assert reply["status"] == "confirmed_error"
            assert reply["mistakes"][0]["lineId"] == f"line{number}"

    def test_a_vague_doubt_doesnt_clear_a_right_answer_either_way(self, client: TestClient, model: Model) -> None:
        model.say(compact("3 × 4 = 10"))
        mistake = check(client, 1, [("a", True)])["mistakes"][0]
        model.say_in_turn([compact("3 × 4 = 12", legibility="unsure")], [compact("3 × 4 = 12")])
        reply = check(client, 2, [("a2", True)], known=[mistake["id"]])
        assert reply["resolvedMistakeIds"] == []

    def test_two_unsure_readings_say_nothing(self, client: TestClient, model: Model) -> None:
        model.say(compact("3 × 4 = 10", legibility="unsure"))
        reply = check(client, 1, [("a", True)])
        assert reply["status"] == "uncertain"
        assert reply["mistakes"] == []
        assert reply["settledLineIds"] == []

    def test_doubt_about_another_digit_still_stops_it(self, client: TestClient, model: Model) -> None:
        model.say_in_turn([compact("3 × 4 = 10")], [compact("3 × 4 = 10", legibility="unsure", alternatives=["3 × 4 = 12"])])
        reply = check(client, 1, [("a", True)])
        assert reply["mistakes"] == []

    @pytest.mark.parametrize("alternative", ["1 ÷ 5 = O", "1 ÷ 5 = D"])
    def test_doubt_about_a_round_letter_doesnt(self, client: TestClient, model: Model, alternative: str) -> None:
        model.say_in_turn([compact("1 ÷ 5 = 0", alternatives=[alternative])], [compact("1 ÷ 5 = 0", legibility="unsure", alternatives=[alternative])])
        reply = check(client, 1, [("a", True)])
        assert reply["status"] == "confirmed_error"
        assert reply["settledLineIds"] == ["a"]

    def test_the_same_sum_spelled_differently_is_agreement(self, client: TestClient, model: Model) -> None:
        model.say_in_turn([compact("1 ÷ 5 = 0")], [compact("1/5 = 0", legibility="unsure")])
        reply = check(client, 1, [("a", True)])
        assert reply["status"] == "confirmed_error"

    def test_another_letter_is_a_real_doubt(self, client: TestClient, model: Model) -> None:
        model.say_in_turn([compact("5 + 1 = 3")], [compact("5 + 1 = 3", legibility="unsure", alternatives=["s + 1 = 3"])])
        reply = check(client, 1, [("a", True)])
        assert reply["mistakes"] == []

    def test_an_image_already_read_is_still_reconciled(self, client: TestClient, model: Model) -> None:
        image = png(size=(91, 20))
        model.say_in_turn([compact("3 × 4 = 10")], [compact("3 × 4 = 10", legibility="unsure")])

        def post(version: int, trigger: str = "auto") -> dict[str, Any]:
            return client.post(
                "/api/analyze",
                json={"sessionId": "s", "snapshotVersion": version, "image": image, "trigger": trigger, "inkLines": [{"id": "a", "inImage": True}]},
            ).json()

        assert post(1)["status"] == "confirmed_error"
        calls = len(model.calls)
        again = post(2, "manual")
        assert len(model.calls) == calls
        assert again["status"] == "confirmed_error"

    def test_a_right_sum_with_a_vague_doubt_is_silent_and_unsettled(self, client: TestClient, model: Model) -> None:
        model.say_in_turn([compact("3 × 4 = 12", legibility="unsure")], [compact("3 × 4 = 12")])
        reply = check(client, 1, [("a", True)])
        assert reply["mistakes"] == []
        assert reply["settledLineIds"] == []

    def test_a_second_reading_that_finds_a_mistake_the_first_didnt_says_nothing(self, client: TestClient, model: Model) -> None:
        model.say_in_turn([compact("3 × 4 = 12")], [compact("3 × 4 = 10")])
        reply = check(client, 1, [("a", True)])
        assert reply["mistakes"] == []
        assert reply["settledLineIds"] == []

    def test_one_disputed_line_doesnt_silence_another(self, client: TestClient, model: Model) -> None:
        model.say_in_turn(
            [compact("1 ÷ 3 = 0"), compact("3 × 4 = 10")],
            [compact("1 ÷ 3 = 6"), compact("3 × 4 = 10")],
        )
        reply = check(client, 1, [("a", True), ("b", True)])
        assert [m["lineId"] for m in reply["mistakes"]] == ["b"]


class TestLookingAgain:
    def test_a_later_attempt_gets_new_readings_of_the_same_image(self, client: TestClient, model: Model) -> None:
        image = png(size=(77, 20))

        def post(version: int, attempt: int) -> dict[str, Any]:
            response = client.post(
                "/api/analyze",
                json={"sessionId": "s", "snapshotVersion": version, "image": image, "attempt": attempt, "inkLines": [{"id": "a", "inImage": True}]},
            )
            assert response.status_code == 200
            return response.json()

        model.say_in_turn([compact("3 × 4 = 10")], [compact("3 × 4 = 12")], [compact("3 × 4 = 10")], [compact("3 × 4 = 10")])
        assert post(1, 0)["mistakes"] == []
        calls = len(model.calls)
        # The same request again costs nothing, and can't help.
        assert post(2, 0)["mistakes"] == []
        assert len(model.calls) == calls
        again = post(3, 1)
        assert len(model.calls) == calls + 2
        assert [m["lineId"] for m in again["mistakes"]] == ["a"]
        assert again["settledLineIds"] == ["a"]

    @pytest.mark.parametrize("attempt", [-1, 9])
    def test_attempts_are_bounded(self, client: TestClient, attempt: int) -> None:
        response = client.post(
            "/api/analyze", json={"sessionId": "s", "snapshotVersion": 1, "image": png(), "attempt": attempt}
        )
        assert response.status_code in (400, 422)


class TestTheTrace:
    def traces(self, client: TestClient) -> list[dict[str, Any]]:
        response = client.get("/api/dev/trace/s")
        assert response.status_code == 200
        return response.json()

    def test_it_says_what_became_of_each_line(self, client: TestClient, model: Model) -> None:
        model.say(*[compact(text) for text in SEQUENCE])
        reply = check(client, 3, [(IDS[i], True) for i in range(len(SEQUENCE))])
        lines = reply["trace"]
        assert [line["lineId"] for line in lines] == list(IDS)
        assert all(line["snapshotVersion"] == 3 for line in lines)
        assert [line["disposition"] for line in lines] == [
            "confirmed_new_mistake", "verification_valid", "confirmed_new_mistake", "confirmed_new_mistake",
            "confirmed_new_mistake", "confirmed_new_mistake", "confirmed_new_mistake", "verification_valid",
        ]
        first = lines[0]
        assert first["first"]["math"] == "3 × 4 = 10"
        assert first["second"]["math"] == "3 × 4 = 10"
        assert first["verdict"] == "invalid"
        assert first["parsedOperation"] == "multiplication"
        assert first["mistakeId"] == reply["mistakes"][0]["id"]
        assert self.traces(client)[-1]["lines"][0]["disposition"] == "confirmed_new_mistake"

    @pytest.mark.parametrize(
        ("first", "second", "disposition"),
        [
            (compact("3 × 4 = 12"), compact("3 × 4 = 10"), "transcription_disagreement"),
            (compact("3 × 4 = 10"), compact("3 × 4 = 12"), "transcription_disagreement"),
            (compact("3 × 4 = 10", legibility="unsure"), compact("3 × 4 = 10", legibility="unsure"), "transcription_uncertain"),
            (compact("3 × 4 = 10", alternatives=["3 × 4 = 12"]), compact("3 × 4 = 10"), "transcription_uncertain"),
            (compact("∫x = 2"), compact("∫x = 2"), "parse_unsupported"),
            (compact("1/3 = 0.33"), compact("1/3 = 0.33"), "verification_uncertain"),
            (compact("3 × 4 = 12"), compact("3 × 4 = 12"), "verification_valid"),
            (compact("x + 1 = 2"), compact("x + 1 = 2"), "not_checked"),
            (compact("", kind="prose"), compact("", kind="prose"), "not_mathematics"),
        ],
    )
    def test_each_way_of_not_flagging_says_which_it_was(self, client: TestClient, model: Model, first: dict, second: dict, disposition: str) -> None:
        model.say_in_turn([first], [second])
        reply = check(client, 1, [("a", True)])
        assert reply["trace"][0]["disposition"] == disposition

    def test_it_knows_a_mistake_already_on_record(self, client: TestClient, model: Model) -> None:
        model.say(compact("3 × 4 = 10"))
        mistake = check(client, 1, [("a", True)])["mistakes"][0]
        reply = check(client, 2, [("a", False)], known=[mistake["id"]])
        line = reply["trace"][0]
        assert line["disposition"] == "known_mistake_active"
        assert line["remembered"] is True
        assert line["inImage"] is False
        assert line["first"] is None

    def test_the_same_sum_written_twice_is_two_records(self, client: TestClient, model: Model) -> None:
        """Each sum stands alone, so each is its own mistake. Until the iPad session of 2026-11-02 the second
        was `suppressed_duplicate`, and a second `∫ x dx = 2` lower down the page never quacked."""

        model.say(compact("3 × 4 = 10"), compact("3 × 4 = 10"))
        reply = check(client, 1, [("a", True), ("b", True)])
        assert [line["disposition"] for line in reply["trace"]] == ["confirmed_new_mistake", "confirmed_new_mistake"]
        assert len({mistake["id"] for mistake in reply["mistakes"]}) == 2

    def test_a_check_that_says_nothing_is_on_record_too(self, client: TestClient, model: Model) -> None:
        check(client, 1, [("a", False)])
        assert self.traces(client)[-1]["reason"] == "lines_unread"

    def test_there_is_none_outside_development(self, settings: Settings, model: Model) -> None:
        production = TestClient(create_app(build_services(settings.model_copy(update={"env": "production"}))))
        model.say(compact("3 × 4 = 10"))
        reply = check(production, 1, [("a", True)])
        assert reply["trace"] == []
        assert production.get("/api/dev/trace/s").status_code == 404
        assert production.app.state.services.analysis.traces("s") == []  # type: ignore[attr-defined]
