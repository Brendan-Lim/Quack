"""Backend behaviour with Meta's API mocked at the HTTP layer.

These prove our handling of responses. They say nothing about how well Muse Spark reads
handwriting: that is what evals/ is for.
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from PIL import Image

from quack.config import Settings
from quack.llm.ledger import Ledger
from quack.llm.pricing import token_cost
from quack.llm.router import ModelRouter
from quack.main import build_services, create_app

BASE = "https://api.test/v1"
CHAT = f"{BASE}/chat/completions"


def png(size: tuple[int, int] = (40, 20), colour: str = "white") -> str:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def line(math: str, **overrides: Any) -> dict[str, Any]:
    return {
        "raw_text": math,
        "math": math,
        "kind": "math",
        "legibility": "clear",
        "alternatives": [],
        "continues_previous": "yes",
        "operation": "expand",
        "y_top": 0.1,
        "y_bottom": 0.4,
    } | overrides


def completion(content: Any, finish_reason: str = "stop", cached: int = 0) -> httpx.Response:
    text = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(
        200,
        json={
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "muse-spark-1.3",
            "choices": [{"index": 0, "finish_reason": finish_reason, "message": {"role": "assistant", "content": text}}],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 200,
                "total_tokens": 1200,
                "prompt_tokens_details": {"cached_tokens": cached},
                "completion_tokens_details": {"reasoning_tokens": 150},
            },
        },
    )


def reading(*lines: dict[str, Any]) -> httpx.Response:
    return completion({"lines": list(lines)})


WRONG = (line("2(x + 3)", continues_previous="no"), line("= 2x + 3"))
RIGHT = (line("2(x + 3)", continues_previous="no"), line("= 2x + 6"))


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(build_services(settings)))


def _hang(*_: Any) -> None:
    import time

    time.sleep(120)


def analyze(
    client: TestClient,
    version: int = 1,
    session: str = "s1",
    image: str | None = None,
    active: str | list[str] | None = None,
) -> dict[str, Any]:
    known = [active] if isinstance(active, str) else (active or [])
    response = client.post(
        "/api/analyze",
        json={"sessionId": session, "snapshotVersion": version, "image": image or png(), "acknowledgedMistakeIds": known},
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestAlertDecision:
    @respx.mock
    def test_verified_mistake_quacks(self, client: TestClient) -> None:
        respx.post(CHAT).mock(return_value=reading(*WRONG))
        body = analyze(client)
        assert body["status"] == "confirmed_error"
        assert body["intervention"]["shouldQuack"] is True
        assert body["mistake"]["verified"] is True
        assert body["mistake"]["errorType"] == "incorrect_distribution"
        assert body["mistake"]["previousStep"] == "2(x + 3)"

    @respx.mock
    def test_response_does_not_reveal_the_answer(self, client: TestClient) -> None:
        respx.post(CHAT).mock(return_value=reading(*WRONG))
        assert "2x + 6" not in json.dumps(analyze(client))

    @respx.mock
    def test_correct_work_stays_silent(self, client: TestClient) -> None:
        respx.post(CHAT).mock(return_value=reading(*RIGHT))
        body = analyze(client)
        assert body["status"] == "no_error"
        assert body["intervention"]["shouldQuack"] is False

    @respx.mock
    def test_ambiguous_handwriting_stays_silent(self, client: TestClient) -> None:
        respx.post(CHAT).mock(
            return_value=reading(WRONG[0], line("= 2x + 3", legibility="unsure", alternatives=["= 2x + 8"]))
        )
        body = analyze(client)
        assert body["status"] == "uncertain"
        assert body["intervention"]["shouldQuack"] is False

    @respx.mock
    def test_unsupported_notation_stays_silent(self, client: TestClient) -> None:
        respx.post(CHAT).mock(return_value=reading(line("lim(x)", continues_previous="no")))
        body = analyze(client)
        assert body["status"] == "unsupported"
        assert body["intervention"]["shouldQuack"] is False

    @respx.mock
    def test_prose_only_is_not_a_mistake(self, client: TestClient) -> None:
        respx.post(CHAT).mock(return_value=reading(line("", raw_text="remember the test", kind="prose")))
        assert analyze(client)["status"] == "unsupported"

    @respx.mock
    def test_crossed_out_error_is_ignored(self, client: TestClient) -> None:
        respx.post(CHAT).mock(
            return_value=reading(WRONG[0], line("= 2x + 3", kind="crossed_out"), line("= 2x + 6"))
        )
        assert analyze(client)["status"] == "no_error"

    @respx.mock
    def test_model_claim_of_a_mistake_is_not_trusted(self, client: TestClient) -> None:
        # There is no field through which the model can assert an error, and an unexpected
        # one fails validation rather than triggering an alert.
        forged = {"lines": list(RIGHT), "verified": True, "status": "confirmed_error"}
        respx.post(CHAT).mock(return_value=completion(forged))
        body = analyze(client)
        assert body["status"] == "uncertain"
        assert body["intervention"]["shouldQuack"] is False
        assert body["modelIssue"]["category"] == "invalid_output"


class TestDeduplication:
    """`active` is what the browser reports it is showing, as the real client does."""

    @respx.mock
    def test_same_mistake_quacks_once(self, client: TestClient) -> None:
        respx.post(CHAT).mock(return_value=reading(*WRONG))
        first = analyze(client, 1)
        assert first["intervention"]["shouldQuack"] is True
        active = first["mistake"]["id"]
        assert analyze(client, 2, active=active)["intervention"]["shouldQuack"] is False
        assert analyze(client, 3, active=active)["intervention"]["shouldQuack"] is False

    @respx.mock
    def test_reply_the_browser_discarded_does_not_use_up_the_quack(self, client: TestClient) -> None:
        # The student kept writing, so the browser dropped the first reply as stale and is
        # showing nothing. The next reply must still quack, or the alert is lost for good.
        respx.post(CHAT).mock(return_value=reading(*WRONG))
        assert analyze(client, 1)["intervention"]["shouldQuack"] is True
        assert analyze(client, 2, active=None)["intervention"]["shouldQuack"] is True

    @respx.mock
    def test_correcting_resolves_the_mistake(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        first = analyze(client, 1)
        route.mock(return_value=reading(*RIGHT))
        second = analyze(client, 2, image=png(colour="black"), active=first["mistake"]["id"])
        assert second["status"] == "no_error"
        assert second["resolvedMistakeIds"] == [first["mistake"]["id"]]

    @respx.mock
    def test_correct_work_resolves_nothing_when_nothing_is_showing(self, client: TestClient) -> None:
        respx.post(CHAT).mock(return_value=reading(*RIGHT))
        assert analyze(client, 1)["resolvedMistakeIds"] == []

    @respx.mock
    def test_new_error_after_fixing_quacks_again(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        first = analyze(client, 1)
        route.mock(return_value=reading(WRONG[0], line("= 2x + 5")))
        second = analyze(client, 2, image=png(colour="black"), active=first["mistake"]["id"])
        assert second["intervention"]["shouldQuack"] is True
        assert second["mistake"]["id"] != first["mistake"]["id"]
        assert second["resolvedMistakeIds"] == [first["mistake"]["id"]]

    @respx.mock
    def test_same_error_again_after_real_fix_rearms(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        analyze(client, 1)
        route.mock(return_value=reading(*RIGHT))
        analyze(client, 2, image=png(colour="black"))
        route.mock(return_value=reading(*WRONG))
        assert analyze(client, 3, image=png(colour="gray"))["intervention"]["shouldQuack"] is True

    @respx.mock
    def test_unreadable_rewrite_does_not_resolve_or_requack(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        active = analyze(client, 1)["mistake"]["id"]
        route.mock(return_value=reading(WRONG[0], line("= 2x", kind="incomplete")))
        assert analyze(client, 2, image=png(colour="black"), active=active)["resolvedMistakeIds"] == []
        route.mock(return_value=reading(*WRONG))
        assert analyze(client, 3, image=png(colour="gray"), active=active)["intervention"]["shouldQuack"] is False

    @respx.mock
    def test_erasing_the_whole_page_resolves_it(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = analyze(client, 1)["mistake"]["id"]
        cleared = client.post("/api/clear", json={"sessionId": "s1", "snapshotVersion": 2, "acknowledgedMistakeIds": [mistake_id]})
        assert cleared.json() == {"resolvedMistakeIds": [mistake_id]}
        assert route.call_count == 1

    @respx.mock
    def test_hint_history_survives_rereads_but_not_a_fix(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        active = analyze(client, 1)["mistake"]["id"]
        analyze(client, 2, image=png(colour="gray"), active=active)
        manager = client.app.state.services.mistakes  # type: ignore[attr-defined]
        manager.add_hint("s1", active, "first hint")
        analyze(client, 3, image=png(colour="red"), active=active)
        assert manager.get_mistake("s1", active).hints_given == ["first hint"]
        route.mock(return_value=reading(*RIGHT))
        analyze(client, 4, image=png(colour="blue"), active=active)
        assert manager.get_mistake("s1", active) is None

    @respx.mock
    def test_an_old_mistake_does_not_hide_a_new_one(self, client: TestClient) -> None:
        # The first mistake is still on the page, and the student starts a new problem below.
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        old = analyze(client, 1)["mistake"]["id"]
        route.mock(return_value=reading(*WRONG, line("3(x + 1)", continues_previous="no"), line("= 3x + 1")))
        second = analyze(client, 2, image=png(colour="black"), active=old)
        assert second["intervention"]["shouldQuack"] is True
        assert second["mistake"]["id"] != old
        assert second["resolvedMistakeIds"] == []

        # With both known, reading the page again is silent, and neither has been fixed.
        again = analyze(client, 3, image=png(colour="gray"), active=[old, second["mistake"]["id"]])
        assert again["intervention"]["shouldQuack"] is False
        assert again["resolvedMistakeIds"] == []

    @respx.mock
    def test_fixing_one_of_two_mistakes_resolves_only_that_one(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        old = analyze(client, 1)["mistake"]["id"]
        route.mock(return_value=reading(*WRONG, line("3(x + 1)", continues_previous="no"), line("= 3x + 1")))
        new = analyze(client, 2, image=png(colour="black"), active=old)["mistake"]["id"]
        route.mock(return_value=reading(*RIGHT, line("3(x + 1)", continues_previous="no"), line("= 3x + 1")))
        body = analyze(client, 3, image=png(colour="gray"), active=[old, new])
        assert body["resolvedMistakeIds"] == [old]
        assert body["mistake"]["id"] == new
        assert body["intervention"]["shouldQuack"] is False

    @respx.mock
    def test_a_fix_is_recognised_despite_an_unsupported_line_nearby(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        old = analyze(client, 1)["mistake"]["id"]
        route.mock(return_value=reading(*RIGHT, line("lim(x)", continues_previous="no")))
        body = analyze(client, 2, image=png(colour="black"), active=old)
        assert body["status"] == "unsupported"
        assert body["resolvedMistakeIds"] == [old]

    @respx.mock
    def test_an_unreadable_line_does_not_count_as_a_fix(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        old = analyze(client, 1)["mistake"]["id"]
        route.mock(return_value=reading(WRONG[0], line("= 2x + 3", legibility="unsure", alternatives=["= 2x + 6"])))
        assert analyze(client, 2, image=png(colour="black"), active=old)["resolvedMistakeIds"] == []

    @respx.mock
    def test_new_mistake_must_also_survive_confirmation_with_an_old_one_present(self, settings: Settings) -> None:
        client = TestClient(create_app(build_services(settings.model_copy(update={"confirm_alerts": True}))))
        both = reading(*WRONG, line("3(x + 1)", continues_previous="no"), line("= 3x + 1"))
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        old = analyze(client, 1)["mistake"]["id"]
        route.mock(side_effect=[both, reading(*WRONG)])
        body = analyze(client, 2, image=png(colour="black"), active=old)
        assert body["reason"] == "readings_disagree"
        assert body["intervention"]["shouldQuack"] is False

    @respx.mock
    def test_sessions_are_independent(self, client: TestClient) -> None:
        respx.post(CHAT).mock(return_value=reading(*WRONG))
        assert analyze(client, 1, "a")["intervention"]["shouldQuack"] is True
        assert analyze(client, 1, "b")["intervention"]["shouldQuack"] is True


class TestWorkerIsolation:
    @respx.mock
    def test_a_stuck_verifier_is_killed_and_the_next_request_works(self, client: TestClient, monkeypatch: Any) -> None:
        import quack.services.verifier_pool as pool_module

        # Bypasses the parser's limits, to show the backstop works even if they miss something.
        monkeypatch.setattr(pool_module, "_run", _hang)
        client.app.state.services.settings.verify_timeout_s = 0.5  # type: ignore[attr-defined]
        respx.post(CHAT).mock(return_value=reading(*WRONG))
        body = analyze(client, 1)
        assert body["status"] == "uncertain"
        assert body["reason"].startswith("verification_")

        monkeypatch.undo()
        client.app.state.services.settings.verify_timeout_s = 4.0  # type: ignore[attr-defined]
        assert analyze(client, 2, image=png(colour="black"))["status"] == "confirmed_error"

    @respx.mock
    def test_a_label_flip_does_not_resolve_and_requack(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        old = analyze(client, 1)["mistake"]["id"]
        for continues in ("unknown", "no"):
            route.mock(return_value=reading(WRONG[0], line("= 2x + 3", continues_previous=continues)))
            body = analyze(client, 2, image=png(colour="black"), active=old)
            assert body["resolvedMistakeIds"] == []
            assert body["intervention"]["shouldQuack"] is False

    @respx.mock
    def test_confirmation_cannot_be_answered_by_the_reading_it_checks(self, settings: Settings) -> None:
        same_route = settings.model_copy(
            update={
                "confirm_alerts": True,
                "routes_json": '{"transcribe_confirm": {"reasoning_effort": "low", "max_tokens": 6000}}',
            }
        )
        client = TestClient(create_app(build_services(same_route)))
        route = respx.post(CHAT).mock(side_effect=[reading(*WRONG), reading(*RIGHT)])
        body = analyze(client)
        assert route.call_count == 2
        assert body["reason"] == "readings_disagree"

    @respx.mock
    def test_failed_confirmation_reports_the_model_problem(self, settings: Settings) -> None:
        client = TestClient(create_app(build_services(settings.model_copy(update={"confirm_alerts": True}))))
        respx.post(CHAT).mock(side_effect=[reading(*WRONG), httpx.Response(401, json={"error": {"message": "no"}})])
        body = analyze(client)
        assert body["modelIssue"]["category"] == "auth"
        assert body["intervention"]["shouldQuack"] is False

    @respx.mock
    def test_prose_between_lines_means_they_cannot_be_called_a_step(self, client: TestClient) -> None:
        respx.post(CHAT).mock(
            return_value=reading(
                line("2x = 4", continues_previous="no", operation="solve_equation"),
                line("", raw_text="Question 2", kind="prose"),
                line("3x = 9", operation="solve_equation"),
            )
        )
        assert analyze(client)["intervention"]["shouldQuack"] is False


class TestStaleness:
    @respx.mock
    def test_older_snapshot_is_ignored(self, client: TestClient) -> None:
        respx.post(CHAT).mock(return_value=reading(*WRONG))
        analyze(client, 5)
        body = analyze(client, 4)
        assert body["stale"] is True
        assert body["intervention"]["shouldQuack"] is False

    @respx.mock
    def test_identical_image_uses_the_cached_reading(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        analyze(client, 1)
        second = analyze(client, 2)
        assert route.call_count == 1
        assert second["timings"]["cacheHit"] is True


class TestConfirmation:
    @pytest.fixture
    def confirming(self, settings: Settings) -> TestClient:
        return TestClient(create_app(build_services(settings.model_copy(update={"confirm_alerts": True}))))

    @respx.mock
    def test_alert_needs_both_readings_to_agree(self, confirming: TestClient) -> None:
        respx.post(CHAT).mock(return_value=reading(*WRONG))
        body = analyze(confirming)
        assert body["status"] == "confirmed_error"
        assert body["timings"]["modelRequests"] == 2

    @respx.mock
    def test_disagreeing_second_reading_blocks_the_alert(self, confirming: TestClient) -> None:
        respx.post(CHAT).mock(side_effect=[reading(*WRONG), reading(*RIGHT)])
        body = analyze(confirming)
        assert body["status"] == "uncertain"
        assert body["reason"] == "readings_disagree"
        assert body["intervention"]["shouldQuack"] is False

    @respx.mock
    def test_failed_confirmation_stays_silent(self, confirming: TestClient) -> None:
        respx.post(CHAT).mock(
            side_effect=[reading(*WRONG), httpx.Response(400, json={"error": {"message": "no"}})]
        )
        body = analyze(confirming)
        assert body["status"] == "uncertain"
        assert body["intervention"]["shouldQuack"] is False

    @respx.mock
    def test_in_sequence_correct_work_does_not_pay_for_a_second_reading(self, settings: Settings) -> None:
        sequential = settings.model_copy(update={"confirm_alerts": True, "confirm_in_parallel": False})
        client = TestClient(create_app(build_services(sequential)))
        route = respx.post(CHAT).mock(return_value=reading(*RIGHT))
        analyze(client)
        assert route.call_count == 1


class TestModelFailures:
    def test_missing_api_key(self, settings: Settings) -> None:
        client = TestClient(create_app(build_services(settings.model_copy(update={"model_api_key": ""}))))
        assert client.get("/api/health").json()["modelConfigured"] is False
        body = analyze(client)
        assert body["status"] == "uncertain"
        assert body["modelIssue"]["category"] == "no_api_key"

    @respx.mock
    def test_auth_error_is_not_retried(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=httpx.Response(401, json={"error": {"message": "bad"}}))
        body = analyze(client)
        assert route.call_count == 1
        assert body["modelIssue"]["category"] == "auth"

    @respx.mock
    def test_bad_request_is_not_retried(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=httpx.Response(400, json={"error": {"message": "bad"}}))
        analyze(client)
        assert route.call_count == 1

    @respx.mock
    def test_rate_limit_retries_then_succeeds(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(
            side_effect=[
                httpx.Response(429, headers={"retry-after": "0"}, json={"error": {"message": "slow"}}),
                reading(*RIGHT),
            ]
        )
        assert analyze(client)["status"] == "no_error"
        assert route.call_count == 2

    @respx.mock
    def test_server_errors_stop_after_bounded_retries(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=httpx.Response(500, json={"error": {"message": "x"}}))
        body = analyze(client)
        assert route.call_count == 3
        assert body["modelIssue"]["category"] == "server_error"
        assert body["intervention"]["shouldQuack"] is False

    @respx.mock
    def test_long_retry_after_gives_up_rather_than_hanging(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(
            return_value=httpx.Response(429, headers={"retry-after": "120"}, json={"error": {"message": "slow"}})
        )
        analyze(client)
        assert route.call_count == 1

    @respx.mock
    def test_timeout_is_reported(self, client: TestClient) -> None:
        respx.post(CHAT).mock(side_effect=httpx.TimeoutException("slow"))
        assert analyze(client)["modelIssue"]["category"] == "timeout"

    @respx.mock
    def test_invalid_json_is_handled(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=completion("not json"))
        body = analyze(client)
        assert route.call_count == 2
        assert body["status"] == "uncertain"
        assert body["modelIssue"]["category"] == "invalid_output"

    @respx.mock
    def test_truncated_output_is_not_parsed(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=completion("{}", finish_reason="length"))
        assert analyze(client)["modelIssue"]["category"] == "truncated"
        assert route.call_count == 1

    @respx.mock
    def test_error_detail_does_not_leak_the_key(self, client: TestClient) -> None:
        respx.post(CHAT).mock(return_value=httpx.Response(401, json={"error": {"message": "key test-key is bad"}}))
        assert "test-key" not in json.dumps(analyze(client))


class TestRequestShape:
    @respx.mock
    def test_request_follows_meta_documentation(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*RIGHT))
        analyze(client)
        request = route.calls.last.request
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "muse-spark-1.3"
        assert body["reasoning_effort"] == "minimal"
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
        assert body["messages"][0]["role"] == "developer"
        assert isinstance(body["messages"][0]["content"], str), "images are only allowed on user messages"
        parts = body["messages"][1]["content"]
        assert any(part["type"] == "image_url" for part in parts)
        assert "stop" not in body and "logit_bias" not in body

    @respx.mock
    def test_contributor_model_is_refused(self, settings: Settings) -> None:
        bad = settings.model_copy(update={"routes_json": '{"transcribe": {"model": "muse-spark-1.3-contributor"}}'})
        with pytest.raises(ValueError, match="no known price"):
            build_services(bad)

    @respx.mock
    def test_route_can_be_overridden(self, settings: Settings) -> None:
        overridden = settings.model_copy(
            update={"routes_json": '{"transcribe": {"model": "muse-wonder-1.0", "reasoning_effort": "medium"}}'}
        )
        client = TestClient(create_app(build_services(overridden)))
        route = respx.post(CHAT).mock(return_value=reading(*RIGHT))
        analyze(client)
        body = json.loads(route.calls.last.request.content)
        assert (body["model"], body["reasoning_effort"]) == ("muse-wonder-1.0", "medium")


class TestImageSafety:
    @pytest.mark.parametrize(
        "image",
        [
            "not a data url",
            "data:image/png;base64,@@@@",
            "data:image/png;base64," + base64.b64encode(b"<svg></svg>").decode(),
            "data:text/html;base64," + base64.b64encode(b"<script>").decode(),
        ],
    )
    def test_bad_images_are_rejected_without_a_model_call(self, client: TestClient, image: str) -> None:
        with respx.mock:
            route = respx.post(CHAT)
            response = client.post("/api/analyze", json={"sessionId": "s", "snapshotVersion": 1, "image": image})
            assert response.status_code == 400
            assert route.call_count == 0

    def test_oversized_request_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/api/analyze", content=b"x" * 10_000_001, headers={"content-type": "application/json"}
        )
        assert response.status_code == 413

    def test_rate_limit_protects_credits(self, settings: Settings) -> None:
        client = TestClient(create_app(build_services(settings.model_copy(update={"rate_limit_per_minute": 1}))))
        with respx.mock:
            respx.post(CHAT).mock(return_value=reading(*RIGHT))
            analyze(client)
            response = client.post("/api/analyze", json={"sessionId": "s", "snapshotVersion": 2, "image": png()})
        assert response.status_code == 429


class TestBudgets:
    def test_cost_uses_cached_rate(self) -> None:
        assert token_cost("muse-spark-1.3", 1000, 0, 0) == pytest.approx(0.00125)
        assert token_cost("muse-spark-1.3", 1000, 1000, 0) == pytest.approx(0.00015)
        assert token_cost("muse-spark-1.3", 0, 0, 1000) == pytest.approx(0.00425)
        assert token_cost("muse-wonder-1.0", 1000, 500, 1000) == pytest.approx(0.0021 + 0.02)

    @respx.mock
    def test_usage_is_logged_with_every_field(self, client: TestClient, settings: Settings) -> None:
        respx.post(CHAT).mock(return_value=completion({"lines": list(RIGHT)}, cached=800))
        analyze(client)
        record = json.loads(settings.ledger_path.read_text().splitlines()[0])
        assert record["model"] == "muse-spark-1.3"
        assert record["reasoning_effort"] == "minimal"
        assert record["purpose"] == "transcribe"
        assert record["input_tokens"] == 1000
        assert record["cached_input_tokens"] == 800
        assert record["output_tokens"] == 200
        assert record["reasoning_tokens"] == 150
        assert record["estimated_usd"] == pytest.approx(token_cost("muse-spark-1.3", 1000, 800, 200))
        assert record["http_status"] == 200
        assert record["run_id"] == "runtime"
        assert record["elapsed_ms"] > 0

    @respx.mock
    def test_failures_are_logged_at_zero_cost(self, client: TestClient, settings: Settings) -> None:
        respx.post(CHAT).mock(return_value=httpx.Response(401, json={"error": {"message": "x"}}))
        analyze(client)
        record = json.loads(settings.ledger_path.read_text().splitlines()[0])
        assert record["error_category"] == "auth"
        assert record["estimated_usd"] == 0

    @respx.mock
    def test_project_ceiling_blocks_before_spending(self, settings: Settings) -> None:
        client = TestClient(create_app(build_services(settings.model_copy(update={"budget_project_usd": 0.001}))))
        route = respx.post(CHAT).mock(return_value=reading(*RIGHT))
        body = analyze(client)
        assert route.call_count == 0
        assert body["modelIssue"]["category"] == "budget_exceeded"

    @respx.mock
    def test_session_budget_blocks_one_session_only(self, settings: Settings) -> None:
        client = TestClient(create_app(build_services(settings.model_copy(update={"budget_session_usd": 0.05}))))
        route = respx.post(CHAT).mock(return_value=reading(*RIGHT))
        assert analyze(client, 1, "a").get("modelIssue") is None
        for version in range(2, 100):
            body = analyze(client, version, "a", image=png(colour=f"#{version:06x}"))
            if body.get("modelIssue"):
                break
        assert body["modelIssue"]["category"] == "budget_exceeded"
        calls = route.call_count
        assert analyze(client, 1, "b", image=png(colour="red")).get("modelIssue") is None
        assert route.call_count == calls + 1

    @respx.mock
    def test_spend_survives_a_restart(self, settings: Settings) -> None:
        client = TestClient(create_app(build_services(settings)))
        respx.post(CHAT).mock(return_value=reading(*RIGHT))
        analyze(client)
        ledger = Ledger(settings.ledger_path, 100, 100, 100)
        assert ledger.project_spend > 0

    def test_per_request_limit(self, settings: Settings) -> None:
        strict = settings.model_copy(update={"budget_request_usd": 0.0001})
        router = ModelRouter(strict, Ledger(None, 100, 100, strict.budget_request_usd))
        client = TestClient(create_app(build_services(strict, router=router)))
        assert analyze(client)["modelIssue"]["category"] == "budget_exceeded"


class TestPublicSurface:
    def app(self, settings: Settings, **update: Any) -> TestClient:
        return TestClient(create_app(build_services(settings.model_copy(update=update))))

    def test_development_routes_are_off_unless_asked_for(self, settings: Settings) -> None:
        client = self.app(settings, env="production")
        for path in ("/api/dev/usage", "/api/dev/trace/anything", "/api/session", "/api/session/anything", "/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404, path
        assert client.get("/api/health").status_code == 200

    def test_production_is_the_default_environment(self) -> None:
        assert Settings(_env_file=None).is_development is False

    def test_key_is_not_in_settings_repr(self, settings: Settings) -> None:
        assert "test-key" not in repr(settings)

    def test_readiness_distinguishes_a_configured_key_from_database_and_auth(self, settings: Settings) -> None:
        client = self.app(settings, env="production")
        response = client.get("/api/ready")
        assert response.status_code == 503
        assert response.json() == {
            "reachable": True, "databaseReachable": False, "authConfigured": False,
            "modelConfigured": True, "modelStatus": "configured_not_probed", "inferenceEnabled": True,
        }

    def test_readiness_checks_database_without_exposing_errors(self, settings: Settings, tmp_path, monkeypatch) -> None:
        client = self.app(
            settings, env="production", auth_mode_setting="supabase", supabase_url="https://auth.example",
            supabase_anon_key="public-test-key", sqlite_path=tmp_path / "readiness.db",
        )
        response = client.get("/api/ready")
        assert response.status_code == 200
        assert response.json()["databaseReachable"] is True
        assert response.json()["modelStatus"] == "configured_not_probed"

        def unavailable(*args):
            raise RuntimeError("private-connection-error")

        monkeypatch.setattr(client.app.state.services.accounts.store.database, "one", unavailable)
        failed = client.get("/api/ready")
        assert failed.status_code == 503
        assert "private-connection-error" not in failed.text

    @respx.mock
    def test_cross_site_post_is_refused_even_without_a_preflight(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*RIGHT))
        body = {"sessionId": "s", "snapshotVersion": 1, "image": png()}
        assert client.post("/api/analyze", json=body, headers={"origin": "https://evil.example"}).status_code == 403
        assert route.call_count == 0

    @respx.mock
    def test_same_origin_post_is_allowed_through_a_proxy(self, client: TestClient) -> None:
        respx.post(CHAT).mock(return_value=reading(*RIGHT))
        body = {"sessionId": "s", "snapshotVersion": 1, "image": png()}
        allowed = client.post("/api/analyze", json=body, headers={"origin": "http://10.0.0.5:3000", "x-forwarded-host": "10.0.0.5:3000"})
        assert allowed.status_code == 200
        configured = client.post("/api/analyze", json={**body, "snapshotVersion": 2}, headers={"origin": "http://localhost:3000"})
        assert configured.status_code == 200

    def test_chunked_body_is_limited_by_bytes_received(self, client: TestClient) -> None:
        def chunks():
            for _ in range(12):
                yield b"x" * 1_000_000

        response = client.post("/api/analyze", content=chunks(), headers={"content-type": "application/json"})
        assert response.status_code == 413

    def test_validation_errors_do_not_echo_the_input(self, client: TestClient) -> None:
        response = client.post("/api/analyze", json={"sessionId": "s", "image": "secret-handwriting"})
        assert response.status_code == 422
        assert "secret-handwriting" not in response.text

    def test_absurd_version_cannot_poison_a_session(self, client: TestClient) -> None:
        response = client.post("/api/analyze", json={"sessionId": "s", "snapshotVersion": 10**30, "image": png()})
        assert response.status_code == 422

    @respx.mock
    def test_rejected_upload_does_not_move_the_session_version(self, client: TestClient) -> None:
        respx.post(CHAT).mock(return_value=reading(*RIGHT))
        bad = client.post("/api/analyze", json={"sessionId": "s", "snapshotVersion": 999, "image": "data:image/png;base64,AAAA"})
        assert bad.status_code == 400
        assert analyze(client, 1, "s")["stale"] is False

    def test_clearing_unknown_sessions_cannot_evict_real_ones(self, client: TestClient) -> None:
        manager = client.app.state.services.mistakes  # type: ignore[attr-defined]
        for index in range(50):
            client.post("/api/clear", json={"sessionId": f"x{index}", "snapshotVersion": 1})
        assert len(manager._sessions) == 0

    def test_page_context_field_is_gone(self) -> None:
        from quack.schemas import AnalyzeRequest

        assert "page_context_image" not in AnalyzeRequest.model_fields

    def test_global_rate_limit_ignores_forged_addresses(self, settings: Settings) -> None:
        client = self.app(settings, rate_limit_per_minute=1000, global_rate_limit_per_minute=3)
        codes = [
            client.post("/api/clear", json={"sessionId": "s", "snapshotVersion": 1}, headers={"x-forwarded-for": f"10.0.0.{i}"}).status_code
            for i in range(5)
        ]
        assert codes == [200, 200, 200, 429, 429]

    def test_rate_limiter_memory_is_bounded(self) -> None:
        from quack.main import MAX_TRACKED_CLIENTS, RateLimiter

        limiter = RateLimiter(10, 10**9)
        for index in range(MAX_TRACKED_CLIENTS * 2):
            limiter.allow(f"client-{index}")
        assert len(limiter._hits) <= MAX_TRACKED_CLIENTS


class TestBudgetReservation:
    @respx.mock
    def test_concurrent_requests_cannot_all_pass_the_check(self, settings: Settings) -> None:
        import asyncio

        from quack.llm.router import ModelError, ModelRoute, Purpose
        from quack.services.handwriting import HandwritingReading

        # One request's worst case fits the ceiling, but two together don't.
        route = ModelRoute("muse-spark-1.3", "low", 5, 6000)
        ledger = Ledger(None, 0.06, 10, 10)
        router = ModelRouter(settings, ledger)

        async def delayed(_: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.05)
            return reading(*RIGHT)

        respx.post(CHAT).mock(side_effect=delayed)

        async def attempt() -> str:
            try:
                await router.complete_json(Purpose.TRANSCRIBE, [], HandwritingReading, route=route)
            except ModelError as exc:
                return exc.category
            return "ok"

        async def both() -> list[str]:
            return await asyncio.gather(attempt(), attempt())

        assert sorted(asyncio.run(both())) == ["budget_exceeded", "ok"]
        assert ledger._reserved_project == 0

    def test_hourly_limit_stops_a_drain_across_many_sessions(self) -> None:
        from quack.llm.ledger import BudgetExceeded, UsageRecord

        ledger = Ledger(None, 100, 100, 100, hourly_limit=0.10)
        ledger.record(UsageRecord("m", "low", "p", "r", session_id="a", estimated_usd=0.09))
        ledger.check("b", 0.005)
        with pytest.raises(BudgetExceeded):
            ledger.check("c", 0.02)

    def test_old_spend_does_not_count_towards_the_hour(self) -> None:
        import time

        from quack.llm.ledger import UsageRecord

        ledger = Ledger(None, 100, 100, 100, hourly_limit=0.10)
        ledger.record(UsageRecord("m", "low", "p", "r", estimated_usd=0.09, timestamp=time.time() - 3700))
        ledger.check("a", 0.05)

    def test_partial_last_ledger_line_does_not_stop_the_server(self, tmp_path: Any) -> None:
        path = tmp_path / "u.jsonl"
        path.write_text('{"estimated_usd": 1.5}\n{"estimated_us')
        assert Ledger(path, 100, 100, 100).project_spend == 1.5

    def test_corruption_elsewhere_is_not_ignored(self, tmp_path: Any) -> None:
        path = tmp_path / "u.jsonl"
        path.write_text('{"estimated_us\n{"estimated_usd": 1.5}\n')
        with pytest.raises(json.JSONDecodeError):
            Ledger(path, 100, 100, 100)


class TestMistakesOutOfView:
    """An image that doesn't show a mistake's lines can't say it was fixed."""

    @respx.mock(base_url=BASE, assert_all_called=False)
    def test_a_mistake_not_in_the_image_is_not_resolved(self, respx_mock: respx.MockRouter, client: TestClient) -> None:
        respx_mock.post(CHAT).mock(side_effect=[reading(*WRONG), reading(*WRONG), reading(*RIGHT)])
        mistake_id = analyze(client, 1)["mistake"]["id"]
        response = client.post(
            "/api/analyze",
            json={
                "sessionId": "s1",
                "snapshotVersion": 2,
                "image": png(colour="black"),
                "acknowledgedMistakeIds": [],
                "outOfViewMistakeIds": [mistake_id],
            },
        )
        assert response.status_code == 200
        assert response.json()["resolvedMistakeIds"] == []
        assert client.post(
            "/api/hint", json={"sessionId": "s1", "mistakeId": mistake_id, "level": 1}
        ).status_code == 200

    @respx.mock(base_url=BASE, assert_all_called=False)
    def test_a_known_mistake_read_again_does_not_quack_while_marked_out_of_view(
        self, respx_mock: respx.MockRouter, client: TestClient
    ) -> None:
        respx_mock.post(CHAT).mock(side_effect=[reading(*WRONG), reading(*WRONG), reading(*WRONG)])
        mistake_id = analyze(client, 1)["mistake"]["id"]
        response = client.post(
            "/api/analyze",
            json={
                "sessionId": "s1",
                "snapshotVersion": 2,
                "image": png(colour="black"),
                "acknowledgedMistakeIds": [],
                "outOfViewMistakeIds": [mistake_id],
            },
        )
        assert response.json()["intervention"]["shouldQuack"] is False


