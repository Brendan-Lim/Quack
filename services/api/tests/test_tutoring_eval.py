"""The offline trajectory scorecard is also part of the existing regression suite."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "evals"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from adaptive_eval import CASES as ADAPTIVE_CASES  # noqa: E402
from adaptive_eval import run_case  # noqa: E402
from tutoring import CASES, SAFETY_CASES, Trajectory, compare, evaluate_safety, evaluate_trajectory  # noqa: E402


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_complete_tutoring_trajectory(case: Trajectory, tmp_path: Path) -> None:
    result = evaluate_trajectory(case, tmp_path)
    assert all(result["checks"].values()), result


@pytest.mark.parametrize("case", SAFETY_CASES)
def test_episode_safety(case: str, tmp_path: Path) -> None:
    result = evaluate_safety(case, tmp_path)
    assert all(result["checks"].values()), result


def report(checks: dict[str, bool]) -> dict:
    return {"version": 1, "mode": "offline", "fixtures_sha256": "fixture", "evaluator_sha256": "method",
            "passed_cases": int(all(checks.values())), "rows": [{"case": "case", "checks": checks}]}


def test_comparison_exposes_regressions_and_refuses_missing_checks() -> None:
    before = report({"safe": True, "followup": False})
    after = report({"safe": False, "followup": True})
    assert compare(before, after)["regressions"] == [{"case": "case", "check": "safe", "before": True, "after": False}]
    with pytest.raises(ValueError, match="same cases and checks"):
        compare(before, report({"safe": True}))


def test_changed_fixture_or_method_cannot_claim_an_improvement() -> None:
    before = report({"safe": True})
    with pytest.raises(ValueError, match="different evaluation"):
        compare(before, before | {"fixtures_sha256": "different"})


def test_existing_adaptive_report_selects_algebra_independently_of_catalog_order() -> None:
    rows = [run_case(case) for case in ADAPTIVE_CASES]
    assert len(rows) == 14
    assert all(row["verdict"] != "not a mistake" and row["fits"] == "yes" for row in rows)
