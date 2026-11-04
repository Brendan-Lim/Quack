"""Pure scoring functions, separate from the runner so that they can be unit tested."""

from __future__ import annotations

import re
import statistics
from collections import Counter
from typing import Any

from quack.services.math_parser import MathParseError, parse_statement, to_sympy

ALERT = "confirmed_error"
ABSTAIN = {"uncertain", "unsupported"}


def squash(text: str) -> str:
    return re.sub(r"[\s*]", "", text).replace("−", "-").lower()


def edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def same_mathematics(truth: str, read: str) -> bool:
    """Same text, or the same value structure: `2x+6` and `6+2x` are the same reading."""

    if squash(truth) == squash(read):
        return True
    try:
        a, b = parse_statement(truth), parse_statement(read)
        if a.kind != b.kind or len(a.parts) != len(b.parts) or a.continuation != b.continuation:
            return False
        return all(to_sympy(x) == to_sympy(y) for x, y in zip(a.parts, b.parts, strict=True))
    except MathParseError:
        return False


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def score_transcription(records: list[dict[str, Any]]) -> dict[str, Any]:
    lines = line_hits = cases = case_hits = chars = errors = 0
    superscripts = superscript_misses = 0
    failures: list[dict[str, str]] = []
    for record in records:
        if record.get("error"):
            continue
        truth, read = record["ground_truth"], record["transcription"]
        cases += 1
        all_right = len(truth) == len(read)
        for index, line in enumerate(truth):
            lines += 1
            got = read[index] if index < len(read) else ""
            chars += len(squash(line))
            errors += edit_distance(squash(line), squash(got))
            for exponent in re.findall(r"\^[-\w(]+", squash(line)):
                superscripts += 1
                if exponent not in squash(got):
                    superscript_misses += 1
            if same_mathematics(line, got):
                line_hits += 1
            else:
                all_right = False
                failures.append({"case": record["case"], "truth": line, "read": got})
        case_hits += all_right
    return {
        "cases": cases,
        "line_accuracy": ratio(line_hits, lines),
        "case_accuracy": ratio(case_hits, cases),
        "character_error_rate": ratio(errors, chars),
        "superscripts": superscripts,
        "superscript_misses": superscript_misses,
        "failures": failures,
    }


def score_decisions(records: list[dict[str, Any]]) -> dict[str, Any]:
    alerts_on_errors = alerts_elsewhere = missed = localised = 0
    errors_total = valid_total = abstain_total = alerts_on_valid = alerts_on_abstain = 0
    valid_silent = failed = 0
    wrong: list[dict[str, Any]] = []
    outcomes: Counter[str] = Counter()
    for record in records:
        predicted, expected = record["predicted"], record["expected"]
        outcomes[f"{expected}->{predicted}"] += 1
        if predicted == "failed":
            failed += 1
            continue
        if expected == ALERT:
            errors_total += 1
            if predicted == ALERT:
                alerts_on_errors += 1
                if record.get("error_line_predicted") == record.get("error_line"):
                    localised += 1
                else:
                    wrong.append({"kind": "wrong_line", **_brief(record)})
            else:
                missed += 1
                wrong.append({"kind": "missed", **_brief(record)})
        elif expected == "no_error":
            valid_total += 1
            if predicted == ALERT:
                alerts_on_valid += 1
                alerts_elsewhere += 1
                wrong.append({"kind": "false_alert", **_brief(record)})
            else:
                valid_silent += 1
        else:
            abstain_total += 1
            if predicted == ALERT:
                alerts_on_abstain += 1
                alerts_elsewhere += 1
                wrong.append({"kind": "false_alert", **_brief(record)})
    return {
        "alert_precision": ratio(alerts_on_errors, alerts_on_errors + alerts_elsewhere),
        "alert_recall": ratio(alerts_on_errors, errors_total),
        "false_alert_rate_on_valid": ratio(alerts_on_valid, valid_total),
        "false_alerts": alerts_elsewhere,
        "first_error_localisation": ratio(localised, alerts_on_errors),
        "abstain_respected": ratio(abstain_total - alerts_on_abstain, abstain_total),
        "valid_cases": valid_total,
        "error_cases": errors_total,
        "abstain_cases": abstain_total,
        "failed_requests": failed,
        "outcomes": dict(sorted(outcomes.items())),
        "wrong": wrong,
    }


def _brief(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "case": record["case"],
        "expected": record["expected"],
        "predicted": record["predicted"],
        "reason": record.get("reason"),
        "truth": record["ground_truth"],
        "read": record.get("transcription"),
    }


def score_operations(records: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = [(r["operation"], r["operation_read"]) for r in records if r["operation"] != "unknown" and r.get("operation_read")]
    return {"operation_accuracy": ratio(sum(a == b for a, b in pairs), len(pairs)), "operations_scored": len(pairs)}


def score_cost(records: list[dict[str, Any]]) -> dict[str, Any]:
    done = [r for r in records if not r.get("error")]
    latencies = [r["latency_ms"] for r in done]
    costs = [r["cost_usd"] for r in done]
    return {
        "latency_p50_ms": percentile(latencies, 0.5),
        "latency_p95_ms": percentile(latencies, 0.95),
        "cost_per_analysis_usd": statistics.mean(costs) if costs else None,
        "total_cost_usd": sum(r["cost_usd"] for r in records),
        "output_tokens_mean": statistics.mean(r["output_tokens"] for r in done) if done else None,
        "reasoning_tokens_mean": statistics.mean(r["reasoning_tokens"] for r in done) if done else None,
        "cached_input_share": ratio(sum(r["cached_input_tokens"] for r in done), sum(r["input_tokens"] for r in done)),
    }


def combine_confirmation(fast: list[dict[str, Any]], confirm: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the product does with confirm-before-quack, derived without spending more.

    A fast alert only stands if the confirming reading finds the same mistake.
    """

    by_case = {r["case"]: r for r in confirm}
    combined = []
    for record in fast:
        other = by_case.get(record["case"])
        if other is None:
            continue
        merged = dict(record)
        if record["predicted"] == ALERT:
            merged["latency_ms"] = record["latency_ms"] + other["latency_ms"]
            merged["cost_usd"] = record["cost_usd"] + other["cost_usd"]
            if other["predicted"] != ALERT or other.get("fingerprint") != record.get("fingerprint"):
                merged["predicted"] = "uncertain"
                merged["reason"] = "readings_disagree"
        combined.append(merged)
    return combined


def score_all(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "records": len(records),
        **score_decisions(records),
        "transcription": score_transcription(records),
        **score_operations(records),
        **score_cost(records),
    }
