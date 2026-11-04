"""Run labelled images through the real reading and verification code, per configuration.

    python evals/run.py --configs A,B --split dev --max-usd 1 --confirm-credits

Spends real credit. It needs --confirm-credits, stops at --max-usd, resumes where it left
off rather than repeating calls, and logs every request to the shared usage ledger.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT.parent / "services" / "api")]

from quack.config import get_settings  # noqa: E402
from quack.llm.ledger import Ledger  # noqa: E402
from quack.llm.router import ModelError, ModelRoute, ModelRouter, Purpose  # noqa: E402
from quack.services.analysis import classify  # noqa: E402
from quack.services.handwriting import (  # noqa: E402
    COMPACT_PROMPT_VERSION,
    READING_PROMPT_VERSION,
    read_handwriting,
    read_lines,
    sanitise_image,
    to_line_inputs,
)
from quack.services.math_verifier import verify_lines  # noqa: E402

CONCURRENCY = 4


@dataclass(frozen=True)
class Config:
    name: str
    model: str
    effort: str
    page_context: bool
    timeout_s: float
    max_tokens: int
    # The reading a notebook gets: told the line count, and asked only for what's used.
    compact: bool = False


SPARK, WONDER = "muse-spark-1.3", "muse-wonder-1.0"
CONFIGS = {
    config.name: config
    for config in [
        Config("A", SPARK, "low", False, 30, 6000),
        Config("B", SPARK, "high", False, 60, 16000),
        Config("C", SPARK, "high", True, 60, 16000),
        Config("D", WONDER, "medium", False, 90, 12000),
        Config("E", WONDER, "high", True, 120, 16000),
        # Added because reasoning is billed as output: is even less thinking enough to read?
        Config("F", SPARK, "minimal", False, 30, 6000),
        Config("G", SPARK, "medium", False, 45, 10000),
        Config("H", SPARK, "low", False, 30, 6000, compact=True),
        Config("I", SPARK, "minimal", False, 30, 6000, compact=True),
    ]
}


def load_manifest(provenance: str) -> list[dict[str, Any]]:
    entries = []
    for path in sorted((ROOT / "dataset").glob("*/manifest.jsonl")):
        with path.open() as handle:
            entries.extend(json.loads(line) for line in handle if line.strip())
    return [e for e in entries if provenance == "all" or e["provenance"] == provenance]


def as_data_url(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def stratified_sample(entries: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    """Take from each group in turn, so a small sample still covers every kind of case."""

    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        groups.setdefault(entry["group"], []).append(entry)
    chosen: list[dict[str, Any]] = []
    while len(chosen) < size and any(groups.values()):
        for items in groups.values():
            if items and len(chosen) < size:
                chosen.append(items.pop(0))
    return chosen


async def evaluate(router: ModelRouter, entry: dict[str, Any], config: Config, run_id: str) -> dict[str, Any]:
    image, _ = sanitise_image(as_data_url(ROOT / entry["image"]))
    page = None
    if config.page_context and entry.get("page_image"):
        page, _ = sanitise_image(as_data_url(ROOT / entry["page_image"]))
    route = ModelRoute(config.model, config.effort, config.timeout_s, config.max_tokens)
    record: dict[str, Any] = {
        "case": entry["id"],
        "config": config.name,
        "model": config.model,
        "effort": config.effort,
        "page_context": config.page_context,
        "prompt_version": COMPACT_PROMPT_VERSION if config.compact else READING_PROMPT_VERSION,
        "provenance": entry["provenance"],
        "split": entry["split"],
        "group": entry["group"],
        "tags": entry["tags"],
        "image": entry["image"],
        "ground_truth": entry["lines"],
        "expected": entry["expected"],
        "error_line": entry["error_line"],
        "operation": entry["operation"],
        "transcription": [],
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cost_usd": 0.0,
        "latency_ms": 0.0,
    }
    started = time.perf_counter()
    try:
        if config.compact:
            result = await read_lines(router, image, len(entry["lines"]), purpose=Purpose.TRANSCRIBE, route=route, run_id=run_id)
        else:
            result = await read_handwriting(router, image, page_context=page, purpose=Purpose.TRANSCRIBE, route=route, run_id=run_id)
    except ModelError as exc:
        record.update(predicted="failed", error=exc.category, latency_ms=(time.perf_counter() - started) * 1000)
        return record

    reading, usage = result.value, result.record
    inputs = to_line_inputs(reading)
    report = verify_lines(inputs)
    status, reason = classify(reading, report)
    step = report.first_invalid
    record.update(
        predicted=status,
        reason=reason,
        transcription=[line.math for line in inputs],
        operation_read=inputs[-1].operation if inputs else None,
        legibility=[line.legibility for line in reading.lines],
        kinds=[line.kind for line in reading.lines],
        alternatives=[line.alternatives for line in reading.lines],
        error_line_predicted=step.line if step else None,
        error_type=step.error_type if step else None,
        fingerprint=step.fingerprint if step else None,
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        cost_usd=usage.estimated_usd,
        latency_ms=usage.elapsed_ms,
    )
    return record


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--configs", default="A")
    parser.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    parser.add_argument("--provenance", choices=["real", "synthetic", "all"], default="all")
    parser.add_argument("--tag", default=None, help="only cases with this tag")
    parser.add_argument("--sample", type=int, default=None, help="stratified sample size per configuration")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--max-usd", type=float, required=True, help="stop this run at this estimated spend")
    parser.add_argument("--confirm-credits", action="store_true", help="confirm you have checked your active credits")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    entries = [e for e in load_manifest(args.provenance) if args.split == "all" or e["split"] == args.split]
    if args.tag:
        entries = [e for e in entries if args.tag in e["tags"]]
    if args.sample:
        entries = stratified_sample(entries, args.sample)
    configs = [CONFIGS[name.strip()] for name in args.configs.split(",")]

    run_id = args.run_id or time.strftime("run-%Y%m%d-%H%M%S")
    # Results from real handwriting quote what people wrote, so they are not committed.
    has_real = any(entry["provenance"] == "real" for entry in entries)
    out_dir = (ROOT / "dataset" / "private" / "results" if has_real else ROOT / "results") / run_id
    out_path = out_dir / "results.jsonl"
    done: set[tuple[str, str]] = set()
    if out_path.exists():
        with out_path.open() as handle:
            for line in handle:
                record = json.loads(line)
                if not record.get("error"):
                    done.add((record["case"], record["config"]))
    plan = [(entry, config) for config in configs for entry in entries if (entry["id"], config.name) not in done]
    print(f"{run_id}: {len(plan)} calls planned ({len(done)} already done), {len(entries)} cases, configs {args.configs}")
    if args.dry_run or not plan:
        return 0
    if not args.confirm_credits:
        print("Refusing to spend: pass --confirm-credits once you have checked your active credits.")
        return 2

    settings = get_settings()
    ledger = Ledger(settings.ledger_path, settings.budget_project_usd, settings.budget_session_usd, settings.budget_request_usd)
    router = ModelRouter(settings, ledger)
    out_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    spent = 0.0
    stopped = False

    async def one(entry: dict[str, Any], config: Config) -> None:
        nonlocal spent, stopped
        async with semaphore:
            if stopped or spent >= args.max_usd:
                stopped = True
                return
            record = await evaluate(router, entry, config, run_id)
        spent += record["cost_usd"]
        with out_path.open("a") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        flag = record["predicted"] if record["predicted"] == record["expected"] else f"{record['predicted']} (wanted {record['expected']})"
        print(f"  {config.name} {entry['id']:<26} {flag:<44} {record['latency_ms']:>7.0f}ms ${record['cost_usd']:.4f}", flush=True)
        if record.get("error") == "budget_exceeded":
            stopped = True

    await asyncio.gather(*(one(entry, config) for entry, config in plan))
    print(f"Spent about ${spent:.4f} this run; ledger total ${ledger.project_spend:.4f}.{' Stopped early at the limit.' if stopped else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
