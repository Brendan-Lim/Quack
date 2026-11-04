"""The second physical iPad session of 2026-11-02, replayed through the whole backend.

The readings here are the real model's, copied from that session's development trace
(`GET /api/dev/trace/{session}` on the server that ran it), and the line ids are the ones it used.
They are played back through a mocked Meta API, so this shows what the pipeline does with those
readings. It says nothing about how often the real model would read the same ink the same way.
"""

from __future__ import annotations

from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient

from quack.config import Settings
from quack.main import build_services, create_app
from tests.test_ipad_qa_2026_11 import ByEffort, line
from tests.test_line_memory import check
from tests.test_pipeline import CHAT


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

# Line ids from the session. `ul1j035n` was the first `∫ x dx = 2`, written at 15:11 and flagged.
# `1bke11ed` was a second one, written lower down the same page at 15:20, which never quacked.
FIRST_COPY, SECOND_COPY = "ul1j035n", "1bke11ed"
INTEGRAL_2 = "∫ x dx = 2"
COSINE, BETWEEN = "d/dx(cos(x)) = -sin(x)", "1ga0i4gw"


def both_read(model: ByEffort, *readings: dict[str, Any] | list[dict[str, Any]]) -> None:
    """Each check's lines, read the same way by both readings."""

    turns = [list(reading) if isinstance(reading, list) else [reading] for reading in readings]
    model.minimal, model.low = list(turns), list(turns)


def mistake_on(reply: dict[str, Any], line_id: str) -> dict[str, Any] | None:
    return next((mistake for mistake in reply["mistakes"] if mistake["lineId"] == line_id), None)


