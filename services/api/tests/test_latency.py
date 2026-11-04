"""How long a check makes the student wait, with Meta's API mocked to take a known time.

The real model's speed is measured live, in docs/LATENCY_AND_AUTOCHECK_REPORT.md. These pin
down what our own code adds to it: which readings run at once, which are shared, and which a
reply waits for.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import pytest
import respx

from quack.config import Settings
from quack.llm.ledger import Ledger
from quack.llm.router import ModelRouter
from quack.schemas import AnalyzeRequest
from quack.services.analysis import AnalysisService
from quack.services.mistake_manager import MistakeManager
from quack.services.verifier_pool import shared_pool
from tests.test_pipeline import CHAT, RIGHT, WRONG, line, png, reading

READ_S = 0.6


def service(settings: Settings, **overrides: Any) -> AnalysisService:
    tuned = settings.model_copy(update={"confirm_alerts": True, **overrides})
    ledger = Ledger(tuned.ledger_path, 100, 100, 100, 100)
    return AnalysisService(tuned, ModelRouter(tuned, ledger), MistakeManager())


def request(version: int = 1, image: str | None = None, known: list[str] | None = None) -> AnalyzeRequest:
    return AnalyzeRequest(
        session_id="s", snapshot_version=version, image=image or png(), acknowledged_mistake_ids=known or []
    )


def slow(*replies: httpx.Response, delays: tuple[float, ...] | None = None):
    """Answer each call in turn, after a delay, recording which purpose asked."""

    queue = list(replies)
    waits = list(delays or (READ_S,) * len(replies))
    purposes: list[str] = []

    async def answer(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        purposes.append(body["reasoning_effort"])
        reply, wait = queue.pop(0), waits.pop(0)
        await asyncio.sleep(wait)
        return reply

    return answer, purposes


@pytest.fixture(scope="module", autouse=True)
def _warm_verifier() -> None:
    shared_pool.warm()


@respx.mock
async def test_an_alert_waits_for_the_slower_reading_not_both(settings: Settings) -> None:
    answer, _ = slow(reading(*WRONG), reading(*WRONG))
    route = respx.post(CHAT).mock(side_effect=answer)
    started = time.perf_counter()
    body = await service(settings).analyze(request())
    elapsed = time.perf_counter() - started
    assert body.status == "confirmed_error"
    assert route.call_count == 2
    # One after the other would be at least two readings' worth.
    assert elapsed < READ_S * 2, f"{elapsed:.2f}s: the readings ran one after the other"


@respx.mock
async def test_in_sequence_an_alert_waits_for_both(settings: Settings) -> None:
    answer, _ = slow(reading(*WRONG), reading(*WRONG))
    respx.post(CHAT).mock(side_effect=answer)
    started = time.perf_counter()
    await service(settings, confirm_in_parallel=False).analyze(request())
    assert time.perf_counter() - started >= READ_S * 2


@respx.mock
async def test_correct_work_does_not_wait_for_the_second_reading(settings: Settings) -> None:
    answer, _ = slow(reading(*RIGHT), reading(*RIGHT), delays=(READ_S, 3.0))
    respx.post(CHAT).mock(side_effect=answer)
    started = time.perf_counter()
    body = await service(settings).analyze(request())
    assert body.status == "no_error"
    assert time.perf_counter() - started < 1.5


@respx.mock
async def test_a_fix_waits_for_both_readings_and_needs_both(settings: Settings) -> None:
    analysis = service(settings)
    respx.post(CHAT).mock(return_value=reading(*WRONG))
    old = (await analysis.analyze(request(1))).mistake
    assert old is not None
    answer, _ = slow(reading(*RIGHT), reading(*WRONG))
    respx.post(CHAT).mock(side_effect=answer)
    body = await analysis.analyze(request(2, image=png(colour="black"), known=[old.id]))
    assert body.resolved_mistake_ids == []


@respx.mock
async def test_a_disputed_fix_says_nothing_is_settled(settings: Settings) -> None:
    analysis = service(settings)
    respx.post(CHAT).mock(return_value=reading(*WRONG))
    old = (await analysis.analyze(request(1))).mistake
    assert old is not None
    respx.post(CHAT).mock(side_effect=[reading(*RIGHT), reading(*WRONG)])
    body = await analysis.analyze(request(2, image=png(colour="black"), known=[old.id]))
    assert body.status == "uncertain"
    assert body.reason == "fix_disputed"


@respx.mock
async def test_a_fix_the_second_reading_cant_make_out_is_not_called_disputed(settings: Settings) -> None:
    analysis = service(settings)
    respx.post(CHAT).mock(return_value=reading(*WRONG))
    old = (await analysis.analyze(request(1))).mistake
    assert old is not None
    unclear = reading(RIGHT[0], {**RIGHT[1], "legibility": "unsure", "alternatives": ["= 2x + 8"]})
    respx.post(CHAT).mock(side_effect=[reading(*RIGHT), unclear])
    body = await analysis.analyze(request(2, image=png(colour="black"), known=[old.id]))
    assert (body.status, body.reason, body.resolved_mistake_ids) == ("uncertain", "fix_unconfirmed", [])


@respx.mock
async def test_identical_lines_are_placed_by_position(settings: Settings) -> None:
    same = line("1 + 1 = 3", continues_previous="no", operation="arithmetic")
    respx.post(CHAT).mock(return_value=reading(line("2 + 2 = 4", continues_previous="no", operation="arithmetic"), same))
    body = await service(settings, confirm_alerts=False).analyze(request())
    assert body.mistake is not None and body.mistake.region is not None
    assert body.mistake.region.line == 1


@respx.mock
async def test_a_fix_whose_confirmation_failed_reports_it_so_it_can_be_tried_again(settings: Settings) -> None:
    analysis = service(settings)
    respx.post(CHAT).mock(return_value=reading(*WRONG))
    old = (await analysis.analyze(request(1))).mistake
    assert old is not None
    respx.post(CHAT).mock(side_effect=[reading(*RIGHT), httpx.Response(401, json={"error": {"message": "no"}})])
    body = await analysis.analyze(request(2, image=png(colour="black"), known=[old.id]))
    assert body.resolved_mistake_ids == []
    assert body.model_issue is not None and body.model_issue.category == "auth"


@respx.mock
async def test_a_mistake_reports_its_place_among_the_lines_read(settings: Settings) -> None:
    respx.post(CHAT).mock(return_value=reading(*WRONG))
    body = await service(settings).analyze(request())
    assert body.mistake is not None and body.mistake.region is not None
    assert (body.mistake.region.line, body.mistake.region.line_count) == (1, 2)


@respx.mock
async def test_the_same_image_twice_at_once_is_read_once(settings: Settings) -> None:
    answer, _ = slow(reading(*WRONG), reading(*WRONG), reading(*WRONG), reading(*WRONG))
    route = respx.post(CHAT).mock(side_effect=answer)
    analysis = service(settings)
    first, second = await asyncio.gather(analysis.analyze(request(1)), analysis.analyze(request(1)))
    assert route.call_count == 2
    assert first.status == second.status == "confirmed_error"
    assert second.timings.cache_hit or first.timings.cache_hit


@respx.mock
async def test_a_confirmation_that_failed_early_is_not_paid_for_again(settings: Settings) -> None:
    route = respx.post(CHAT).mock(side_effect=[reading(*WRONG), httpx.Response(401, json={"error": {"message": "no"}})])
    body = await service(settings).analyze(request())
    assert route.call_count == 2
    assert body.model_issue is not None and body.model_issue.category == "auth"
    assert body.intervention.should_quack is False


@respx.mock
async def test_a_reading_that_times_out_is_not_retried_while_the_student_waits(settings: Settings) -> None:
    route = respx.post(CHAT).mock(side_effect=httpx.TimeoutException("slow"))
    body = await service(settings, confirm_alerts=False).analyze(request())
    assert route.call_count == 1
    assert body.model_issue is not None and body.model_issue.category == "timeout"


@respx.mock
async def test_timings_say_where_the_time_went(settings: Settings) -> None:
    answer, _ = slow(reading(*WRONG), reading(*WRONG))
    respx.post(CHAT).mock(side_effect=answer)
    body = await service(settings).analyze(request())
    stages = body.timings.stages
    assert list(stages) == ["sanitised", "read", "verified", "alert_confirmed", "decided"]
    assert [call.purpose for call in body.timings.calls] == ["transcribe", "transcribe_confirm"]
    assert all(call.ms >= 0 and not call.cached for call in body.timings.calls)

