"""A stand-in for Meta's API, for browser tests only. It never reads the image.

Tests tell it what the "model" should say, so the browser, Next proxy, FastAPI, the real
verifier and the real dedupe logic are all exercised. How well the real model reads
handwriting is a separate question, answered only by evals/ with a real key.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()


class Script:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.reading: dict[str, Any] = {"lines": []}
        self.queue: list[dict[str, Any]] = []
        self.help_text: list[str] = []
        self.delay_ms = 0
        self.status = 200
        self.reading_requests = 0
        self.help_requests = 0
        self.auth_headers: set[str] = set()
        self.line_counts: list[int] = []
        # How many samples of the student's own handwriting each reading was shown.
        self.reference_counts: list[int] = []
        self.images_sent: list[int] = []
        # What a second reading at `low` effort does: None to answer, or "truncated" to use up all its
        # tokens, as they did on calculus in the iPad session of 2026-11-02.
        self.second_reading: str | None = None
        self.efforts: list[str] = []


script = Script()


def completion(content: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "fake",
        "object": "chat.completion",
        "created": 0,
        "model": "muse-spark-1.3",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
    }


@app.post("/__script")
async def set_script(request: Request) -> dict[str, bool]:
    body = await request.json()
    if body.get("reset"):
        script.reset()
    for key in ("reading", "queue", "delay_ms", "status", "second_reading"):
        if key in body:
            setattr(script, key, body[key])
    if "help" in body:
        script.help_text = body["help"]
    return {"ok": True}


@app.get("/__stats")
async def stats() -> dict[str, Any]:
    return {
        "readingRequests": script.reading_requests,
        "helpRequests": script.help_requests,
        "authHeaders": sorted(script.auth_headers),
        "lineCounts": script.line_counts,
        "referenceCounts": script.reference_counts,
        "imagesSent": script.images_sent,
        "efforts": script.efforts,
    }


@app.post("/v1/chat/completions")
async def chat(request: Request) -> JSONResponse:
    script.auth_headers.add(request.headers.get("authorization", ""))
    body = await request.json()
    schema = body["response_format"]["json_schema"]["name"]
    is_reading = schema in ("HandwritingReading", "CompactReading")
    if is_reading:
        script.reading_requests += 1
        parts = body["messages"][1]["content"]
        said = " ".join(part["text"] for part in parts if part["type"] == "text")
        told = re.search(r"It has (\d+) written", said)
        if told:
            script.line_counts.append(int(told[1]))
        script.reference_counts.append(said.count("Reference sample"))
        script.images_sent.append(sum(part["type"] == "image_url" for part in parts))
        script.efforts.append(body.get("reasoning_effort", ""))
        if body.get("reasoning_effort") == "low" and script.second_reading == "truncated":
            ran_out = completion({})
            ran_out["choices"][0].update({"finish_reason": "length", "message": {"role": "assistant", "content": ""}})
            return JSONResponse(ran_out)
    else:
        script.help_requests += 1
    # Read the delay and failure now, so that a test changing them mid-request sees the
    # original request behave as it was scripted.
    delay, status = script.delay_ms, script.status
    reading = script.queue.pop(0) if is_reading and script.queue else script.reading
    if delay:
        await asyncio.sleep(delay / 1000)
    if status != 200:
        return JSONResponse({"error": {"message": "scripted failure"}}, status_code=status)
    if schema == "CompactReading":
        # What a compact reading doesn't ask for, it mustn't be given.
        kept = {"math", "kind", "legibility", "alternatives", "continues_previous", "operation"}
        return JSONResponse(completion({"lines": [{k: v for k, v in line.items() if k in kept} for line in reading["lines"]]}))
    if is_reading:
        return JSONResponse(completion(reading))
    level = 3 if body["response_format"]["json_schema"]["name"] == "GeneratedHelp" and "correct_result" in body["messages"][1]["content"] and json.loads(body["messages"][1]["content"].split("\n\n")[0])["requested_level"] == 3 else 1
    text = script.help_text.pop(0) if script.help_text else "Look at this step again."
    if schema == "GeneratedAnswer":
        return JSONResponse(completion({"text": text}))
    return JSONResponse(completion({"level": level, "text": text, "references_step": "scripted"}))
