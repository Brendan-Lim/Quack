"""The numbers in the evaluation reports are only as good as this arithmetic."""

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "evals"))

from cases import CASES  # noqa: E402
from metrics import combine_confirmation, edit_distance, same_mathematics, score_decisions, score_transcription  # noqa: E402

from quack.services.math_verifier import LineInput, verify_lines  # noqa: E402


def record(case: str, expected: str, predicted: str, **extra: Any) -> dict[str, Any]:
    return {"case": case, "expected": expected, "predicted": predicted, "ground_truth": [], "transcription": [], **extra}


def test_precision_counts_every_false_alert() -> None:
    score = score_decisions(
        [
            record("a", "confirmed_error", "confirmed_error", error_line=1, error_line_predicted=1),
            record("b", "confirmed_error", "uncertain"),
            record("c", "no_error", "confirmed_error"),
            record("d", "no_error", "no_error"),
            record("e", "abstain", "confirmed_error"),
            record("f", "abstain", "unsupported"),
        ]
    )
    assert score["alert_precision"] == pytest.approx(1 / 3)
    assert score["alert_recall"] == pytest.approx(1 / 2)
    assert score["false_alerts"] == 2
    assert score["false_alert_rate_on_valid"] == pytest.approx(1 / 2)
    assert score["abstain_respected"] == pytest.approx(1 / 2)


def test_alert_on_the_wrong_line_counts_as_detection_but_not_localisation() -> None:
    score = score_decisions([record("a", "confirmed_error", "confirmed_error", error_line=1, error_line_predicted=2)])
    assert score["alert_recall"] == 1
    assert score["first_error_localisation"] == 0


def test_failures_are_not_silently_scored_as_correct() -> None:
    score = score_decisions([record("a", "no_error", "failed", error="timeout")])
    assert score["failed_requests"] == 1
    assert score["valid_cases"] == 0
    assert score["false_alert_rate_on_valid"] is None


def test_no_alerts_gives_unknown_precision_rather_than_perfect() -> None:
    assert score_decisions([record("a", "no_error", "no_error")])["alert_precision"] is None


def test_confirmation_only_removes_alerts_and_adds_cost_when_it_ran() -> None:
    fast = [
        record("a", "no_error", "confirmed_error", fingerprint="x", latency_ms=1000, cost_usd=0.01),
        record("b", "confirmed_error", "confirmed_error", fingerprint="y", latency_ms=1000, cost_usd=0.01),
        record("c", "no_error", "no_error", latency_ms=1000, cost_usd=0.01),
    ]
    confirm = [
        record("a", "no_error", "no_error", latency_ms=5000, cost_usd=0.05),
        record("b", "confirmed_error", "confirmed_error", fingerprint="y", latency_ms=5000, cost_usd=0.05),
        record("c", "no_error", "no_error", latency_ms=5000, cost_usd=0.05),
    ]
    merged = {r["case"]: r for r in combine_confirmation(fast, confirm)}
    assert merged["a"]["predicted"] == "uncertain"
    assert merged["b"]["predicted"] == "confirmed_error"
    assert merged["b"]["latency_ms"] == 6000
    assert merged["c"]["latency_ms"] == 1000, "correct work never pays for a second reading"


def test_same_mathematics() -> None:
    assert same_mathematics("2x+6", "2x + 6")
    assert same_mathematics("=2x+6", "= 6 + 2x")
    assert not same_mathematics("2x+6", "2x+8")
    assert not same_mathematics("=2x+6", "2x+6"), "dropping the equals sign changes what the verifier does"
    assert not same_mathematics("x^2", "x2")


def test_transcription_scores() -> None:
    assert edit_distance("abc", "abd") == 1
    score = score_transcription(
        [
            {"case": "a", "ground_truth": ["x^2", "=2x"], "transcription": ["x2", "=2x"]},
            {"case": "b", "ground_truth": ["x^2"], "transcription": ["x^2"]},
        ]
    )
    assert score["line_accuracy"] == pytest.approx(2 / 3)
    assert score["case_accuracy"] == pytest.approx(1 / 2)
    assert score["superscript_misses"] == 1


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_every_label_agrees_with_the_verifier_when_the_reading_is_perfect(case: Any) -> None:
    """Oracle check: given the true transcription, the verifier must reach the labelled outcome."""

    lines = [
        LineInput(index, text, continues="no" if index == 0 else "yes", operation=case.operation)
        for index, text in enumerate(case.lines)
    ]
    report = verify_lines(lines)
    if case.expected == "confirmed_error":
        assert report.status == "invalid"
        assert report.first_invalid is not None and report.first_invalid.line == case.error_line
    elif case.expected == "no_error":
        assert report.status == "valid"
    else:
        assert report.status in ("uncertain", "unsupported")
