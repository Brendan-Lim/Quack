"""Release boundaries: bad configuration, ledger integrity, retries and private diagnostics."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from quack.config import Settings
from quack.llm.ledger import BudgetExceeded, Ledger, UsageRecord
from quack.llm.router import DEFAULT_ROUTES, ModelError, ModelRouter, Purpose
from quack.main import RateLimiter, build_services, create_app
from quack.observability import JsonFormatter, RequestDiagnostics, request_id
from quack.schemas import AnalyzeResponse, Mistake
from quack.services.hint_engine import _log_failure
from quack.services.verifier_pool import _child


@pytest.mark.parametrize("name", [
    "budget_project_usd", "budget_session_usd", "budget_request_usd", "budget_hourly_usd",
    "reading_cache_size", "line_memory_size", "verify_timeout_s", "second_reading_patience_s",
    "rate_limit_per_minute", "global_rate_limit_per_minute",
])
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_invalid_limits_fail_at_configuration(name: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: value})


@pytest.mark.parametrize("name", ["budget_project_usd", "budget_session_usd", "budget_request_usd", "budget_hourly_usd"])
def test_zero_budget_is_an_explicit_spending_stop(name: str) -> None:
    assert getattr(Settings(_env_file=None, **{name: 0}), name) == 0


@pytest.mark.parametrize("name", ["rate_limit_per_minute", "global_rate_limit_per_minute", "verify_timeout_s"])
def test_zero_rate_or_timeout_is_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: 0})


@pytest.mark.parametrize("overrides", [
    {"max_tokens": 0}, {"max_tokens": -1}, {"max_tokens": True}, {"max_tokens": 1.5},
    {"timeout_s": 0}, {"timeout_s": float("nan")}, {"timeout_s": float("inf")},
    {"model": "unpriced"}, {"reasoning_effort": "typo"}, {"retry_timeouts": "false"},
])
def test_invalid_model_routes_fail_before_a_request(overrides: dict) -> None:
    with pytest.raises(ValueError):
        replace(DEFAULT_ROUTES[Purpose.HINT], **overrides)


def usage(cost: float, **fields) -> UsageRecord:
    return UsageRecord("m", "low", "p", "r", estimated_usd=cost, **fields)


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True, "0.1", None])
def test_corrupt_ledger_amount_stops_spending_atomically(tmp_path: Path, bad: object) -> None:
    path = tmp_path / "usage.jsonl"
    ledger = Ledger(path, 100, 100, 100)
    ledger.record(usage(1))
    with path.open("a") as handle:
        handle.write(json.dumps({"estimated_usd": 2}) + "\n")
        handle.write(json.dumps({"estimated_usd": bad}) + "\n")
    for _ in range(2):
        with pytest.raises(BudgetExceeded, match="ledger"):
            ledger.check("s", .01)
        assert ledger.project_spend == 1  # retries cannot count the valid prefix twice


@pytest.mark.parametrize("record", [[], None, 5, {"session_id": []}, {"timestamp": "yesterday"}])
def test_invalid_ledger_structure_stops_spending(tmp_path: Path, record: object) -> None:
    path = tmp_path / "usage.jsonl"
    ledger = Ledger(path, 100, 100, 100)
    path.write_text(json.dumps(record) + "\n")
    with pytest.raises(BudgetExceeded, match="ledger"):
        ledger.check("s", .01)


@pytest.mark.parametrize("bad", [-1, float("inf"), float("nan"), True])
def test_invalid_reservations_cannot_increase_the_budget(bad: float) -> None:
    ledger = Ledger(None, 1, 1, 1)
    with pytest.raises(ValueError), ledger.reserve("s", bad):
        pytest.fail("reservation accepted")
    assert ledger._reserved_project == 0
    assert dict(ledger._reserved_session) == {}


def test_reservation_is_released_on_failure_and_cancellation() -> None:
    ledger = Ledger(None, 1, 1, 1)
    for error in (RuntimeError, asyncio.CancelledError):
        with pytest.raises(error), ledger.reserve("s", .5):
            raise error()
        assert ledger._reserved_project == 0
        assert dict(ledger._reserved_session) == {}
        ledger.check("s", 1)


def test_out_of_order_timestamps_do_not_pin_expired_hourly_spend() -> None:
    ledger = Ledger(None, 100, 100, 100, hourly_limit=1)
    ledger.record(usage(.1))
    ledger.record(usage(.95, timestamp=time.time() - 7200))
    ledger.check("s", .8)
    assert ledger.project_spend == pytest.approx(1.05)


def test_failed_ledger_write_counts_call_and_blocks_further_spend(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "usage.jsonl", 100, 100, 100)
    with patch.object(Path, "open", side_effect=OSError("disk full")):
        ledger.record(usage(.3, session_id="s"))
    assert ledger.project_spend == .3
    assert ledger.session_spend("s") == .3
    with pytest.raises(BudgetExceeded, match="ledger"):
        ledger.check("s", .01)


class Answer(BaseModel):
    answer: str


def completion(content: str = '{"answer":"ok"}') -> httpx.Response:
    return httpx.Response(200, json={
        "id": "c", "object": "chat.completion", "created": 0, "model": "muse-spark-1.3",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        # Deliberately no usage: charge the conservative fallback.
    })


@respx.mock
@pytest.mark.parametrize("failure", ["timeout", "invalid_output"])
async def test_retry_must_fit_remaining_budget(settings: Settings, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    monkeypatch.setattr("quack.llm.router._retry_delay", lambda *_: 0)
    ledger = Ledger(None, .06, 10, 10)
    router = ModelRouter(settings, ledger)
    response = httpx.ReadTimeout("slow") if failure == "timeout" else completion("not JSON")
    route = respx.post("https://api.test/v1/chat/completions").mock(side_effect=[response, completion()])
    with pytest.raises(ModelError) as caught:
        await router.complete_json(
            Purpose.EXPLAIN, [], Answer, session_id="s",
            route=replace(DEFAULT_ROUTES[Purpose.EXPLAIN], retry_timeouts=True),
        )
    assert caught.value.category == "budget_exceeded"
    assert route.call_count == 1
    assert ledger.project_spend > 0
    assert ledger._reserved_project == 0


@respx.mock
async def test_model_cancellation_releases_reservation(settings: Settings) -> None:
    ledger = Ledger(None, 1, 1, 1)
    router = ModelRouter(settings, ledger)
    called = asyncio.Event()

    async def waiting(_: httpx.Request) -> httpx.Response:
        called.set()
        await asyncio.Event().wait()
        return completion()

    respx.post("https://api.test/v1/chat/completions").mock(side_effect=waiting)
    task = asyncio.create_task(router.complete_json(Purpose.HINT, [], Answer, session_id="s"))
    await called.wait()
    assert ledger._reserved_project > 0
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ledger._reserved_project == 0
    assert dict(ledger._reserved_session) == {}
    assert ledger.project_spend > 0  # cancellation cannot erase potentially billable work


def test_rate_windows_expire_at_the_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 100.
    monkeypatch.setattr("quack.main.time.monotonic", lambda: now)
    limiter = RateLimiter(1, 2)
    assert limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")
    assert not limiter.allow("c")
    now += 59.999
    assert not limiter.allow("a")
    now += .001
    assert limiter.allow("a")
    assert limiter.allow("c")


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(build_services(settings)))


@pytest.fixture
def log_output(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(JsonFormatter())
    monkeypatch.setattr(logging.getLogger("quack"), "handlers", [handler])
    return output


@pytest.mark.parametrize("path, method, expected", [
    ("/api/health", "GET", 200), ("/api/not-real/private-id", "GET", 404),
    ("/api/analyze", "POST", 422), ("/api/health", "POST", 405),
])
def test_api_responses_have_private_correlation_headers(client: TestClient, path: str, method: str, expected: int) -> None:
    response = client.request(method, path, headers={"x-request-id": "forged-id"})
    assert response.status_code == expected
    assert len(response.headers["x-request-id"]) == 32
    assert response.headers["x-request-id"] != "forged-id"
    assert response.headers["cache-control"] == "no-store"
    assert request_id.get() is None


@pytest.mark.parametrize("origin", ["http://[broken", "ftp://10.0.0.1", "http://name@10.0.0.1", "null"])
def test_malformed_or_untrusted_origin_is_refused(client: TestClient, origin: str) -> None:
    response = client.post("/api/analyze", json={}, headers={"origin": origin, "x-forwarded-host": "10.0.0.1"})
    assert response.status_code == 403
    assert response.headers["x-request-id"]


def test_request_logs_never_include_private_url_or_body(client: TestClient, log_output: io.StringIO) -> None:
    response = client.post(
        "/api/analyze?secret=private-query", json={"image": "private-image"},
        headers={"authorization": "Bearer private-token"},
    )
    events = [json.loads(line) for line in log_output.getvalue().splitlines() if line.startswith("{")]
    event = next(item for item in events if item["event"] == "http_request")
    assert event["request_id"] == response.headers["x-request-id"]
    assert event["route"] == "/api/analyze"
    assert event["duration_ms"] >= 0
    assert event["status"] == 422
    assert "private-" not in json.dumps(events)


def test_unhandled_exception_has_safe_response_and_diagnostic(log_output: io.StringIO) -> None:
    app = FastAPI()
    app.add_middleware(RequestDiagnostics)

    @app.get("/api/private/{user_id}")
    def broken(user_id: str) -> None:
        raise RuntimeError("credential-and-handwriting")

    response = TestClient(app).get("/api/private/user-secret")
    assert response.status_code == 500
    assert response.json() == {"detail": "internal server error"}
    assert response.headers["x-request-id"]
    output = log_output.getvalue()
    events = [json.loads(line) for line in output.splitlines()]
    failure = next(event for event in events if event["event"] == "request_failed")
    assert failure["error_type"] == "RuntimeError"
    assert failure["frames"][-1]["function"] == "broken"
    assert failure["frames"][-1]["line"] > 0
    assert failure["request_id"] == response.headers["x-request-id"]
    assert "credential-and-handwriting" not in output
    assert "user-secret" not in output
    assert request_id.get() is None


async def test_concurrent_requests_keep_distinct_log_contexts() -> None:
    app = FastAPI()
    app.add_middleware(RequestDiagnostics)
    both = asyncio.Event()
    ids: list[str | None] = []

    @app.get("/api/check")
    async def check() -> dict:
        current = request_id.get()
        ids.append(current)
        if len(ids) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), timeout=2)
        assert request_id.get() == current
        return {"id": current}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        replies = await asyncio.gather(client.get("/api/check"), client.get("/api/check"))
    assert len(set(ids)) == 2
    assert all(reply.json()["id"] == reply.headers["x-request-id"] for reply in replies)
    assert request_id.get() is None


def test_json_log_format_includes_timestamp_and_structured_fields() -> None:
    record = logging.makeLogRecord({"name": "quack.test", "msg": "test_event", "levelname": "INFO", "count": 3})
    result = json.loads(JsonFormatter().format(record))
    assert result["event"] == "test_event"
    assert result["count"] == 3
    assert result["timestamp"].endswith("+00:00")


def mistake(**overrides) -> Mistake:
    return Mistake(**{
        "id": "m1", "category": "arithmetic", "error_type": "arithmetic_error",
        "operation_type": "arithmetic", "previous_step": "2 * 3", "current_step": "5",
        "verified": True, "is_new": True, **overrides,
    })


@pytest.mark.parametrize("fields", [
    {"intervention": {"shouldQuack": True}},
    {"mistakes": [mistake(verified=False)]},
    {"mistakes": [mistake(), mistake()]},
    {"mistake": mistake()},
    {"mistakes": [mistake()], "mistake": mistake(verified=False)},
    {"mistakes": [mistake()], "mistake": mistake(current_step="different evidence")},
    {"mistakes": [mistake()], "resolvedMistakeIds": ["m1"]},
    {"mistakes": [mistake()], "status": "no_error", "intervention": {"shouldQuack": True}},
    {"mistakes": [mistake()], "stale": True, "intervention": {"shouldQuack": True}},
    {"mistakes": [mistake(is_new=False)], "intervention": {"shouldQuack": True}},
])
def test_unsafe_alert_contract_is_rejected(fields: dict) -> None:
    with pytest.raises(ValidationError):
        AnalyzeResponse.model_validate({"snapshotVersion": 1, "status": "confirmed_error", **fields})


def test_valid_alert_and_silent_acknowledgement_pass_the_contract() -> None:
    alert = AnalyzeResponse(
        snapshot_version=1, status="confirmed_error", mistakes=[mistake()], mistake=mistake(),
        intervention={"shouldQuack": True},
    )
    assert alert.intervention.should_quack
    acknowledged = AnalyzeResponse(
        snapshot_version=2, status="confirmed_error", mistakes=[mistake(is_new=False)],
    )
    assert not acknowledged.intervention.should_quack


def test_alert_validation_remains_active_with_python_assertions_disabled() -> None:
    result = subprocess.run([
        sys.executable, "-O", "-c",
        "from quack.schemas import AnalyzeResponse\n"
        "from pydantic import ValidationError\n"
        "try:\n"
        " AnalyzeResponse(snapshot_version=1, status='no_error', intervention={'shouldQuack': True})\n"
        "except ValidationError:\n"
        " raise SystemExit(0)\n"
        "raise SystemExit('unsafe alert passed optimized Python')\n",
    ], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_cors_exposes_server_generated_request_id(client: TestClient, settings: Settings) -> None:
    response = client.get("/api/health", headers={"origin": settings.cors_origin_list[0]})
    assert response.headers["access-control-allow-origin"] == settings.cors_origin_list[0]
    assert "x-request-id" in response.headers["access-control-expose-headers"]
    assert len(response.headers["x-request-id"]) == 32


def test_background_failures_never_log_private_expressions(log_output: io.StringIO) -> None:
    def broken() -> None:
        raise ValueError("private-student-expression")

    connection = Mock()
    with patch("quack.services.verifier_pool._limit_resources"):
        _child(connection, broken)
    assert connection.send.call_args_list[-1].args == (("error", "ValueError"),)
    assert "private-student-expression" not in str(connection.mock_calls)
    connection.close.assert_called_once()
    task = Mock()
    task.cancelled.return_value = False
    task.exception.return_value = ValueError("private-student-expression")
    _log_failure(task)
    event = json.loads(log_output.getvalue().splitlines()[-1])
    assert event["event"] == "hint_preparation_failed"
    assert event["error_type"] == "ValueError"
    assert "private-student-expression" not in log_output.getvalue()
