#!/usr/bin/env python3
"""Validate locked release inputs, published files and committed test selection."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    errors: list[str] = []
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    for name in filter(None, names):
        path = ROOT / name
        if not path.is_file():
            errors.append(f"Missing published file: {name}")
            continue
        if "/tests/" in name and path.suffix in {".ts", ".tsx", ".py"}:
            if re.search(r"\b(?:test|it|describe)\s*\.\s*only\s*[(<]", path.read_text()):
                errors.append(f"Focused test committed: {name}")
    package = json.loads((ROOT / "apps/web/package.json").read_text())
    locked = json.loads((ROOT / "apps/web/package-lock.json").read_text())["packages"][""]
    for group in ("dependencies", "devDependencies"):
        if package[group] != locked[group]:
            errors.append(f"Web {group} differ from lockfile")
    for name in ("requirements.lock", "requirements-tooling.lock"):
        for line in (ROOT / "services/api" / name).read_text().splitlines():
            if line.strip() and not line.startswith("#") and not re.fullmatch(r"[\w.-]+==[\w.+-]+", line):
                errors.append(f"Unpinned Python input: {name}")
    if "next/font/google" in (ROOT / "apps/web/app/layout.tsx").read_text():
        errors.append("Build must use bundled fonts")
    for error in errors:
        print(error)
    print(f"Release inputs: {len(errors)} errors")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
