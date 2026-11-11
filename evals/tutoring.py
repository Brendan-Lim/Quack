"""Offline tutoring trajectories using the existing HTTP test doubles and real verifier.

Run with the backend development Python: python evals/tutoring.py --output /tmp/tutoring.json
No real model, real handwriting, hosted database or student record is used. Assertions measure
software contracts, not recognition accuracy, the student's thought process or learning gains.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/api"))

import httpx  # noqa: E402
import respx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# isort: split
from quack.main import build_services, create_app  # noqa: E402
from quack.services.hint_engine import find_problems  # noqa: E402
from quack.services.practice import generate_question  # noqa: E402
from tests.accounts_support import account_settings  # noqa: E402
from tests.test_learning import Model, Student  # noqa: E402
from tests.test_line_memory import compact  # noqa: E402
from tests.test_pipeline import CHAT, completion, png  # noqa: E402


@dataclass(frozen=True)
class Trajectory:
    id: str
    question: str
    wrong: str
    right: str
    error: str
    topic: str
    seed: int


CASES = (
    Trajectory("sine_square", "d/dx(sin(x^2))", "cos(x^2)", "2x*cos(x^2)",
               "chain_rule_inner_missing", "chain_rule", 0),
    Trajectory("cosine_linear", "d/dx(cos(3x))", "-sin(3x)", "-3sin(3x)",
               "chain_rule_inner_missing", "chain_rule", 1),
    Trajectory("exponential_linear", "d/dx(exp(2x))", "exp(2x)", "2exp(2x)",
               "chain_rule_inner_missing", "chain_rule", 2),
    Trajectory("power_of_bracket", "d/dx((2x+1)^3)", "3(2x+1)^2", "6(2x+1)^2",
               "chain_rule_inner_missing", "chain_rule", 3),
    Trajectory("power_rule_control", "d/dx(x^3)", "2x^2", "3x^2",
               "power_rule_coefficient", "power_rule", 4),
)


def isolated_client(directory: Path, **overrides: Any) -> TestClient:
    # Explicit overrides beat shell credentials and prevent inherited database/route settings.
    settings = account_settings(
        directory, QUACK_DATABASE_URL="", SUPABASE_URL="", QUACK_LEDGER_BACKEND="file",
        QUACK_ROUTES="", QUACK_PERSONALIZATION="off", **overrides,
    )
    return TestClient(create_app(build_services(settings)))


class Script(Model):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return super().__call__(request)


def evaluate_trajectory(case: Trajectory, directory: Path) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    observations: dict[str, Any] = {}
    model = Script()
    # respx refuses every unregistered HTTP call. The only provider URL uses a fake key.
    with respx.mock(assert_all_called=False) as mock, isolated_client(directory) as client:
        mock.post(CHAT).mock(side_effect=model)
        student = Student(client)
        first = student.check(model, f"{case.question} = {case.wrong}")
        mistakes = first["mistakes"]
        checks["verified_error_and_category"] = bool(
            len(mistakes) == 1 and mistakes[0]["verified"] and mistakes[0]["errorType"] == case.error
            and first["intervention"]["shouldQuack"]
        )
        if not mistakes:
            return {"case": case.id, "checks": checks, "mock_provider_calls": model.calls}
        mistake = mistakes[0]["id"]
        checks["learning_accepts_detection"] = not student.event("mistake_detected", mistake)["refused"]
        checks["duplicate_alert_suppressed"] = not student.check(
            model, f"{case.question} = {case.wrong}", [mistake]
        )["intervention"]["shouldQuack"]
        before_hints = model.calls
        hints = [client.post("/api/hint", json={
            "sessionId": student.page, "mistakeId": mistake, "level": level,
            "studySessionId": student.session,
        }, headers=student.headers).json() for level in (1, 2)]
        checks["bounded_prepared_hints"] = model.calls == before_hints and all(h["source"] == "template" for h in hints)
        # Reuse the product's spoiler and fidelity checks against its private verified record.
        user = client.get("/api/profile", headers=student.headers).json()["userId"]
        tracked = client.app.state.services.mistakes.get_mistake(f"{user}:{student.page}", mistake)
        checks["hint_fidelity_and_no_spoiler"] = tracked is not None and all(
            not find_problems(level, hint["text"], tracked) for level, hint in enumerate(hints, 1)
        )
        fixed = student.check(model, f"{case.question} = {case.right}", [mistake])
        checks["correction_verified"] = fixed["resolvedMistakeIds"] == [mistake] and not fixed["mistakes"]
        checks["learning_accepts_correction"] = not student.event("correction_verified", mistake)["refused"]
        student.event("correction_verified", mistake)  # Retries cannot inflate progress.
        entry = client.get(f"/api/mistakes/{mistake}", headers=student.headers).json()
        checks["mistake_offers_targeted_practice"] = entry["practiceTopic"] == case.topic
        recap = student.finish()
        checks["counts_once_with_help"] = (
            recap["glance"]["found"] == recap["glance"]["corrected"] == recap["glance"]["assisted"] == 1
        )
        recommendation = recap["recommendation"]
        checks["recap_offers_targeted_practice"] = recommendation is not None and recommendation["topic"] == case.topic
        # Pin question generation without changing IDs or any model/verifier behaviour.
        with patch("quack.services.practice.generate_question", side_effect=lambda topic, difficulty, rng: generate_question(
            topic, difficulty, random.Random(case.seed)
        )):
            before = model.calls
            response = client.post("/api/practice/sessions", json={
                "topic": case.topic, "originSessionId": student.session, "sourceMistakeId": mistake,
            }, headers=student.headers)
        checks["verified_practice_available_without_model"] = response.status_code == 200 and before == model.calls
        checks["practice_wrong_then_correct"] = False
        checks["practice_completion_counted_once"] = False
        checks["answer_hidden_on_creation"] = False
        if response.status_code == 200:
            problem = response.json()
            user = client.get("/api/profile", headers=student.headers).json()["userId"]
            row = client.app.state.services.accounts.store.problem(user, problem["id"])
            checks["answer_hidden_on_creation"] = row["expected_answer"] not in json.dumps(problem)

            def answer(text: str, version: int, known: list[str]) -> dict[str, Any]:
                model.reply = [compact(text)]
                result = client.post(f"/api/practice/problems/{problem['id']}/check", json={
                    "sessionId": problem["checkSessionId"], "snapshotVersion": version,
                    "image": png(), "acknowledgedMistakeIds": known,
                    "inkLines": [{"id": f"practice-{version}", "inImage": True}],
                }, headers=student.headers)
                result.raise_for_status()
                return result.json()

            wrong = answer(f"2*({row['expected_answer']})", 1, [])
            known = [m["id"] for m in wrong["mistakes"]]
            right = answer(row["expected_answer"], 2, known)
            observations["practice"] = {
                "question": row["prompt_math"], "wrong_outcome": wrong["outcome"], "right_outcome": right["outcome"],
                "wrong_status": wrong["status"], "right_status": right["status"],
            }
            answer(row["expected_answer"], 3, [])
            checks["practice_wrong_then_correct"] = wrong["outcome"] == "incorrect" and right["outcome"] == "correct"
            summary = client.post(f"/api/practice/sessions/{problem['sessionId']}/finish", headers=student.headers).json()
            checks["practice_completion_counted_once"] = summary["correct"] == summary["attempted"] == 1
        checks["bounded_reading_calls"] = model.calls <= 12
    return {"case": case.id, "checks": checks, "observations": observations, "mock_provider_calls": model.calls}


SAFETY_CASES = ("ambiguous", "disagreement", "malformed", "timeout", "zero_budget")


def evaluate_safety(case: str, directory: Path) -> dict[str, Any]:
    overrides = {"QUACK_BUDGET_PROJECT_USD": 0} if case == "zero_budget" else {}
    with respx.mock(assert_all_called=False) as mock, isolated_client(directory, **overrides) as client:
        student = Student(client)
        wrong = compact("d/dx(sin(x^2)) = cos(x^2)")
        route = mock.post(CHAT)
        if case == "timeout":
            route.mock(side_effect=httpx.ReadTimeout("synthetic timeout"))
        elif case == "malformed":
            route.mock(return_value=completion({"not_lines": []}))
        elif case == "disagreement":
            right = compact("d/dx(sin(x^2)) = 2x*cos(x^2)")
            route.mock(side_effect=[completion({"lines": [wrong]}), completion({"lines": [right]})])
        else:
            if case == "ambiguous":
                wrong.update(legibility="unsure", alternatives=["d/dx(sin(x^2)) = 2x*cos(x^2)"])
            route.mock(return_value=completion({"lines": [wrong]}))
        reply = client.post("/api/analyze", headers=student.headers, json={
            "sessionId": student.page, "snapshotVersion": 1, "image": png(),
            "inkLines": [{"id": "guard", "inImage": True}], "studySessionId": student.session,
        }).json()
        checks = {
            "no_false_alert": not reply["mistakes"] and not reply["intervention"]["shouldQuack"],
            "no_learning_penalty": student.finish()["glance"]["found"] == 0,
            # Schema validation permits one repair per reading; timeouts do not retry.
            "bounded_reading_calls": route.call_count <= (4 if case == "malformed" else 2),
        }
        if case == "zero_budget":
            checks["budget_blocks_provider"] = route.call_count == 0
    return {"case": case, "checks": checks, "mock_provider_calls": route.call_count}


def run() -> dict[str, Any]:
    rows = []
    # Match the release runner: shell credentials, proxy configuration and routes are irrelevant.
    env = {key: value for key, value in os.environ.items() if not key.startswith(("MODEL_", "QUACK_", "SUPABASE_"))
           and key.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}}
    with patch.dict(os.environ, env, clear=True), tempfile.TemporaryDirectory(prefix="quack-tutoring-") as temp:
        for case in CASES:
            directory = Path(temp) / case.id
            directory.mkdir()
            rows.append(evaluate_trajectory(case, directory))
        for guard in SAFETY_CASES:
            directory = Path(temp) / guard
            directory.mkdir()
            rows.append(evaluate_safety(guard, directory))
    fixtures = json.dumps({"trajectories": [asdict(case) for case in CASES], "safety": SAFETY_CASES}, sort_keys=True)
    return {"version": 1, "mode": "offline-scripted-readings", "live_model_calls": 0,
            "fixtures_sha256": hashlib.sha256(fixtures.encode()).hexdigest(),
            "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "rows": rows, "passed_cases": sum(all(row["checks"].values()) for row in rows), "cases": len(rows)}


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Reject incomparable evidence, including changed fixtures or silently missing checks."""

    if any(baseline[key] != current[key] for key in ("version", "mode", "fixtures_sha256", "evaluator_sha256")):
        raise ValueError("baseline uses a different evaluation method or fixtures")
    before = {row["case"]: row["checks"] for row in baseline["rows"]}
    after = {row["case"]: row["checks"] for row in current["rows"]}
    if before.keys() != after.keys() or any(before[case].keys() != after[case].keys() for case in before):
        raise ValueError("baseline and current results must contain the same cases and checks")
    changed = [{"case": case, "check": check, "before": old, "after": after[case][check]}
               for case, checks in before.items() for check, old in checks.items() if old != after[case][check]]
    return {"before_passed_cases": baseline["passed_cases"], "after_passed_cases": current["passed_cases"],
            "changed_checks": changed, "regressions": [item for item in changed if not item["after"]]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, help="compare with a report produced by this same evaluator")
    args = parser.parse_args()
    result = run()
    if args.baseline:
        result["comparison"] = compare(json.loads(args.baseline.read_text()), result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    for row in result["rows"]:
        failures = [name for name, passed in row["checks"].items() if not passed]
        print(f"{row['case']}: {'PASS' if not failures else ', '.join(failures)}")
    print(f"{result['passed_cases']}/{result['cases']} trajectories passed; no live model calls.")
    return 0 if result["passed_cases"] == result["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
