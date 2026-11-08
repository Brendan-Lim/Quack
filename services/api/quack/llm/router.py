"""The single route to Meta's API: picks a model per purpose, retries, and accounts for cost.

A route is plain data. There is no agent framework here on purpose.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Generic, TypeVar, cast

import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.shared import ReasoningEffort
from openai.types.shared_params import ResponseFormatJSONSchema
from pydantic import BaseModel, ValidationError

from quack.config import Settings
from quack.llm.ledger import BudgetExceeded, Ledger, UsageRecord
from quack.llm.pricing import ALLOWED_TOKEN_MODELS, token_cost, worst_case_output_cost

log = logging.getLogger("quack.router")
T = TypeVar("T", bound=BaseModel)

MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 0.5
MAX_RETRY_AFTER_S = 8.0
# Input is small and mostly cached, so budget checks allow a generous fixed amount.
ASSUMED_MAX_INPUT_USD = 0.02


class Purpose(StrEnum):
    TRANSCRIBE = "transcribe"
    TRANSCRIBE_CONFIRM = "transcribe_confirm"
    HINT = "hint"
    EXPLAIN = "explain"
    VOICE_INTENT = "voice_intent"


@dataclass(frozen=True)
class ModelRoute:
    model: str
    reasoning_effort: str
    timeout_s: float
    max_tokens: int
    # A student is waiting. After a timeout, the next pause tries again sooner than a retry would end.
    retry_timeouts: bool = True

    def __post_init__(self) -> None:
        if self.model not in ALLOWED_TOKEN_MODELS:
            raise ValueError("model has no known price")
        if self.reasoning_effort not in ("none", "minimal", "low", "medium", "high"):
            raise ValueError("invalid reasoning effort")
        if isinstance(self.timeout_s, bool) or not math.isfinite(self.timeout_s) or self.timeout_s <= 0:
            raise ValueError("model timeout must be finite and positive")
        if type(self.max_tokens) is not int or self.max_tokens <= 0:
            raise ValueError("model token limit must be a positive integer")
        if type(self.retry_timeouts) is not bool:
            raise ValueError("retry_timeouts must be a boolean")


# Reading is timed per call in docs/IPAD_TEST_ROUND_2.md. The two readings run at different efforts
# on purpose: identical ones could share a misreading, and the second is what stops an accusation.
DEFAULT_ROUTES: dict[Purpose, ModelRoute] = {
    Purpose.TRANSCRIBE: ModelRoute("muse-spark-1.3", "minimal", 20.0, 4000, retry_timeouts=False),
    Purpose.TRANSCRIBE_CONFIRM: ModelRoute("muse-spark-1.3", "low", 20.0, 4000, retry_timeouts=False),
    Purpose.HINT: ModelRoute("muse-spark-1.3", "low", 20.0, 3000),
    # Not tried again after a timeout: a student is watching "Thinking...", and the verifier's own
    # wording is ready at once. On 2026-11-03 two 30 s timeouts in a row kept one waiting 75 s.
    Purpose.EXPLAIN: ModelRoute("muse-spark-1.3", "medium", 20.0, 6000, retry_timeouts=False),
    Purpose.VOICE_INTENT: ModelRoute("muse-spark-1.3", "minimal", 15.0, 1500),
}


class ModelError(Exception):
    """A request failed. `category` is safe to return to the client; details are not."""

    def __init__(self, category: str, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.status = status


@dataclass(frozen=True)
class StructuredResult(Generic[T]):
    value: T
    record: UsageRecord


def _load_routes(settings: Settings) -> dict[Purpose, ModelRoute]:
    routes = dict(DEFAULT_ROUTES)
    if settings.routes_json:
        for name, overrides in json.loads(settings.routes_json).items():
            purpose = Purpose(name)
            routes[purpose] = replace(routes[purpose], **overrides)
    for purpose, route in routes.items():
        if route.model not in ALLOWED_TOKEN_MODELS:
            raise ValueError(f"route {purpose} uses {route.model}, which has no known price")
    return routes


def _categorise(exc: Exception) -> tuple[str, bool, int | None]:
    """Map an SDK error to (category, retryable, status)."""

    if isinstance(exc, openai.APITimeoutError | TimeoutError):
        return "timeout", True, None
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return "auth", False, exc.status_code
    if isinstance(exc, openai.RateLimitError):
        return "rate_limited", True, exc.status_code
    if isinstance(exc, openai.BadRequestError | openai.UnprocessableEntityError | openai.NotFoundError):
        return "invalid_request", False, exc.status_code
    if isinstance(exc, openai.InternalServerError):
        return "server_error", True, exc.status_code
    if isinstance(exc, openai.APIConnectionError):
        return "network", True, None
    if isinstance(exc, openai.APIStatusError):
        return "api_error", False, exc.status_code
    return "unknown", False, None


def _retry_delay(attempt: int, exc: Exception) -> float | None:
    """Seconds to wait before the next attempt, or None if the server asks for too long."""

    backoff = BACKOFF_BASE_S * 2 ** (attempt - 1) + random.uniform(0, BACKOFF_BASE_S)
    if isinstance(exc, openai.APIStatusError):
        header = exc.response.headers.get("retry-after")
        if header:
            try:
                wait = float(header)
            except ValueError:
                return backoff
            return None if wait > MAX_RETRY_AFTER_S else max(wait, backoff)
    return backoff


def _usage_fields(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    completion_details = getattr(usage, "completion_tokens_details", None)
    return {
        "input_tokens": usage.prompt_tokens or 0,
        "cached_input_tokens": getattr(prompt_details, "cached_tokens", 0) or 0,
        "output_tokens": usage.completion_tokens or 0,
        "reasoning_tokens": getattr(completion_details, "reasoning_tokens", 0) or 0,
    }


class ModelRouter:
    def __init__(self, settings: Settings, ledger: Ledger, client: AsyncOpenAI | None = None) -> None:
        self.settings = settings
        self.ledger = ledger
        self.routes = _load_routes(settings)
        self._client = client

    @property
    def configured(self) -> bool:
        return self._client is not None or bool(self.settings.model_api_key)

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            if not self.settings.model_api_key:
                raise ModelError("no_api_key", "MODEL_API_KEY is not configured")
            # Retries are ours, so that every attempt is recorded and bounded.
            self._client = AsyncOpenAI(
                base_url=self.settings.model_api_base_url, api_key=self.settings.model_api_key, max_retries=0
            )
        return self._client

    async def complete_json(
        self,
        purpose: Purpose,
        messages: list[dict[str, Any]],
        schema: type[T],
        *,
        session_id: str = "",
        run_id: str = "runtime",
        route: ModelRoute | None = None,
    ) -> StructuredResult[T]:
        """Call the model and return schema-validated output, or raise ModelError.

        Valid output is only well-formed. It says nothing about mathematical correctness.
        """

        chosen = route or self.routes[purpose]
        client = self._get_client()
        worst_case = worst_case_output_cost(chosen.model, chosen.max_tokens) + ASSUMED_MAX_INPUT_USD
        try:
            with self.ledger.reserve_operation(session_id, worst_case):
                return await self._send(purpose, messages, schema, chosen, client, session_id, run_id)
        except BudgetExceeded as exc:
            log.warning("budget_exceeded", extra={"scope": exc.scope, "purpose": str(purpose)})
            raise ModelError("budget_exceeded", str(exc)) from exc

    async def _send(
        self,
        purpose: Purpose,
        messages: list[dict[str, Any]],
        schema: type[T],
        chosen: ModelRoute,
        client: AsyncOpenAI,
        session_id: str,
        run_id: str,
    ) -> StructuredResult[T]:
        response_format: ResponseFormatJSONSchema = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
        }

        last_error: ModelError | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            # Each retry is another billable call. Re-check committed spend and reserve atomically.
            worst_case = worst_case_output_cost(chosen.model, chosen.max_tokens) + ASSUMED_MAX_INPUT_USD
            with self.ledger.reserve_attempt(session_id, worst_case):
                record = UsageRecord(
                    model=chosen.model,
                    reasoning_effort=chosen.reasoning_effort,
                    purpose=str(purpose),
                    run_id=run_id,
                    session_id=session_id,
                    attempt=attempt,
                )
                started = time.perf_counter()
                try:
                    response = await client.chat.completions.create(
                        model=chosen.model,
                        messages=cast(list[ChatCompletionMessageParam], messages),
                        response_format=response_format,
                        max_tokens=chosen.max_tokens,
                        reasoning_effort=cast(ReasoningEffort, chosen.reasoning_effort),
                        timeout=chosen.timeout_s,
                        extra_body={"prompt_cache_key": f"quack-{schema.__name__}"},
                    )
                except asyncio.CancelledError:
                    # Cancelling our wait doesn't prove the provider stopped its billable work.
                    record.elapsed_ms = (time.perf_counter() - started) * 1000
                    record.error_category = "cancelled"
                    record.estimated_usd = worst_case
                    self.ledger.record(record)
                    log.info("model_request_cancelled", extra={"purpose": str(purpose), "attempt": attempt})
                    raise
                except (openai.APIError, TimeoutError) as exc:
                    category, retryable, status = _categorise(exc)
                    record.elapsed_ms = (time.perf_counter() - started) * 1000
                    record.http_status = status
                    record.error_category = category
                    # The model may have done the work and billed for it, with the reply lost on the way.
                    # A refused connection never reached it, so that costs nothing.
                    if isinstance(exc, openai.APITimeoutError | TimeoutError):
                        record.estimated_usd = worst_case_output_cost(chosen.model, chosen.max_tokens)
                    self.ledger.record(record)
                    log.warning("model_request_failed", extra={"category": category, "status": status, "attempt": attempt})
                    last_error = ModelError(category, f"model request failed: {category}", status)
                    if category == "timeout" and not chosen.retry_timeouts:
                        raise last_error from exc
                    if retryable and attempt < MAX_ATTEMPTS:
                        delay = _retry_delay(attempt, exc)
                        if delay is not None:
                            await asyncio.sleep(delay)
                            continue
                    raise last_error from exc

                record.elapsed_ms = (time.perf_counter() - started) * 1000
                record.http_status = 200
                for key, token_count in _usage_fields(response.usage).items():
                    setattr(record, key, token_count)
                record.estimated_usd = (
                    token_cost(chosen.model, record.input_tokens, record.cached_input_tokens, record.output_tokens)
                    if response.usage is not None
                    # It says nothing about what it cost. A budget mustn't take that to mean nothing.
                    else worst_case_output_cost(chosen.model, chosen.max_tokens)
                )

                if not response.choices:
                    record.error_category = "invalid_output"
                    self.ledger.record(record)
                    log.warning("model_output_invalid", extra={"attempt": attempt, "purpose": str(purpose)})
                    last_error = ModelError("invalid_output", "model response had no choices")
                    if attempt < MAX_ATTEMPTS - 1:
                        continue
                    raise last_error

                choice = response.choices[0]
                if choice.finish_reason == "length":
                    # Reasoning shares the token limit, so a retry at the same limit tends to fail
                    # the same way and costs the same again.
                    record.error_category = "truncated"
                    self.ledger.record(record)
                    raise ModelError("truncated", "output hit max_tokens before finishing")

                try:
                    value = schema.model_validate_json(choice.message.content or "")
                except ValidationError as exc:
                    record.error_category = "invalid_output"
                    self.ledger.record(record)
                    log.warning("model_output_invalid", extra={"attempt": attempt, "purpose": str(purpose)})
                    last_error = ModelError("invalid_output", "model output failed schema validation")
                    if attempt < MAX_ATTEMPTS - 1:
                        continue
                    raise last_error from exc

                self.ledger.record(record)
                log.info(
                    "model_request",
                    extra={
                        "purpose": str(purpose),
                        "model": chosen.model,
                        "effort": chosen.reasoning_effort,
                        "ms": round(record.elapsed_ms),
                        "input": record.input_tokens,
                        "cached": record.cached_input_tokens,
                        "output": record.output_tokens,
                        "usd": round(record.estimated_usd, 6),
                    },
                )
                return StructuredResult(value, record)

        raise last_error or ModelError("invalid_output", "model request produced no result")