class TestTheSameMistakeWrittenAgain:
    """Check #24: `∫ x dx = 2` on a new line, both readings clear, was `suppressed_duplicate`.

    A mistake's id was a hash of its mathematics, so a second copy anywhere on the page was the one
    already known. It made no sound, and its highlight stayed on the first copy, off screen.
    """

    def first_copy(self, client: TestClient, model: ByEffort) -> str:
        both_read(model, line("∫x dx=2"))
        reply = check(client, 1, [(FIRST_COPY, True)])
        assert reply["intervention"]["shouldQuack"] is True
        return reply["mistakes"][0]["id"]

    def test_a_second_copy_on_its_own_line_is_its_own_alert(self, client: TestClient, model: ByEffort) -> None:
        known = self.first_copy(client, model)
        both_read(model, [line(COSINE), line(INTEGRAL_2)])
        reply = check(client, 2, [(FIRST_COPY, False), (BETWEEN, True), (SECOND_COPY, True)], known=[known], mistakeLineIds={known: [FIRST_COPY]})
        assert reply["status"] == "confirmed_error"
        second = mistake_on(reply, SECOND_COPY)
        assert second is not None, reply["mistakes"]
        assert second["id"] != known
        assert second["isNew"] is True
        assert reply["intervention"]["shouldQuack"] is True
        # The first copy is still there, and still the same mistake.
        first = mistake_on(reply, FIRST_COPY)
        assert first is not None and first["id"] == known and first["isNew"] is False
        assert reply["resolvedMistakeIds"] == []

    def test_reading_the_second_copy_again_is_the_same_alert(self, client: TestClient, model: ByEffort) -> None:
        known = self.first_copy(client, model)
        both_read(model, [line(COSINE), line(INTEGRAL_2)])
        second = mistake_on(check(client, 2, [(FIRST_COPY, False), (BETWEEN, True), (SECOND_COPY, True)], known=[known], mistakeLineIds={known: [FIRST_COPY]}), SECOND_COPY)
        assert second is not None
        ids = [known, second["id"]]
        both_read(model, [line("∫x dx=2"), line(COSINE), line("∫x dx=2")])
        reply = check(client, 3, [(FIRST_COPY, True), (BETWEEN, True), (SECOND_COPY, True)], known=ids, mistakeLineIds={known: [FIRST_COPY], second["id"]: [SECOND_COPY]}, trigger="manual")
        assert reply["intervention"]["shouldQuack"] is False
        assert {mistake["id"] for mistake in reply["mistakes"]} == set(ids)
        assert reply["resolvedMistakeIds"] == []

    def test_its_id_comes_from_its_own_ink_so_an_undo_brings_back_the_same_one(self, client: TestClient, model: ByEffort) -> None:
        known = self.first_copy(client, model)
        both_read(model, [line(COSINE), line(INTEGRAL_2)])
        once = mistake_on(check(client, 2, [(FIRST_COPY, False), (BETWEEN, True), (SECOND_COPY, True)], known=[known], mistakeLineIds={known: [FIRST_COPY]}), SECOND_COPY)
        both_read(model, [line(COSINE), line(INTEGRAL_2)])
        again = mistake_on(check(client, 3, [(FIRST_COPY, False), (BETWEEN, True), (SECOND_COPY, True)], known=[known], mistakeLineIds={known: [FIRST_COPY]}), SECOND_COPY)
        assert once is not None and again is not None and once["id"] == again["id"]

    def test_changing_the_second_copy_moves_on_from_it_alone(self, client: TestClient, model: ByEffort) -> None:
        """Check #26: the second copy changed to 2x, which was also already on the page from the morning."""

        known = self.first_copy(client, model)
        both_read(model, line("∫ x dx = 2x"))
        morning = check(client, 2, [(FIRST_COPY, False), ("d9ntn05p", True)], known=[known], mistakeLineIds={known: [FIRST_COPY]})
        two_x = mistake_on(morning, "d9ntn05p")
        assert two_x is not None
        both_read(model, [line(COSINE), line(INTEGRAL_2)])
        second = mistake_on(check(client, 3, [(FIRST_COPY, False), ("d9ntn05p", False), (BETWEEN, True), (SECOND_COPY, True)], known=[known, two_x["id"]], mistakeLineIds={known: [FIRST_COPY], two_x["id"]: ["d9ntn05p"]}), SECOND_COPY)
        assert second is not None
        open_ids = [known, two_x["id"], second["id"]]
        scopes = {known: [FIRST_COPY], two_x["id"]: ["d9ntn05p"], second["id"]: ["z3ive71u"]}
        both_read(model, line("∫x dx = 2x"))
        reply = check(client, 4, [(FIRST_COPY, False), ("d9ntn05p", False), (BETWEEN, False), ("z3ive71u", True)], known=open_ids, mistakeLineIds=scopes)
        assert reply["resolvedMistakeIds"] == [second["id"]]
        edited = mistake_on(reply, "z3ive71u")
        assert edited is not None and edited["isNew"] is True
        assert edited["id"] not in open_ids
        # The other two copies are where they were, and nothing about them changed.
        assert {mistake["id"] for mistake in reply["mistakes"]} >= {known, two_x["id"]}

    def test_correcting_the_second_copy_leaves_the_first_alone(self, client: TestClient, model: ByEffort) -> None:
        known = self.first_copy(client, model)
        both_read(model, [line(COSINE), line(INTEGRAL_2)])
        second = mistake_on(check(client, 2, [(FIRST_COPY, False), (BETWEEN, True), (SECOND_COPY, True)], known=[known], mistakeLineIds={known: [FIRST_COPY]}), SECOND_COPY)
        assert second is not None
        # The cosine line was between them on the page. A right line straight below a wrong one is a
        # correction of it, which isn't what happened here.
        both_read(model, [line(COSINE), line("∫ x dx = x^2/2 + C")])
        reply = check(client, 3, [(FIRST_COPY, False), (BETWEEN, True), ("fixed", True)], known=[known, second["id"]], mistakeLineIds={known: [FIRST_COPY], second["id"]: ["fixed"]})
        assert reply["resolvedMistakeIds"] == [second["id"]]
        assert [mistake["id"] for mistake in reply["mistakes"]] == [known]
        assert reply["intervention"]["shouldQuack"] is False

    def test_correcting_the_first_copy_leaves_the_second_alone(self, client: TestClient, model: ByEffort) -> None:
        known = self.first_copy(client, model)
        both_read(model, [line(COSINE), line(INTEGRAL_2)])
        second = mistake_on(check(client, 2, [(FIRST_COPY, False), (BETWEEN, True), (SECOND_COPY, True)], known=[known], mistakeLineIds={known: [FIRST_COPY]}), SECOND_COPY)
        assert second is not None
        both_read(model, line("∫ x dx = x^2/2 + C"))
        reply = check(client, 3, [("fixed", True), (BETWEEN, False), (SECOND_COPY, False)], known=[known, second["id"]], mistakeLineIds={known: ["fixed"], second["id"]: [SECOND_COPY]})
        assert reply["resolvedMistakeIds"] == [known]
        assert [mistake["id"] for mistake in reply["mistakes"]] == [second["id"]]

    def test_with_no_idea_where_the_first_is_it_stays_one_mistake(self, client: TestClient, model: ByEffort) -> None:
        """After a reload the client has no anchors. Then a copy can't be told from the original, and
        the old behaviour stands: no second quack."""

        known = self.first_copy(client, model)
        both_read(model, [line(COSINE), line(INTEGRAL_2)])
        reply = check(client, 2, [(FIRST_COPY, False), (BETWEEN, True), (SECOND_COPY, True)], known=[known])
        assert reply["intervention"]["shouldQuack"] is False
        assert all(mistake["id"] == known for mistake in reply["mistakes"])

    def test_the_second_copy_still_needs_both_readings(self, client: TestClient, model: ByEffort) -> None:
        known = self.first_copy(client, model)
        model.minimal, model.low = [[line(INTEGRAL_2)]], [[line("∫ x dx = 2x")]]
        reply = check(client, 2, [(FIRST_COPY, False), (BETWEEN, True), (SECOND_COPY, True)], known=[known], mistakeLineIds={known: [FIRST_COPY]})
        assert mistake_on(reply, SECOND_COPY) is None
        assert reply["intervention"]["shouldQuack"] is False

    def test_two_copies_written_at_once_are_two_alerts(self, client: TestClient, model: ByEffort) -> None:
        both_read(model, [line(INTEGRAL_2), line(COSINE), line(INTEGRAL_2)])
        reply = check(client, 1, [("a", True), (BETWEEN, True), ("b", True)])
        assert {mistake["lineId"] for mistake in reply["mistakes"]} == {"a", "b"}
        assert len({mistake["id"] for mistake in reply["mistakes"]}) == 2


