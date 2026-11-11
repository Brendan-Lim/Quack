"""Do different verified mistakes lead to different, fitting next questions? No model, no network.

    PYTHONPATH=. .venv/bin/python scripts/adaptive_eval.py > ../../docs/reports/adaptive-eval.md

Each case is a level's first question with a wrong answer a student could write. The real verifier
judges the answer, the real classifier names the misconception from what the verifier reported, and
the real policy chooses the next question. That choice is set beside the one made with the same
history and every mistake left unnamed, which is what Quack would do with no misconception taxonomy.
The same seed gives the same table every time.

It shows what the policy does with a verified mistake. It isn't evidence that anyone learns more.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from quack.services.adaptive import Attempt, choose_next, normal_progression
from quack.services.curriculum import course
from quack.services.lesson_questions import TEMPLATES, build
from quack.services.math_verifier import LineInput, verify_lines
from quack.services.misconceptions import TAXONOMY, classify


@dataclass(frozen=True)
class Case:
    level: str
    # What the student wrote, as the answer line, or the lines under an equation, split by ";".
    wrong: str
    # Whether they put it right afterwards, before moving on.
    corrected: bool
    note: str
    # A question other than the level's first, as (template, parameters).
    asked: tuple[str, dict[str, object]] | None = None


CASES = (
    Case("combine_like_terms", "5", True, "Missing variable, then fixed"),
    Case("combine_like_terms", "5x^2", False, "Letters multiplied"),
    Case("combine_like_terms", "6", False, "Wrong total, letter kept off: not a named slip"),
    Case("simplify_expressions", "9x", True, "Unlike terms combined, then fixed"),
    Case("simplify_expressions", "8x + 5", False, "Sign of a term changed"),
    Case("brackets_and_factors", "5x + 3", True, "Incomplete distribution, then fixed"),
    Case("brackets_and_factors", "5x + 8", False, "Wrong product: not a named slip"),
    Case("solve_linear_equations", "2x = 24", True, "Moved + 7 without changing its sign, then fixed"),
    Case("substitute_and_evaluate", "57", True, "Value written next to the number, then fixed"),
    Case("meet_the_variables", "y^5", False, "Repeated adding written as a power"),
    # The mixed final challenge, where the next question can come from any of five skills.
    Case("algebra_adventure", "4x - 1 = 12", False, "Final challenge: bracket term missed in an equation",
         ("linear_equations.brackets", {"a": 4, "b": 1, "solution": 4})),
    Case("algebra_adventure", "7x + 2", True, "Final challenge: bracket term missed while simplifying, then fixed",
         ("brackets.two_brackets_added", {"a": 2, "b": 3, "c": 5, "d": 1})),
    Case("algebra_adventure", "3x = -6; x = -9", False, "Final challenge: took away 3 instead of dividing by it",
         ("linear_equations.negative_solution", {"a": 3, "b": 20, "solution": -2})),
    Case("algebra_adventure", "-21", True, "Final challenge: negative squared to a negative, then fixed",
         ("substitution.square_negative", {"value": 3, "a": 4})),
)


def run_case(case: Case) -> dict[str, str]:
    algebra = course("algebra_foundations")
    assert algebra is not None
    level = algebra.level(case.level)
    assert level is not None
    template, params = case.asked or (level.opener.template, dict(level.opener.params))  # type: ignore[union-attr]
    question = build(template, random.Random(0), dict(params))
    if question.method == "equation":
        written = [part.strip() for part in case.wrong.split(";")]
        lines = [
            LineInput(index, text, continues="yes", intent="solve", given=question.question.math)
            for index, text in enumerate(written)
        ]
    else:
        lines = [LineInput(0, f"{question.question.math} = {case.wrong}", posed=True)]
    step = verify_lines(lines).first_invalid
    if step is None:
        return {"case": case.note, "question": question.question.display, "wrong": case.wrong, "verdict": "not a mistake"}
    named = classify(
        question.skill, question.question.math, question.method, question.probes, step.error_category or "",
        step.error_type or "", step.previous_step or "", step.current_step or "",
    )
    attempt = Attempt(
        "p1", "visit", question.template, question.skill, question.tier, "correct" if case.corrected else "attempted",
        ((named.misconception, named.confidence),), False, "start", None,
    )
    adaptive = choose_next(level, [attempt], "visit", random.Random(1))
    baseline = normal_progression(level, [attempt], "visit", random.Random(1))
    targets = TEMPLATES[adaptive.template].targets
    return {
        "case": case.note,
        "question": question.question.display,
        "wrong": case.wrong,
        "verdict": f"{step.error_type}",
        "named": f"{named.misconception} ({named.confidence})",
        "adaptive": f"{adaptive.kind}: {adaptive.template} [{adaptive.tier}]",
        "fits": "yes" if adaptive.target is None or adaptive.target in targets else "NO",
        "baseline": f"{baseline.kind}: {baseline.template} [{baseline.tier}]",
        # The question itself, not what it's called: a new label on the same template isn't adapting.
        "changed": "yes" if (adaptive.template, adaptive.tier) != (baseline.template, baseline.tier) else "no",
    }


def main() -> None:
    rows = [run_case(case) for case in CASES]
    print("# Adaptive selection evaluation\n")
    print("Generated by `services/api/scripts/adaptive_eval.py`. Real verifier, classifier and policy; no model.")
    print("Every mistake is verified by SymPy before anything is named. Synthetic answers, not handwriting.\n")
    print(
        "| Case | Question | Wrote | Verifier | Named | Adaptive next | Targets it "
        "| Mistake-blind baseline | Question differs |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for row in rows:
        print(
            f"| {row['case']} | {row['question']} | `{row['wrong']}` | {row.get('verdict', '')} | {row.get('named', '')} "
            f"| {row.get('adaptive', '')} | {row.get('fits', '')} | {row.get('baseline', '')} | {row.get('changed', '')} |"
        )
    named = [row for row in rows if "(confirmed)" in row.get("named", "")]
    changed = [row for row in named if row["changed"] == "yes"]
    unnamed = [row for row in rows if "(confirmed)" not in row.get("named", "") and "named" in row]
    print(f"\n- Confirmed misconceptions: {len(named)} of {len(rows)} cases. The next question differed in {len(changed)}.")
    same = [row["case"] for row in named if row["changed"] == "no"]
    if same:
        print(f"- Confirmed, and the same question as the baseline anyway: {'; '.join(same)}.")
    print(f"- Not confirmed: {len(unnamed)}. Nothing is targeted for these, by design; any difference from the")
    print("  baseline is only that a verified mistake keeps the tier where it is.")
    print(f"- Misconceptions in the taxonomy: {len(TAXONOMY)}.")


if __name__ == "__main__":
    main()
