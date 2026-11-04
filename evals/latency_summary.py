"""Summarise the JSON written by apps/web/tests/live/latency.spec.ts, as tables for the report.

    python evals/latency_summary.py latency-results/latency-after-*.json

Spends nothing. It only reads what a live run recorded.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def seconds(ms: float) -> str:
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms:.0f}ms"


def group(scenario: str) -> str:
    return scenario.split(":")[0]


def main() -> None:
    entries: list[dict[str, Any]] = []
    for name in sys.argv[1:]:
        entries.extend(json.loads(Path(name).read_text()))

    timings: dict[str, list[float]] = defaultdict(list)
    missing: dict[str, int] = defaultdict(int)
    requests: list[dict[str, Any]] = []
    for entry in entries:
        kind = group(entry["scenario"])
        requests.extend(entry.get("requests", []))
        for field, label in (
            ("alertMs", kind),
            ("hintMs", kind),
            ("clearedMs", kind),
            ("replyMs", kind),
            ("alertAfterFirstLineMs", "keep writing, from the wrong line"),
            ("alertAfterSecondLineMs", "keep writing, from the next line"),
        ):
            if field not in entry:
                continue
            if entry[field] is None:
                missing[label] += 1
            else:
                timings[label].append(entry[field])

    print("| Interaction | Runs | Median | p95 | Worst | Never came |")
    print("|---|--:|--:|--:|--:|--:|")
    for label in sorted(timings):
        values = timings[label]
        print(
            f"| {label} | {len(values)} | {seconds(statistics.median(values))} | "
            f"{seconds(percentile(values, 0.95))} | {seconds(max(values))} | {missing[label]} |"
        )

    answered = [request for request in requests if request.get("calls") is not None]
    calls = [call for request in answered for call in request["calls"] if not call["cached"]]
    print(f"\nRequests sent: {len(requests)}, answered: {len(answered)}.")
    if answered:
        print(f"Model calls counted per answered request: {statistics.mean(len([c for c in r['calls'] if not c['cached']]) for r in answered):.2f}.")
        print(f"Round trip, median: {seconds(statistics.median(r['roundTripMs'] for r in answered))}.")
    by_route: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for call in calls:
        by_route[(call["purpose"], call["effort"])].append(call)
    print("\n| Call | Effort | Calls | Median | p95 | Worst | Output tokens | Reasoning tokens | Attempts over 1 | $ per call |")
    print("|---|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    for (purpose, effort), group_calls in sorted(by_route.items()):
        ms = [call["ms"] for call in group_calls]
        print(
            f"| {purpose} | {effort} | {len(ms)} | {seconds(statistics.median(ms))} | {seconds(percentile(ms, 0.95))} | {seconds(max(ms))} | "
            f"{statistics.median(call['outputTokens'] for call in group_calls):.0f} | "
            f"{statistics.median(call['reasoningTokens'] for call in group_calls):.0f} | "
            f"{sum(call['attempts'] > 1 for call in group_calls)} | "
            f"${statistics.mean(call['costUsd'] for call in group_calls):.4f} |"
        )

    stage_times: dict[str, list[float]] = defaultdict(list)
    for request in answered:
        previous = 0.0
        for stage, at in (request.get("stages") or {}).items():
            stage_times[stage].append(at - previous)
            previous = at
    print("\n| Server stage, time since the previous stage | Median | p95 |")
    print("|---|--:|--:|")
    for stage, values in stage_times.items():
        print(f"| {stage} | {seconds(statistics.median(values))} | {seconds(percentile(values, 0.95))} |")


if __name__ == "__main__":
    main()