class TestWorkersAreIndependent:
    @respx.mock
    def test_a_stuck_verification_does_not_fail_anyone_elses(self, client: TestClient, monkeypatch: Any) -> None:
        import asyncio

        from quack.services.math_verifier import LineInput
        from quack.services.verifier_pool import VerificationFailed, VerifierPool

        lines = [LineInput(0, "2(x+3)", continues="no"), LineInput(1, "= 2x+3", continues="yes")]
        pool = VerifierPool()

        async def stuck() -> str:
            import quack.services.verifier_pool as pool_module

            with monkeypatch.context() as patched:
                patched.setattr(pool_module, "_run", _hang)
                task = asyncio.create_task(pool.verify(lines, frozenset(), 1.0))
                await asyncio.sleep(0.1)
            try:
                await task
            except VerificationFailed as exc:
                return str(exc)
            return "finished"

        async def both() -> tuple[str, Any]:
            first = asyncio.create_task(stuck())
            # After the stuck job has started, and the patch that makes it stick has gone.
            await asyncio.sleep(0.3)
            return await asyncio.gather(first, pool.verify(lines, frozenset(), 20.0))

        outcome, (report, _) = asyncio.run(both())
        assert outcome == "timeout"
        assert report.status == "invalid"

    def test_a_timeout_starts_when_the_work_does(self) -> None:
        import asyncio

        from quack.services.math_verifier import LineInput
        from quack.services.verifier_pool import VerifierPool

        lines = [LineInput(0, "2(x+3)", continues="no"), LineInput(1, "= 2x+6", continues="yes")]
        pool = VerifierPool()

        async def many() -> list[Any]:
            return await asyncio.gather(*(pool.verify(lines, frozenset(), 20.0) for _ in range(6)))

        assert all(report.status == "valid" for report, _ in asyncio.run(many()))


