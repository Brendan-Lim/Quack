"""Cost report from the usage ledger. Run: python -m quack.llm.report

Figures are estimates from API-reported token counts at list price. Meta's Usage dashboard
is the source of truth for what was actually charged.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

from quack.config import get_settings
from quack.llm.ledger import Ledger, UsageRecord, read_records


def _group(records: list[UsageRecord], key: str) -> list[dict]:
    groups: dict[str, list[UsageRecord]] = defaultdict(list)
    for record in records:
        if key == "route":
            name = f"{record.purpose} / {record.model} / {record.reasoning_effort}"
        else:
            name = str(getattr(record, key))
        groups[name].append(record)

    rows = []
    for name, items in sorted(groups.items()):
        succeeded = [r for r in items if r.error_category is None]
        latencies = sorted(r.elapsed_ms for r in succeeded)
        input_tokens = sum(r.input_tokens for r in items)
        rows.append(
            {
                "name": name,
                "requests": len(items),
                "errors": len(items) - len(succeeded),
                "input_tokens": input_tokens,
                "cached_input_tokens": sum(r.cached_input_tokens for r in items),
                "cache_hit_rate": round(sum(r.cached_input_tokens for r in items) / input_tokens, 3) if input_tokens else 0,
                "output_tokens": sum(r.output_tokens for r in items),
                "reasoning_tokens": sum(r.reasoning_tokens for r in items),
                "usd": round(sum(r.estimated_usd for r in items), 6),
                "p50_ms": round(statistics.median(latencies)) if latencies else None,
                "p95_ms": round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]) if latencies else None,
            }
        )
    return rows


def summarise(path: Path, ledger: Ledger | None = None) -> dict:
    records = read_records(path)
    settings = get_settings()
    spent = ledger.project_spend if ledger is not None else sum(r.estimated_usd for r in records)
    return {
        "note": "Estimates from API-reported usage. Check against Meta's Usage dashboard.",
        "project_spend_usd": round(spent, 6),
        "project_ceiling_usd": settings.budget_project_usd,
        "requests": len(records),
        "by_route": _group(records, "route"),
        "by_run": _group(records, "run_id"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    parser.add_argument("--ledger", type=Path, default=None)
    args = parser.parse_args()
    summary = summarise(args.ledger or get_settings().ledger_path)
    if args.json:
        print(json.dumps(summary, indent=2))
        return
    print(f"Spend: ${summary['project_spend_usd']:.4f} of ${summary['project_ceiling_usd']:.2f} ceiling")
    print(f"Requests: {summary['requests']}  ({summary['note']})\n")
    header = (
        f"{'route':<50} {'reqs':>5} {'errs':>5} {'input':>9} {'cache%':>7} "
        f"{'output':>9} {'reason':>9} {'usd':>9} {'p50ms':>7} {'p95ms':>7}"
    )
    for title in ("by_route", "by_run"):
        print(title.replace("_", " "))
        print(header)
        for row in summary[title]:
            print(
                f"{row['name'][:50]:<50} {row['requests']:>5} {row['errors']:>5} {row['input_tokens']:>9} "
                f"{row['cache_hit_rate'] * 100:>6.1f}% {row['output_tokens']:>9} {row['reasoning_tokens']:>9} "
                f"{row['usd']:>9.4f} {str(row['p50_ms']):>7} {str(row['p95_ms']):>7}"
            )
        print()


if __name__ == "__main__":
    main()
