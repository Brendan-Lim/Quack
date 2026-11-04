"""Push-to-talk: transcribe a short recording, then work out what the student wants."""

from __future__ import annotations

import io
import json
import logging
import math
import struct
import time
import wave
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict

from quack import prompts
from quack.llm.ledger import BudgetExceeded, UsageRecord
from quack.llm.pricing import VOICE_TRANSCRIBE_MODEL, VOICE_TRANSCRIBE_PER_SECOND
from quack.llm.router import ModelError, ModelRouter, Purpose

log = logging.getLogger("quack.voice")

MAX_SECONDS = 30
TRANSCRIBE_TIMEOUT_S = 30.0


class VoiceRejected(ValueError):
    """The upload is not audio that Meta's endpoint accepts."""


class VoiceIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["hint", "explain", "check", "other"]
    question: str


def clean_wav(raw: bytes) -> tuple[bytes, float]:
    """Check a recording and rebuild it from its PCM frames, returning (wav, seconds).

    Meta accepts mono 16-bit PCM WAV at 16 or 24 kHz. Rebuilding drops any other chunks, and
    any data beyond what the header declares, which would otherwise be sent and billed.
    """

    try:
        with wave.open(io.BytesIO(raw)) as wav:
            channels, width, rate, frames = (
                wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes(),
            )
            if channels != 1 or width != 2 or rate not in (16000, 24000):
                raise VoiceRejected("expected mono 16-bit PCM at 16 or 24 kHz")
            seconds = frames / rate
            if seconds > MAX_SECONDS:
                raise VoiceRejected("recording too long")
            if seconds <= 0:
                raise VoiceRejected("recording is empty")
            pcm = wav.readframes(frames)
    except (wave.Error, EOFError, RuntimeError, ValueError, struct.error) as exc:
        if isinstance(exc, VoiceRejected):
            raise
        raise VoiceRejected("expected a WAV recording") from exc

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buffer.getvalue(), seconds


async def transcribe_wav(router: ModelRouter, raw: bytes, *, session_id: str = "", run_id: str = "runtime") -> str:
    cleaned, seconds = clean_wav(raw)
    settings = router.settings
    if not settings.model_api_key:
        raise ModelError("no_api_key", "MODEL_API_KEY is not configured")

    cost = math.ceil(seconds) * VOICE_TRANSCRIBE_PER_SECOND
    try:
        with router.ledger.reserve(session_id, cost):
            return await _send_wav(router, cleaned, cost, session_id, run_id)
    except BudgetExceeded as exc:
        raise ModelError("budget_exceeded", str(exc)) from exc


async def _send_wav(router: ModelRouter, cleaned: bytes, cost: float, session_id: str, run_id: str) -> str:
    settings = router.settings
    record = UsageRecord(
        model=VOICE_TRANSCRIBE_MODEL, reasoning_effort="n/a", purpose="voice_transcribe",
        run_id=run_id, session_id=session_id,
    )
    settings_part = {"mode": "PUSH_TO_TALK", "model": VOICE_TRANSCRIBE_MODEL, "audioEncoding": "WAV"}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=TRANSCRIBE_TIMEOUT_S) as client:
            response = await client.post(
                f"{settings.model_api_base_url}/asr/transcribe",
                headers={"Authorization": f"Bearer {settings.model_api_key}", "Accept": "application/json"},
                files={
                    "request": (None, json.dumps(settings_part), "application/json"),
                    "audio": ("recording.wav", cleaned, "audio/wav"),
                },
            )
    except httpx.TimeoutException as exc:
        record.error_category = "timeout"
        router.ledger.record(record)
        raise ModelError("timeout", "transcription timed out") from exc
    except httpx.HTTPError as exc:
        record.error_category = "network"
        router.ledger.record(record)
        raise ModelError("network", "transcription request failed") from exc
    finally:
        record.elapsed_ms = (time.perf_counter() - started) * 1000

    record.http_status = response.status_code
    if response.status_code != 200:
        category = {401: "auth", 403: "auth", 429: "rate_limited"}.get(
            response.status_code, "server_error" if response.status_code >= 500 else "invalid_request"
        )
        record.error_category = category
        router.ledger.record(record)
        raise ModelError(category, f"transcription failed with {response.status_code}", response.status_code)

    record.estimated_usd = cost
    router.ledger.record(record)
    try:
        body = response.json()
        return str(body["transcript"]).strip()
    except (ValueError, KeyError, TypeError) as exc:
        raise ModelError("invalid_output", "unexpected transcription response") from exc


async def classify_intent(router: ModelRouter, transcript: str, *, session_id: str = "") -> VoiceIntent:
    messages = [
        {"role": "developer", "content": prompts.load_prompt(prompts.VOICE_INTENT)},
        {"role": "user", "content": json.dumps({"transcript": transcript}, ensure_ascii=False)},
    ]
    result = await router.complete_json(Purpose.VOICE_INTENT, messages, VoiceIntent, session_id=session_id)
    return result.value