class TestResolvingIsConfirmed:
    @pytest.fixture
    def confirming(self, settings: Settings) -> TestClient:
        return TestClient(create_app(build_services(settings.model_copy(update={"confirm_alerts": True}))))

    @respx.mock
    def test_one_misreading_cannot_call_a_mistake_fixed(self, confirming: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = analyze(confirming, 1)["mistake"]["id"]
        route.mock(side_effect=[reading(WRONG[0], line("= 2x + 3", kind="prose")), reading(*WRONG)])
        body = analyze(confirming, 2, image=png(colour="black"), active=mistake_id)
        assert body["resolvedMistakeIds"] == []

    @respx.mock
    def test_two_readings_agreeing_resolve_it(self, confirming: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = analyze(confirming, 1)["mistake"]["id"]
        route.mock(return_value=reading(*RIGHT))
        assert analyze(confirming, 2, image=png(colour="black"), active=mistake_id)["resolvedMistakeIds"] == [mistake_id]

    @respx.mock
    def test_a_failed_confirmation_keeps_the_alert(self, confirming: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = analyze(confirming, 1)["mistake"]["id"]
        route.mock(side_effect=[reading(*RIGHT), httpx.Response(401, json={})])
        assert analyze(confirming, 2, image=png(colour="black"), active=mistake_id)["resolvedMistakeIds"] == []


class TestModelResponseShape:
    @respx.mock
    def test_a_reply_with_no_choices_abstains_and_is_recorded(self, client: TestClient) -> None:
        empty = {"id": "x", "object": "chat.completion", "created": 0, "model": "muse-spark-1.3", "choices": []}
        respx.post(CHAT).mock(return_value=httpx.Response(200, json=empty))
        body = analyze(client, 1)
        assert body["status"] == "uncertain"
        assert body["modelIssue"] is not None


class TestDnsRebinding:
    @pytest.mark.parametrize(("origin", "allowed"), [("https://evil.example", False), ("http://192.168.1.20:3000", True), ("http://localhost:3000", True)])
    def test_a_matching_host_only_counts_on_a_local_address(self, client: TestClient, origin: str, allowed: bool) -> None:
        netloc = origin.split("//")[1]
        response = client.post(
            "/api/transcribe", headers={"origin": origin, "host": netloc}, files={"audio": ("a.wav", b"x", "audio/wav")}
        )
        assert (response.status_code == 403) is not allowed


class TestResolvingNearAmbiguousNotation:
    @respx.mock
    def test_a_fix_is_recognised_beside_a_line_with_two_meanings(self, client: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = analyze(client, 1)["mistake"]["id"]
        route.mock(return_value=reading(*RIGHT, line("y = e^2x", continues_previous="no")))
        assert analyze(client, 2, image=png(colour="black"), active=mistake_id)["resolvedMistakeIds"] == [mistake_id]


class TestLedgerSurvivesItsFile:
    def _ledger(self, path: Any) -> Any:
        from quack.llm.ledger import Ledger

        return Ledger(path, project_limit=100, session_limit=100, request_limit=10)

    def _record(self, usd: float) -> Any:
        from quack.llm.ledger import UsageRecord

        return UsageRecord(model="m", reasoning_effort="low", purpose="p", run_id="r", estimated_usd=usd)

    def test_a_partial_last_line_is_dropped_and_nothing_joins_it(self, tmp_path: Any) -> None:
        path = tmp_path / "usage.jsonl"
        first = self._ledger(path)
        first.record(self._record(1.0))
        with path.open("a") as handle:
            handle.write('{"estimated_usd": 9')
        second = self._ledger(path)
        assert second.project_spend == 1.0
        second.record(self._record(2.0))
        assert self._ledger(path).project_spend == 3.0

    def test_spend_from_another_process_is_seen(self, tmp_path: Any) -> None:
        path = tmp_path / "usage.jsonl"
        server, evals = self._ledger(path), self._ledger(path)
        evals.record(self._record(4.0))
        server.check("", 0.01)
        assert server.project_spend == 4.0
        server.record(self._record(1.0))
        assert server.project_spend == 5.0
        evals.check("", 0.01)
        assert evals.project_spend == 5.0

    def test_a_replaced_file_is_counted_afresh(self, tmp_path: Any) -> None:
        path = tmp_path / "usage.jsonl"
        ledger = self._ledger(path)
        ledger.record(self._record(5.0))
        path.write_text("")
        ledger.record(self._record(7.0))
        assert ledger.project_spend == 7.0

    def test_a_corrupt_line_stops_spending_without_double_counting(self, tmp_path: Any) -> None:
        from quack.llm.ledger import BudgetExceeded

        path = tmp_path / "usage.jsonl"
        ledger = self._ledger(path)
        ledger.record(self._record(1.0))
        with path.open("a") as handle:
            handle.write('{"estimated_usd": 2}\nnot json\n')
        for _ in range(2):
            with pytest.raises(BudgetExceeded):
                ledger.check("", 0.01)
        assert ledger.project_spend == 1.0


class TestWhatBudgetsCount:
    @respx.mock
    def test_a_refused_connection_costs_nothing(self, client: TestClient) -> None:
        respx.post(CHAT).mock(side_effect=httpx.ConnectError("refused"))
        analyze(client, 1)
        assert client.app.state.services.ledger.project_spend == 0  # type: ignore[attr-defined]

    @respx.mock
    def test_a_timeout_is_counted(self, client: TestClient) -> None:
        respx.post(CHAT).mock(side_effect=httpx.ReadTimeout("slow"))
        analyze(client, 1)
        assert client.app.state.services.ledger.project_spend > 0  # type: ignore[attr-defined]


class TestResolvingNeedsARealCheck:
    @pytest.fixture
    def confirming(self, settings: Settings) -> TestClient:
        return TestClient(create_app(build_services(settings.model_copy(update={"confirm_alerts": True}))))

    @respx.mock
    @pytest.mark.parametrize(
        "reread",
        [
            (line("(x + 3)2", continues_previous="no"), line("= 2x + 3")),
            (line("hello", kind="prose"),),
            (line("∫x", continues_previous="no"),),
        ],
    )
    def test_both_readings_failing_the_same_way_fix_nothing(self, confirming: TestClient, reread: tuple[Any, ...]) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = analyze(confirming, 1)["mistake"]["id"]
        route.mock(return_value=reading(*reread))
        assert analyze(confirming, 2, image=png(colour="black"), active=mistake_id)["resolvedMistakeIds"] == []

    @respx.mock
    def test_a_bare_correction_gets_one_answer(self, confirming: TestClient) -> None:
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = analyze(confirming, 1)["mistake"]["id"]
        route.mock(return_value=reading(*WRONG, line("2x + 6", continues_previous="unknown")))
        body = analyze(confirming, 2, image=png(colour="black"), active=mistake_id)
        assert body["resolvedMistakeIds"] == [mistake_id]
        assert body["mistake"] is None

        # And it doesn't quack for a client that has already forgotten the mistake.
        again = analyze(confirming, 3, image=png(colour="red"))
        assert again["intervention"]["shouldQuack"] is False


class TestUnsureReadingsFixNothing:
    @respx.mock
    def test_an_unsure_line_might_be_the_mistake_read_as_what_it_should_say(self, settings: Settings) -> None:
        client = TestClient(create_app(build_services(settings.model_copy(update={"confirm_alerts": True}))))
        route = respx.post(CHAT).mock(return_value=reading(*WRONG))
        mistake_id = analyze(client, 1)["mistake"]["id"]
        unsure = line("= 2x + 6", legibility="unsure", alternatives=["= 2x + 3"])
        route.mock(return_value=reading(WRONG[0], unsure))
        assert analyze(client, 2, image=png(colour="black"), active=mistake_id)["resolvedMistakeIds"] == []

    @respx.mock
    def test_no_oscillation_through_the_whole_pipeline(self, settings: Settings) -> None:
        client = TestClient(create_app(build_services(settings.model_copy(update={"confirm_alerts": True}))))
        page = (line("x + 1 = 3", continues_previous="no"), line("x = 2"), line("1/2 + 1/3 = 2/5", continues_previous="no"))
        respx.post(CHAT).mock(return_value=reading(*page))
        known: list[str] = []
        quacks = 0
        for version in range(1, 7):
            body = analyze(client, version, image=png(colour="black" if version % 2 else "red"), active=known)
            quacks += body["intervention"]["shouldQuack"]
            assert body["resolvedMistakeIds"] == []
            if body["mistake"] and body["mistake"]["id"] not in known:
                known.append(body["mistake"]["id"])
        assert quacks == 1

