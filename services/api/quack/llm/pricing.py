"""Meta Model API list prices, from https://dev.meta.ai/docs/pricing-rate-limits (checked 2026-10-31)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenPrice:
    input_per_m: float
    cached_input_per_m: float
    output_per_m: float


PRICES: dict[str, TokenPrice] = {
    "muse-spark-1.3": TokenPrice(1.25, 0.15, 4.25),
    "muse-wonder-1.0": TokenPrice(4.00, 0.20, 20.00),
}
VOICE_TRANSCRIBE_PER_SECOND = 0.18 / 3600
VOICE_TRANSCRIBE_MODEL = "muse-voice-transcribe-1.0"

# Prices are unknown for anything else, so refuse rather than record a misleading zero.
ALLOWED_TOKEN_MODELS = frozenset(PRICES)


def token_cost(model: str, prompt_tokens: int, cached_tokens: int, completion_tokens: int) -> float:
    """Estimated dollars for one request, from API-reported usage.

    Reasoning tokens are part of `completion_tokens` and billed at the output rate, so
    they are not added again.
    """

    price = PRICES[model]
    uncached = max(prompt_tokens - cached_tokens, 0)
    return (
        uncached * price.input_per_m + cached_tokens * price.cached_input_per_m + completion_tokens * price.output_per_m
    ) / 1_000_000


def worst_case_output_cost(model: str, max_tokens: int) -> float:
    return max_tokens * PRICES[model].output_per_m / 1_000_000
