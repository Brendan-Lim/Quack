#!/usr/bin/env python3
"""Run Quack's release checks. No live model calls, deployments or production credentials."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_environment() -> dict[str, str]:
    """Never inherit production keys, databases, routes or budgets into release checks."""
    keep = {"QUACK_TEST_PYTHON", "QUACK_LEDGER_TEST_DATABASE_URL", "QUACK_E2E_PORT_SHIFT", "QUACK_GITLEAKS"}
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("QUACK_", "MODEL_", "SUPABASE_", "NEXT_PUBLIC_")) or key in keep
    }
    env.pop("API_URL", None)
    env.update(CI="true", QUACK_ENV="production", MODEL_API_KEY="",
               QUACK_RELEASE_VERIFY="1", NEXT_TELEMETRY_DISABLED="1")
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true", help="also run all mocked Playwright journeys")
    parser.add_argument("--build", action="store_true", help="also compile the production web app")
    args = parser.parse_args()
    env = test_environment()
    if args.browser:
        # Existing visual/performance journeys are opt-in during development, required for release.
        env.update(QUACK_PERF="1", QUACK_SCREENSHOTS="1")
    if not env.get("QUACK_LEDGER_TEST_DATABASE_URL"):
        parser.error("set QUACK_LEDGER_TEST_DATABASE_URL to a disposable local quack_ledger_test database")
    gitleaks = env.get("QUACK_GITLEAKS") or shutil.which("gitleaks")
    if not gitleaks:
        parser.error("install Gitleaks 8.30.1 or set QUACK_GITLEAKS to its executable")
    local_python = ROOT / "services/api/.venv/bin/python"
    python = os.environ.get("QUACK_TEST_PYTHON") or (str(local_python) if local_python.exists() else sys.executable)
    checks = [
        ("Dependency consistency", ".", [python, "-m", "pip", "check"]),
        ("Release inputs and focused-test guard", ".", [python, "scripts/check_release.py"]),
        ("Public file and history policy", ".", [python, "scripts/check_publication.py", "--history"]),
        ("History secret scan", ".", [gitleaks, "git", "--redact", "--no-banner", "--log-opts=--all"]),
        ("Staged secret scan", ".", [gitleaks, "git", "--redact", "--no-banner", "--pre-commit", "--staged"]),
        ("Python lint", "services/api", [python, "-m", "ruff", "check", "quack", "tests"]),
        ("Python types", "services/api", [python, "-m", "mypy", "quack"]),
        ("Backend regressions", "services/api", [python, "-m", "pytest", "-q", "-m", "not live"]),
        ("Web lint", "apps/web", ["npm", "run", "lint"]),
        ("Web types", "apps/web", ["npm", "run", "typecheck"]),
        ("Web logic", "apps/web", ["npm", "test"]),
    ]
    if args.build:
        checks.append(("Production build", "apps/web", ["npm", "run", "build"]))
        if args.browser:
            checks.append(("Compiled app safety", "apps/web", ["npm", "run", "test:production"]))
            checks.append(("Compiled browser completeness", ".", [python, "scripts/check_browser_results.py",
                           "apps/web/test-results/production-results.json"]))
    if args.browser:
        checks.append(("Browser journeys (fake model)", "apps/web", ["npm", "run", "test:e2e"]))
        checks.append(("Browser journey completeness", ".", [python, "scripts/check_browser_results.py",
                       "apps/web/test-results/results.json"]))
    for label, directory, command in checks:
        print(f"\n{label}", flush=True)
        result = subprocess.run(command, cwd=ROOT / directory, env=env, check=False)
        if result.returncode:
            print(f"\nFAILED: {label} (exit {result.returncode})", file=sys.stderr)
            return result.returncode
    print(f"\nPassed all {len(checks)} checks.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
