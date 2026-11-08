#!/usr/bin/env python3
"""Fail release verification if Playwright omitted cases or reported retries/failures."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check_browser_results.py REPORT.json")
    report = json.loads(Path(sys.argv[1]).read_text())
    stats = report["stats"]
    if report.get("errors") or stats["expected"] <= 0 or any(stats[key] for key in ("skipped", "unexpected", "flaky")):
        print(f"Incomplete browser verification: {stats}")
        return 1
    print(f"Browser completeness: {stats['expected']} passed, zero skipped, failed or flaky")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
