"""Faster reading mustn't cost accuracy. Checks recorded live runs, so it spends nothing.

The ink in these runs is a synthetic stroke alphabet, so they say nothing about real handwriting.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from quack.config import Settings
from quack.llm.ledger import Ledger
from quack.llm.router import ModelRouter, Purpose

RESULTS = Path(__file__).resolve().parents[3] / "evals" / "results"


def recorded(run: str, config: str | None = None) -> list[dict]:
    with (RESULTS / run / "results.jsonl").open() as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return [row for row in rows if config is None or row["config"] == config]


@pytest.mark.parametrize("config", ["H", "I"])
def test_compact_reading_raises_no_false_alert(config: str) -> None:
    rows = recorded("v6-compact", config)
    assert len(rows) == 72
    false_alerts = [row["case"] for row in rows if row["predicted"] == "confirmed_error" and row["expected"] != "confirmed_error"]
    assert false_alerts == []


@pytest.mark.parametrize("config", ["H", "I"])
def test_compact_reading_changes_no_outcome_from_the_old_prompt(config: str) -> None:
    before = {row["case"]: row["predicted"] for row in recorded("v5-all")}
    changed = [row["case"] for row in recorded("v6-compact", config) if row["predicted"] != before[row["case"]]]
    assert changed == []


@pytest.mark.parametrize("config", ["H", "I"])
def test_the_zero_prompt_raises_no_false_alert_and_misses_nothing_more(config: str) -> None:
    rows = recorded("v7-compact", config)
    assert len(rows) == 72
    assert [row["case"] for row in rows if row["predicted"] == "confirmed_error" and row["expected"] != "confirmed_error"] == []
    before = {row["case"]: row["predicted"] for row in recorded("v6-compact", config)}
    lost = [row["case"] for row in rows if before[row["case"]] == "confirmed_error" and row["predicted"] != "confirmed_error"]
    assert lost == []


def test_the_two_readings_together_catch_what_each_did() -> None:
    fast = {row["case"]: row for row in recorded("v6-compact", "I")}
    careful = {row["case"]: row for row in recorded("v6-compact", "H")}
    # An alert stands only if the same mistake comes out of both.
    alerts = Counter(
        fast[case]["expected"]
        for case in fast
        if fast[case]["predicted"] == "confirmed_error"
        and careful[case]["predicted"] == "confirmed_error"
        and fast[case]["fingerprint"] == careful[case]["fingerprint"]
    )
    assert alerts == Counter({"confirmed_error": 33})


def test_the_two_readings_differ_on_purpose(settings: Settings) -> None:
    router = ModelRouter(settings, Ledger(settings.ledger_path, 1, 1, 1, 1))
    first, second = router.routes[Purpose.TRANSCRIBE], router.routes[Purpose.TRANSCRIBE_CONFIRM]
    assert (first.reasoning_effort, second.reasoning_effort) == ("minimal", "low")
