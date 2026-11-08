"""Request diagnostics with no bodies, credentials, user ids, or literal URL paths."""

from __future__ import annotations

import json
import logging
import time
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
log = logging.getLogger("quack.http")
_STANDARD_FIELDS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            **{k: v for k, v in record.__dict__.items() if k not in _STANDARD_FIELDS},
        }
        if current := request_id.get():
            payload["request_id"] = current
        return json.dumps(payload, default=str, allow_nan=False)


class RequestDiagnostics:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Always server-generated: a caller cannot inject log content or reuse another request's id.
        correlation = uuid4().hex
        token = request_id.set(correlation)
        started = time.perf_counter()
        status = 500
        response_started = False

        async def observed(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status, response_started = message["status"], True
                headers = list(message.get("headers", []))
                headers = [(k, v) for k, v in headers if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", correlation.encode("ascii")))
                if scope.get("path", "").startswith("/api/"):
                    headers = [(k, v) for k, v in headers if k.lower() != b"cache-control"]
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        try:
            try:
                await self.app(scope, receive, observed)
            except Exception as exc:
                # Exception messages can contain model text, SQL parameters or credentials.
                frames = [
                    {"file": Path(frame.f_code.co_filename).name, "line": line, "function": frame.f_code.co_name}
                    for frame, line in traceback.walk_tb(exc.__traceback__)
                ][-8:]
                log.error("request_failed", extra={"error_type": type(exc).__name__, "frames": frames})
                if response_started:
                    raise
                await JSONResponse({"detail": "internal server error"}, status_code=500)(scope, receive, observed)
        finally:
            route = scope.get("route")
            log.info(
                "http_request",
                extra={
                    "method": scope["method"],
                    "route": getattr(route, "path", "unmatched"),
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            request_id.reset(token)