# The `d/dx(sin(x)) = con(x)` line, `48zi7g12`, as the session's checks read it.
CON_LINE = "48zi7g12"
SINE_WITH_COS = "d/dx(sin(x))=cos(x)"


class TestReadingWhatWasWritten:
    """Checks #5, #6, #8 and #9: `con(x)` was read as `Con(x)` and `cos(x)`, then as `cos(x)` by both
    readings, which made it verified and remembered. Circling it to Explain then taught `sin → cos`, and
    later readings of `con(x)` by both were ignored in favour of what was remembered.

    Nothing in Quack's own code turned `con` into `cos`: the parser keeps it as c·o·n·x. The model read
    it both ways, and the reading that happened to be right was believed.
    """

    def first_look(self, client: TestClient, model: ByEffort) -> dict[str, Any]:
        model.minimal = [[line("d/dx(sin(x))=Con(x)", legibility="unsure", alternatives=[SINE_WITH_COS])]]
        model.low = [[line(SINE_WITH_COS)]]
        return check(client, 1, [(CON_LINE, True)])

    def test_two_answers_in_one_check_is_ordinary_disagreement(self, client: TestClient, model: ByEffort) -> None:
        reply = self.first_look(client, model)
        assert reply["status"] == "uncertain"
        assert reply["verifiedLineIds"] == []
        assert reply["contestedLineIds"] == []

    def test_a_later_look_that_agrees_on_the_right_answer_doesnt_make_it_right(self, client: TestClient, model: ByEffort) -> None:
        self.first_look(client, model)
        both_read(model, line(SINE_WITH_COS))
        reply = check(client, 2, [(CON_LINE, True)], attempt=1)
        assert reply["status"] == "uncertain"
        assert reply["reason"] == "reading_contested"
        assert reply["contestedLineIds"] == [CON_LINE]
        assert CON_LINE not in reply["verifiedLineIds"]
        assert reply["mistakes"] == []

    def test_explaining_it_says_what_it_was_read_as_not_the_rule_for_the_right_answer(self, client: TestClient, model: ByEffort) -> None:
        self.first_look(client, model)
        both_read(model, line(SINE_WITH_COS))
        check(client, 2, [(CON_LINE, True)], attempt=1)
        response = client.post(
            "/api/ask",
            json={"sessionId": "s", "action": "explain", "inkLines": [{"id": CON_LINE, "inImage": False}],
                  "selectedLineIds": [CON_LINE]},
        )
        assert response.status_code == 200, response.text
        reply = response.json()
        assert reply["verdict"] == "unclear"
        assert "Con(x)" in reply["text"]
        assert "cos(x) becomes" not in reply["text"] and "derivative of sin(x) is cos(x)" not in reply["text"]

    def test_both_reading_con_is_never_called_right(self, client: TestClient, model: ByEffort) -> None:
        """Check #9's readings, without the remembered `cos(x)` to fall back on."""

        model.minimal = [[line("d/dx(sin(x))=con(x)", legibility="unsure", alternatives=[SINE_WITH_COS])]]
        model.low = [[line("d/dx(sin(x))=Con(x)", legibility="unsure", alternatives=[SINE_WITH_COS])]]
        reply = check(client, 1, [(CON_LINE, True)])
        assert reply["status"] == "uncertain"
        assert reply["verifiedLineIds"] == []
        assert reply["mistakes"] == []

    def test_con_read_clearly_by_both_is_not_called_right_or_wrong(self, client: TestClient, model: ByEffort) -> None:
        both_read(model, line("d/dx(sin(x)) = con(x)"))
        reply = check(client, 1, [(CON_LINE, True)])
        assert reply["status"] == "uncertain"
        assert reply["verifiedLineIds"] == []
        assert reply["mistakes"] == []

    def test_cos_read_clearly_every_time_is_still_right(self, client: TestClient, model: ByEffort) -> None:
        both_read(model, line(SINE_WITH_COS))
        reply = check(client, 1, [("cos", True)])
        assert reply["status"] == "no_error"
        assert reply["verifiedLineIds"] == ["cos"]
        assert reply["contestedLineIds"] == []

    def test_it_can_still_be_found_wrong(self, client: TestClient, model: ByEffort) -> None:
        """Only being called right depends on the line's history. An alert still needs its own two readings."""

        model.minimal = [[line("d/dx(sin(x)) = sin(x)", legibility="unsure", alternatives=["d/dx(sin(x)) = sin(z)"])]]
        model.low = [[line("d/dx(sin(x)) = sin(x)")]]
        check(client, 1, [("a", True)])
        both_read(model, line("d/dx(sin(x)) = sin(x)"))
        reply = check(client, 2, [("a", True)], attempt=1)
        assert reply["status"] == "confirmed_error"

    def test_new_ink_starts_again(self, client: TestClient, model: ByEffort) -> None:
        """Rewritten, it's new strokes and a new line id, with nothing held against it."""

        self.first_look(client, model)
        both_read(model, line(SINE_WITH_COS))
        reply = check(client, 2, [("rewritten", True)])
        assert reply["verifiedLineIds"] == ["rewritten"]


