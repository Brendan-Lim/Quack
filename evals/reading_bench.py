"""How long a reading takes, against how many lines are in the image. Spends real credit.

    python evals/reading_bench.py --max-usd 0.6 --confirm-credits

The ink is a synthetic stroke alphabet, so this compares configurations against each other. It says
nothing about reading real handwriting. Two readings run at once for each page, as the app does.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import random
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / "synthetic"), str(ROOT.parent / "services" / "api")]

from quack.config import get_settings
from quack.llm.ledger import Ledger
from quack.llm.router import ModelError, ModelRoute, ModelRouter, Purpose
from quack.services.handwriting import read_handwriting, read_lines, sanitise_image
from render import CASUAL, render

SPARK = "muse-spark-1.3"
LINE_COUNTS = (1, 2, 4, 8, 12)


@dataclass(frozen=True)
class Setup:
    name: str
    effort: str
    compact: bool


SETUPS = (Setup("v5 low", "low", False), Setup("v6 low", "low", True), Setup("v6 minimal", "minimal", True))


def page_of(count: int, rng: random.Random) -> list[str]:
    lines = []
    for _ in range(count):
        a, b = rng.randint(2, 9), rng.randint(2, 9)
        lines.append(rng.choice([f"{a}*{b}={a * b}", f"{a}+{b}={a + b}", f"{a}(x+{b})={a}x+{a * b}"]))
    return lines


def as_data_url(lines: list[str], seed: int) -> str:
    image = render([{"text": line} for line in lines], style=CASUAL, seed=seed)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return sanitise_image("data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode())[0]


async def read_once(router: ModelRouter, image: str, count: int, setup: Setup, purpose: Purpose, run_id: str) -> dict:
    route = ModelRoute(SPARK, setup.effort, 40.0, 6000)
    started = time.perf_counter()
    if setup.compact:
        result = await read_lines(router, image, count, purpose=purpose, route=route, run_id=run_id)
    else:
        result = await read_handwriting(router, image, purpose=purpose, route=route, run_id=run_id)
    return {
        "ms": (time.perf_counter() - started) * 1000,
        "math": [line.math for line in result.value.lines],
        "output": result.record.output_tokens,
        "reasoning": result.record.reasoning_tokens,
        "input": result.record.input_tokens,
        "usd": result.record.estimated_usd,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-usd", type=float, required=True)
    parser.add_argument("--confirm-credits", action="store_true")
    parser.add_argument("--out", default="evals/results/reading-bench/results.jsonl")
    args = parser.parse_args()
    if not args.confirm_credits:
        print("Refusing to spend: pass --confirm-credits once you have checked your active credits.")
        return 2

    settings = get_settings()
    ledger = Ledger(settings.ledger_path, settings.budget_project_usd, settings.budget_session_usd, settings.budget_request_usd)
    router = ModelRouter(settings, ledger)
    rng = random.Random(4)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("bench-%Y%m%d-%H%M%S")
    spent = 0.0
    rows = []
    for repeat in range(args.repeats):
        for count in LINE_COUNTS:
            lines = page_of(count, rng)
            image = as_data_url(lines, seed=rng.randint(0, 10**6))
            for setup in SETUPS:
                if spent >= args.max_usd:
                    print(f"Stopped at the limit, ${spent:.3f}.")
                    return 0
                try:
                    pair = await asyncio.gather(
                        read_once(router, image, count, setup, Purpose.TRANSCRIBE, run_id),
                        read_once(router, image, count, setup, Purpose.TRANSCRIBE_CONFIRM, run_id),
                    )
                except ModelError as exc:
                    print(f"failed: {setup.name} {count} lines: {exc.category}")
                    continue
                spent += sum(item["usd"] for item in pair)
                expected = [line.replace(" ", "") for line in lines]
                right = [[m.replace(" ", "") for m in item["math"]] == expected for item in pair]
                row = {
                    "setup": setup.name,
                    "lines": count,
                    "repeat": repeat,
                    "first_ms": pair[0]["ms"],
                    "both_ms": max(item["ms"] for item in pair),
                    "output": [item["output"] for item in pair],
                    "reasoning": [item["reasoning"] for item in pair],
                    "input": pair[0]["input"],
                    "right": right,
                    "usd": sum(item["usd"] for item in pair),
                }
                rows.append(row)
                print(f"  {setup.name:<11} {count:>2} lines  first {row['first_ms']:>6.0f}ms  both {row['both_ms']:>6.0f}ms  output {row['output']}  right {right}", flush=True)

    with out.open("a") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    print("\n| Setup | Lines | First, median | Both, median | Output tokens | Reasoning | All read right |\n|---|--:|--:|--:|--:|--:|--:|")
    for setup in SETUPS:
        for count in LINE_COUNTS:
            group = [r for r in rows if r["setup"] == setup.name and r["lines"] == count]
            if not group:
                continue
            outputs = [token for r in group for token in r["output"]]
            reasoning = [token for r in group for token in r["reasoning"]]
            rights = [flag for r in group for flag in r["right"]]
            print(
                f"| {setup.name} | {count} | {statistics.median(r['first_ms'] for r in group) / 1000:.1f}s | "
                f"{statistics.median(r['both_ms'] for r in group) / 1000:.1f}s | {statistics.median(outputs):.0f} | "
                f"{statistics.median(reasoning):.0f} | {sum(rights)}/{len(rights)} |"
            )
    print(f"\nSpent about ${spent:.3f}; ledger total ${ledger.project_spend:.3f}.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
