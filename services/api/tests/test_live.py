"""Real calls to Meta's API. Excluded by default: run with `pytest -m live`. Spends credit."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from quack.config import Settings
from quack.llm.ledger import Ledger
from quack.llm.router import ModelRouter
from quack.services.handwriting import read_handwriting, sanitise_image, to_line_inputs
from quack.services.hint_engine import find_problems, generate_help
from quack.services.math_verifier import verify_lines
from quack.services.mistake_manager import TrackedMistake

pytestmark = pytest.mark.live

IMAGES = Path(__file__).resolve().parents[3] / "evals" / "dataset" / "synthetic" / "images"


@pytest.fixture
def router() -> ModelRouter:
    settings = Settings()
    if not settings.model_api_key:
        pytest.skip("MODEL_API_KEY is not configured")
    return ModelRouter(settings, Ledger(settings.ledger_path, 5.0, 2.0, 0.5))


def data_url(name: str) -> str:
    return sanitise_image("data:image/png;base64," + base64.b64encode((IMAGES / name).read_bytes()).decode())[0]


async def test_reads_and_verifies_a_real_image(router: ModelRouter) -> None:
    result = await read_handwriting(router, data_url("demo_algebra.png"), run_id="live-test")
    report = verify_lines(to_line_inputs(result.value))
    assert report.first_invalid is not None
    assert report.first_invalid.error_type == "incorrect_distribution"
    assert result.record.input_tokens > 0, "usage must come from the API"
    assert result.record.estimated_usd > 0


async def test_generates_a_valid_hint(router: ModelRouter) -> None:
    mistake = TrackedMistake(
        "m_live", "algebra_expansion", "incorrect_distribution", "expression_equivalence",
        "2(x + 3)", "2x + 3", "2x + 6", "expected 2x + 6, got 2x + 3",
    )
    attempts: list[tuple[str, list[str]]] = []
    text, source, error = await generate_help(router, 1, mistake, run_id="live-test", attempts=attempts)
    assert error is None
    assert source == "model"
    assert find_problems(1, text, mistake) == []