class TestWhatCountsAsAnotherAnswer:
    """Reading and checking are separate. These are about reading alone: is this the same claim?"""

    @staticmethod
    def differ(one: str, other: str) -> bool:
        from quack.services.analysis import _another_answer

        return _another_answer(one, other) or _another_answer(other, one)

    def test_confusable_letters_in_an_answer_are_different_answers(self) -> None:
        assert self.differ("d/dx(sin(x)) = cos(x)", "d/dx(sin(x)) = con(x)")
        assert self.differ("d/dx(-cos(x)) = sin(x)", "d/dx(-cos(x)) = sln(x)")
        assert self.differ("d/dx(ln(cos(x))) = -tan(x)", "d/dx(ln(cos(x))) = -ton(x)")
        assert self.differ("d/dx(x^2) = 2x", "d/dx(x^2) = 2+x")
        assert self.differ("2 + 1 = 3", "2 + 1 = l")

    def test_spellings_of_one_answer_are_not(self) -> None:
        assert not self.differ("d/dx(sin(x)) = cos(x)", "d/dx(sin(x))=cos(x)")
        assert not self.differ("d/dx(sin(x)) = cos(x)", "d/dx(sin(x)) = Cos(x)")
        assert not self.differ("1/5 = 0", "1/5 = O")
        assert not self.differ("2 * 3 = 6", "2 × 3 = 6")
        assert not self.differ("∫ x dx = x^2/2 + C", "∫ x dx = x^2/2 + c")
        assert not self.differ("∫x dx=(x^2)/(2)+C", "∫x dx=x^2/2+C")

    def test_a_reading_that_lost_part_of_the_question_is_ordinary_disagreement(self) -> None:
        """Check #0: one reading missed the integral sign. Another look settled it, and still can."""

        assert not self.differ("\\int x dx = x^2/2 + C", "x dx = x^2/2 + C")
        assert not self.differ("3 + l = 4", "3 + 1 = 4")


