"""Generate all three help levels for labelled mistakes, and score the raw model answers.

    python evals/run_hints.py --sample 12 --max-usd 0.5 --confirm-credits

Mistakes come from ground-truth lines through the real verifier, so this tests hint writing
without depending on reading. Automatic checks catch spoilers and contradictions. Whether a
hint actually helps a student needs a person, so all text is written out for review.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT.parent / "services" / "api")]

from run import load_manifest, stratified_sample  # noqa: E402

from quack.config import get_settings  # noqa: E402
from quack.llm.ledger import Ledger  # noqa: E402
from quack.llm.router import ModelRouter  # noqa: E402
from quack.services.hint_engine import generate_help  # noqa: E402
from quack.services.math_verifier import LineInput, verify_lines  # noqa: E402
from quack.services.mistake_manager import TrackedMistake  # noqa: E402


def mistake_for(entry: dict[str, Any]) -> TrackedMistake | None:
    lines = [
        LineInput(index, text, continues="no" if index == 0 else "yes", operation=entry["operation"])
        for index, text in enumerate(entry["lines"])
    ]
    step = verify_lines(lines).first_invalid
    if step is None:
        return None
    return TrackedMistake(
        id=f"m_{step.fingerprint}",
        category=step.error_category or "",
        error_type=step.error_type or "",
        operation_type=step.operation_type,
        previous_step=step.previous_step or "",
        current_step=step.current_step or "",
        expected=step.expected or "",
        evidence=step.evidence or "",
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", type=int, default=12)
    parser.add_argument("--max-usd", type=float, required=True)
    parser.add_argument("--confirm-credits", action="store_true")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    if not args.confirm_credits:
        print("Refusing to spend: pass --confirm-credits once you have checked your active credits.")
        return 2

    entries = [e for e in load_manifest("all") if e["expected"] == "confirmed_error"]
    entries = stratified_sample(entries, args.sample)
    run_id = args.run_id or time.strftime("hints-%Y%m%d-%H%M%S")
    # Results from real handwriting quote what people wrote, so they are not committed.
    has_real = any(entry["provenance"] == "real" for entry in entries)
    out_dir = (ROOT / "dataset" / "private" / "results" if has_real else ROOT / "results") / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    settings = get_settings()
    ledger = Ledger(settings.ledger_path, settings.budget_project_usd, settings.budget_session_usd, settings.budget_request_usd)
    router = ModelRouter(settings, ledger)
    start_spend = ledger.project_spend

    rows: list[dict[str, Any]] = []
    for entry in entries:
        mistake = mistake_for(entry)
        if mistake is None:
            print(f"  {entry['id']}: verifier found no mistake in the ground truth, skipping")
            continue
        for level in (1, 2, 3):
            if ledger.project_spend - start_spend >= args.max_usd:
                print("Stopped at the spending limit.")
                break
            attempts: list[tuple[str, list[str]]] = []
            started = time.perf_counter()
            text, source, error = await generate_help(router, level, mistake, run_id=run_id, attempts=attempts)  # type: ignore[arg-type]
            if level < 3:
                mistake.hints_given.append(text)
            rows.append(
                {
                    "case": entry["id"],
                    "error_type": mistake.error_type,
                    "wrote": mistake.current_step,
                    "previous": mistake.previous_step,
                    "correct": mistake.expected,
                    "level": level,
                    "final_text": text,
                    "source": source,
                    "error": error.category if error else None,
                    "attempts": [{"text": t, "problems": p} for t, p in attempts],
                    "latency_ms": (time.perf_counter() - started) * 1000,
                }
            )
            print(f"  {entry['id']} L{level} [{source}] {text}", flush=True)

    with (out_dir / "hints.jsonl").open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    first_pass = [row for row in rows if row["attempts"]]
    clean = sum(1 for row in first_pass if not row["attempts"][0]["problems"])
    spoilers = sum(1 for row in first_pass if row["level"] < 3 and any("gives away" in p or "reveals" in p for p in row["attempts"][0]["problems"]))
    hints = sum(1 for row in first_pass if row["level"] < 3)
    templated = sum(1 for row in rows if row["source"] == "template")
    summary = [
        f"# Hint evaluation: {run_id}",
        "",
        f"{len(rows)} responses for {len(entries)} mistakes. Spend about ${ledger.project_spend - start_spend:.4f}.",
        "",
        f"- First attempt passed every automatic check: {clean}/{len(first_pass)}",
        f"- First-attempt hints that revealed the answer (levels 1 and 2): {spoilers}/{hints}",
        f"- Replaced by the fixed template: {templated}/{len(rows)}",
        "",
        "Automatic checks only. Whether these help a student still needs human review.",
        "",
        "| Case | Wrote | Level | Text | Source |",
        "|---|---|--:|---|---|",
    ]
    summary += [f"| {r['case']} | `{r['previous']}` → `{r['wrote']}` | {r['level']} | {r['final_text']} | {r['source']} |" for r in rows]
    (out_dir / "report.md").write_text("\n".join(summary) + "\n")
    print("\n".join(summary[:8]))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
