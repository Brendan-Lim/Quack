"""Scoring for the personalization comparison. Pure, so that it can be tested. See calibration_eval.py."""

from __future__ import annotations

import re
import statistics
from typing import Any

from metrics import edit_distance, percentile, ratio, same_mathematics, squash

ALERT = "confirmed_error"


def _hits(records: list[dict[str, Any]], pattern: str) -> dict[str, int]:
    """Of the lines with this symbol in them, how many were read right?"""

    total = right = 0
    for record in records:
        if record.get("error"):
            continue
        for index, line in enumerate(record["ground_truth"]):
            if re.search(pattern, line):
                total += 1
                got = record["transcription"][index] if index < len(record["transcription"]) else ""
                right += same_mathematics(line, got)
    return {"lines": total, "right": right}


def score_mode(records: list[dict[str, Any]]) -> dict[str, Any]:
    finished = [record for record in records if not record.get("error")]
    lines = line_hits = cases_exact = chars = errors = 0
    for record in finished:
        exact = len(record["transcription"]) == len(record["ground_truth"])
        for index, truth in enumerate(record["ground_truth"]):
            got = record["transcription"][index] if index < len(record["transcription"]) else ""
            lines += 1
            chars += len(squash(truth))
            errors += edit_distance(squash(truth), squash(got))
            matched = same_mathematics(truth, got)
            line_hits += matched
            exact = exact and matched
        cases_exact += exact

    wrong = [record for record in finished if record["expected"] == ALERT]
    alerted = [record for record in finished if record["status"] == ALERT]
    true_alerts = [record for record in alerted if record["expected"] == ALERT]
    false_alerts = [record for record in alerted if record["expected"] != ALERT]
    latencies = [record["latency_ms"] for record in finished]
    return {
        "cases": len(records),
        "failed": len(records) - len(finished),
        "lines": lines,
        "exact_lines": ratio(line_hits, lines),
        "exact_cases": ratio(cases_exact, len(finished)),
        "symbol_error_rate": ratio(errors, chars),
        "zeros": _hits(finished, r"0"),
        "multiplication": _hits(finished, r"[*×]"),
        "superscripts": _hits(finished, r"\^"),
        "alerts": len(alerted),
        "precision": ratio(len(true_alerts), len(alerted)),
        "recall": ratio(len(true_alerts), len(wrong)),
        "false_alerts": len(false_alerts),
        "false_alert_cases": [record["case"] for record in false_alerts],
        "latency_p50_ms": percentile(latencies, 0.5),
        "latency_p95_ms": percentile(latencies, 0.95),
        "input_tokens": sum(record["input_tokens"] for record in finished),
        "output_tokens": sum(record["output_tokens"] for record in finished),
        "cost_usd": sum(record["cost_usd"] for record in records),
        "cost_per_analysis_usd": statistics.fmean(record["cost_usd"] for record in finished) if finished else None,
        "reference_reads": sum(record["reference_reads"] for record in finished),
    }


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _share(hits: dict[str, int]) -> str:
    return f"{hits['right']}/{hits['lines']}" if hits["lines"] else "none"


def render_report(scores: dict[str, dict[str, Any]], meta: dict[str, Any]) -> str:
    modes = list(scores)
    rows = [
        ("Cases", lambda s: str(s["cases"])),
        ("Failed requests", lambda s: str(s["failed"])),
        ("Exact lines", lambda s: _percent(s["exact_lines"])),
        ("Exact cases", lambda s: _percent(s["exact_cases"])),
        ("Symbol error rate", lambda s: _percent(s["symbol_error_rate"])),
        ("Lines with a 0 read right", lambda s: _share(s["zeros"])),
        ("Lines with × read right", lambda s: _share(s["multiplication"])),
        ("Lines with a power read right", lambda s: _share(s["superscripts"])),
        ("Mistake precision", lambda s: _percent(s["precision"])),
        ("Mistake recall", lambda s: _percent(s["recall"])),
        ("False alerts", lambda s: str(s["false_alerts"])),
        ("Latency p50", lambda s: "n/a" if s["latency_p50_ms"] is None else f"{s['latency_p50_ms'] / 1000:.2f}s"),
        ("Latency p95", lambda s: "n/a" if s["latency_p95_ms"] is None else f"{s['latency_p95_ms'] / 1000:.2f}s"),
        ("Input tokens", lambda s: str(s["input_tokens"])),
        ("Output tokens", lambda s: str(s["output_tokens"])),
        ("Readings shown references", lambda s: str(s["reference_reads"])),
        ("Cost", lambda s: f"${s['cost_usd']:.4f}"),
        ("Cost per analysis", lambda s: f"${s['cost_per_analysis_usd']:.5f}" if s["cost_per_analysis_usd"] else "n/a"),
    ]
    lines = [
        f"# Personalization comparison: {meta['run']}",
        "",
        f"**Handwriting:** {meta['provenance']}.",
        "",
        f"- Writers: {meta['writers']}",
        f"- Held-out cases: {meta['held_out']}, none of them a calibration prompt ({meta['excluded']} left out as one)",
        f"- References per reading: {meta['references_per_reading']}, from {meta['samples']} samples",
        "- The same confirmation policy, verifier and prompts for each mode. Only the references differ.",
        "",
        "| | " + " | ".join(modes) + " |",
        "|---|" + "--:|" * len(modes),
    ]
    lines += [f"| {name} | " + " | ".join(get(scores[mode]) for mode in modes) + " |" for name, get in rows]
    for mode in modes:
        if scores[mode]["false_alert_cases"]:
            lines += ["", f"False alerts in {mode}: " + ", ".join(scores[mode]["false_alert_cases"])]
    if meta["provenance"] != "real handwriting":
        lines += ["", "**This is not handwriting accuracy.** It only shows that the comparison runs."]
    return "\n".join(lines) + "\n"