def circle(client: TestClient, action: str, lines: list[str], session: str = "s", width: int = 60) -> dict[str, Any]:
    from tests.test_pipeline import png

    response = client.post(
        "/api/ask",
        # A different image for different ink, as an identical one would share its readings.
        json={"sessionId": session, "action": action, "image": png(size=(width + len(lines), 20)),
              "inkLines": [{"id": line_id, "inImage": True} for line_id in lines], "selectedLineIds": lines},
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestExplainTeachesWhatWasCircled:
    """Checks #30 and #31: the rewritten integral was grouped as `∫x dx =` and `x^2/2 + C`. Check said it
    couldn't, and Explain took the last line alone, found a letter in it, and taught combining like terms."""

    SPLIT = [line("∫x dx =", kind="incomplete"), line("x^2/2 + C")]

    def test_an_integral_split_over_two_lines_is_still_explained_as_an_integral(self, client: TestClient, model: ByEffort) -> None:
        both_read(model, self.SPLIT)
        reply = circle(client, "explain", ["1juckg71", "nkpd861r"])
        assert reply["verdict"] == "unsupported"
        assert reply["explanation"]["concept"] == "integral_power_rule"
        assert "Like terms" not in reply["text"]
        # It was never checked, so nothing in it is about this answer.
        assert "x^2/2" not in reply["text"]

    def test_what_cant_be_made_out_gets_no_rule_at_all(self, client: TestClient, model: ByEffort) -> None:
        both_read(model, line("x^2/2 + C"))
        reply = circle(client, "explain", ["nkpd861r"])
        assert reply["explanation"] is None
        assert "Like terms" not in reply["text"]
        assert "won't guess" in reply["text"]

    def test_like_terms_only_for_like_terms(self, client: TestClient, model: ByEffort) -> None:
        both_read(model, line("3x + 2x"))
        assert circle(client, "explain", ["a"])["explanation"]["concept"] == "like_terms"
        both_read(model, line("3x + 2y"))
        assert circle(client, "explain", ["b"], width=70)["explanation"] is None

    def test_a_right_integral_is_short_numbered_steps(self, client: TestClient, model: ByEffort) -> None:
        both_read(model, line("∫ x dx = x^2/2 + C"))
        explanation = circle(client, "explain", ["vxecv813"])["explanation"]
        assert explanation == {
            "concept": "integral_power_rule",
            "title": "The power rule for integrals",
            "steps": [
                "Raise the power by one: x is x^1, so the new power is 2.",
                "Divide by the new power: x^2 becomes x^2/2.",
                "Add + C: any constant disappears when you differentiate.",
            ],
            "takeaway": "Quick check: the derivative of x^2/2 + C is x.",
        }

    def test_a_wrong_integral_is_explained_as_an_integral(self, client: TestClient, model: ByEffort) -> None:
        both_read(model, line("∫ x dx = 2"))
        reply = circle(client, "explain", ["1bke11ed"])
        assert reply["verdict"] == "incorrect"
        assert reply["explanation"]["concept"] == "integral_power_rule"
        assert reply["explanation"]["takeaway"] == "∫ x dx is x^2/2 + C, not 2."

    def test_the_cosine_rule_is_three_short_steps(self, client: TestClient, model: ByEffort) -> None:
        both_read(model, line("d/dx(cos(x)) = -sin(x)"))
        explanation = circle(client, "explain", ["1ga0i4gw"])["explanation"]
        assert explanation["concept"] == "derivative_of_cosine"
        assert len(explanation["steps"]) == 3
        assert all(len(step) <= 90 for step in explanation["steps"])

    def test_a_wrong_sine_derivative_says_why_cosine_appears(self, client: TestClient, model: ByEffort) -> None:
        both_read(model, line("d/dx(sin(x)) = sin(x)"))
        explanation = circle(client, "explain", ["a"])["explanation"]
        assert explanation["concept"] == "derivative_of_sine"
        assert any("slope of the sine curve" in step for step in explanation["steps"])
        assert explanation["takeaway"] == "The derivative of sin(x) is cos(x), not sin(x)."

    def test_a_wrong_cosine_derivative_is_the_cosine_rule(self, client: TestClient, model: ByEffort) -> None:
        both_read(model, line("d/dx(cos(x)) = sin(x)"))
        assert circle(client, "explain", ["a"])["explanation"]["concept"] == "derivative_of_cosine"


class TestRewritingTheIntegral:
    """Checks #24 to #29, on a fresh page with nothing else on it, as one line edited in place.
    Each edit is new strokes, so a new line id, and the client says which lines now hold the mistake."""

    def test_wrong_then_wrong_again_then_right(self, client: TestClient, model: ByEffort) -> None:
        both_read(model, line("∫ x dx = 2"))
        first = check(client, 1, [("v1", True)])
        assert first["intervention"]["shouldQuack"] is True
        open_ids = [first["mistakes"][0]["id"]]
        seen: list[str] = list(open_ids)
        for version, (line_id, answer) in enumerate([("v2", "2x"), ("v3", "2x^2"), ("v4", "x^2")], start=2):
            both_read(model, line(f"∫ x dx = {answer}"))
            reply = check(client, version, [(line_id, True)], known=open_ids, mistakeLineIds={open_ids[0]: [line_id]})
            assert reply["resolvedMistakeIds"] == open_ids, answer
            assert [mistake["isNew"] for mistake in reply["mistakes"]] == [True]
            open_ids = [reply["mistakes"][0]["id"]]
            assert open_ids[0] not in seen
            seen += open_ids
        both_read(model, line("∫ x dx = x^2/2 + C"))
        done = check(client, 5, [("v5", True)], known=open_ids, mistakeLineIds={open_ids[0]: ["v5"]})
        assert done["resolvedMistakeIds"] == open_ids
        assert done["mistakes"] == []
        assert done["verifiedLineIds"] == ["v5"]
        assert done["intervention"]["shouldQuack"] is False

    def test_rubbing_out_the_answer_fixes_nothing(self, client: TestClient, model: ByEffort) -> None:
        both_read(model, line("∫ x dx = 2"))
        known = check(client, 1, [("v1", True)])["mistakes"][0]["id"]
        both_read(model, line("∫x dx =", kind="incomplete"))
        reply = check(client, 2, [("v2", True)], known=[known], mistakeLineIds={known: ["v2"]})
        assert reply["resolvedMistakeIds"] == []
        assert reply["undecidedMistakeIds"] == [known]

    def test_another_wrong_line_elsewhere_doesnt_hold_up_the_fix(self, client: TestClient, model: ByEffort) -> None:
        both_read(model, [line("∫ x dx = 2"), line(COSINE), line("3 * 4 = 10")])
        reply = check(client, 1, [("v1", True), (BETWEEN, True), ("sum", True)])
        ids = {mistake["lineId"]: mistake["id"] for mistake in reply["mistakes"]}
        both_read(model, line("∫ x dx = x^2/2 + C"))
        fixed = check(client, 2, [("v2", True), (BETWEEN, False), ("sum", False)], known=list(ids.values()),
                      mistakeLineIds={ids["v1"]: ["v2"], ids["sum"]: ["sum"]})
        assert fixed["resolvedMistakeIds"] == [ids["v1"]]
        assert [mistake["id"] for mistake in fixed["mistakes"]] == [ids["sum"]]


class TestTheStandInsWait:
    """Check #0: the first reading took 11.5 s, and the 14 s wait for the second only began then."""

    def test_its_wait_starts_with_the_second_reading(self, settings: Settings) -> None:
        import asyncio
        import json
        import time

        import httpx

        from tests.test_pipeline import completion, png

        tuned = settings.model_copy(update={"confirm_alerts": True, "confirm_in_parallel": True, "second_reading_patience_s": 0.6})
        client = TestClient(create_app(build_services(tuned)))
        asked: list[tuple[str, float]] = []

        async def answer(request: httpx.Request) -> httpx.Response:
            effort = json.loads(request.content)["reasoning_effort"]
            asked.append((effort, time.monotonic()))
            if effort == "low":
                await asyncio.sleep(1.5)
            elif [kind for kind, _ in asked].count("minimal") == 1:
                await asyncio.sleep(0.5)
            return completion({"lines": [line(INTEGRAL_2)]})

        with respx.mock:
            respx.post(CHAT).mock(side_effect=answer)
            reply = client.post(
                "/api/analyze",
                json={"sessionId": "s", "snapshotVersion": 1, "image": png(size=(91, 20)), "inkLines": [{"id": "a", "inImage": True}]},
            ).json()
        assert reply["status"] == "confirmed_error"
        first, stand_in = [at for kind, at in asked if kind == "minimal"][:2]
        # 0.6 s after both began. Waiting from the end of the first look made it 1.1 s.
        assert stand_in - first < 0.9, stand_in - first


class TestTheTraceSaysWhatHappened:
    def test_two_spellings_of_one_answer_arent_traced_as_disagreement(self, client: TestClient, model: ByEffort) -> None:
        """Check #21 traced `∫x dx=(x^2)/(2)+C` and `∫x dx=x^2/2+C` as a disagreement, though they were agreed."""

        model.minimal, model.low = [[line("∫x dx=(x^2)/(2)+C")]], [[line("∫x dx=x^2/2+C")]]
        reply = check(client, 1, [("vxecv813", True)])
        assert reply["trace"][0]["disposition"] == "verification_valid"


class TestWhatIsntACopy:
    """From an independent review of the copies change, each shown to go wrong before it was fixed."""

    def first(self, client: TestClient, model: ByEffort) -> str:
        both_read(model, line(INTEGRAL_2))
        return check(client, 1, [("a", True)])["mistakes"][0]["id"]

    def test_a_known_mistake_out_of_view_is_never_alerted_again(self, client: TestClient, model: ByEffort) -> None:
        known = self.first(client, model)
        both_read(model, line(INTEGRAL_2))
        reply = check(client, 2, [("a", True)], known=[known], outOfViewMistakeIds=[known])
        assert reply["intervention"]["shouldQuack"] is False
        assert all(not mistake["isNew"] for mistake in reply["mistakes"])

    def test_a_slip_following_a_new_copy_waits_for_it(self, client: TestClient, model: ByEffort) -> None:
        known = self.first(client, model)
        both_read(model, [line(COSINE), line(INTEGRAL_2), line("= 3", continues_previous="yes")])
        reply = check(client, 2, [("a", False), (BETWEEN, True), ("copy", True), ("then", True)], known=[known], mistakeLineIds={known: ["a"]})
        new = [mistake for mistake in reply["mistakes"] if mistake["isNew"]]
        assert [mistake["lineId"] for mistake in new] == ["copy"]

    def test_the_same_step_written_again_straight_underneath_is_the_same_mistake(self, client: TestClient, model: ByEffort) -> None:
        known = self.first(client, model)
        both_read(model, line(INTEGRAL_2))
        reply = check(client, 2, [("a", False), ("again", True)], known=[known], mistakeLineIds={known: ["a"]})
        assert reply["intervention"]["shouldQuack"] is False
        assert reply["resolvedMistakeIds"] == []
