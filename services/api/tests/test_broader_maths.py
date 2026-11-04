"""The declared regression suite for Quack's mathematics, with correctly transcribed lines and no model.

Passing says these lines get these answers, and nothing about mathematics in general, or about real
handwriting. `STUDENT_REPORTED` are lines from physical testing. See docs/OVERNIGHT_MATH_EXPANSION_REPORT.md.
"""

from __future__ import annotations

import random
import time
from unittest.mock import patch

import pytest
import sympy as sp

from quack.services.hint_engine import find_problems, template_text
from quack.services.integration import antiderivative
from quack.services.math_parser import (
    Ambiguous,
    MathParseError,
    Unsupported,
    format_math,
    format_node,
    parse_expression,
    parse_statement,
)
from quack.services.math_verifier import (
    LineInput,
    StepResult,
    VerificationReport,
    completes_answer,
    disposition_of,
    verify_lines,
)
from quack.services.mistake_manager import TrackedMistake
from quack.services.topics import topic_of

x = sp.Symbol("x", real=True)


def run(*lines: str, **fields: object) -> VerificationReport:
    return verify_lines([LineInput(i, text, continues="no" if i == 0 else "yes", **fields) for i, text in enumerate(lines)])  # type: ignore[arg-type]


def error_of(report: VerificationReport) -> str | None:
    step = report.first_invalid
    return step.error_type if step else None


def reason_of(report: VerificationReport) -> str | None:
    return next((step.reason for step in report.steps if step.reason), None)


class TestTheTwoReportedBugs:
    def test_integral_of_x_equals_2_is_a_mistake_with_no_context_needed(self) -> None:
        step = run("∫ x dx = 2").first_invalid
        assert step is not None
        assert step.error_category == "integration"
        assert step.expected == "x^2/2 + C"

    def test_3x_plus_2x_equals_5_is_not_called_wrong_without_knowing_the_task(self) -> None:
        report = run("3x + 2x = 5")
        assert report.first_invalid is None
        assert reason_of(report) == "ambiguous_intent"
        assert disposition_of(report.steps[0]) == "ambiguous_intent"

    @pytest.mark.parametrize(
        "fields", [{"intent": "simplify"}, {"page_intent": "simplify"}, {"posed": True}], ids=["clarified", "setting", "practice"]
    )
    def test_when_simplifying_it_is_a_mistake(self, fields: dict[str, object]) -> None:
        step = run("3x + 2x = 5", **fields).first_invalid
        assert step is not None
        assert (step.error_type, step.expected) == ("lost_variable", "5x")

    @pytest.mark.parametrize("fields", [{"intent": "solve"}, {"page_intent": "solve"}], ids=["clarified", "setting"])
    def test_when_solving_it_is_a_fine_equation(self, fields: dict[str, object]) -> None:
        report = run("3x + 2x = 5", **fields)
        assert report.status == "valid"

    def test_the_first_hint_asks_and_gives_nothing_away(self) -> None:
        step = run("3x + 2x = 5", intent="simplify").first_invalid
        assert step is not None
        mistake = _tracked(step)
        hint = template_text(1, mistake)
        assert hint == "Look at the terms on both sides. What happened to the variable?"
        assert not find_problems(1, hint, mistake)
        assert "5x" in template_text(3, mistake)


def _tracked(step: StepResult) -> TrackedMistake:
    return TrackedMistake(
        id="m", category=step.error_category or "", error_type=step.error_type or "", operation_type=step.operation_type,
        previous_step=step.previous_step or "", current_step=step.current_step or "", expected=step.expected or "",
        evidence=step.evidence or "", parsed_operation=step.parsed_operation or "",
    )


class TestSayingWhatTheLineIsDoing:
    @pytest.mark.parametrize("lines", [("Simplify:", "3x + 2x = 5"), ("Simplify: 3x + 2x = 5",), ("simplify 3x + 2x = 5",)])
    def test_written_on_the_page(self, lines: tuple[str, ...]) -> None:
        assert error_of(run(*lines)) == "lost_variable"

    @pytest.mark.parametrize("lines", [("Solve:", "3x + 2x = 5"), ("Solve 3x + 2x = 5",), ("solve: 3x + 2x = 5", "5x = 5", "x = 1")])
    def test_solving_written_on_the_page(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None
        assert reason_of(run(*lines)) is None

    def test_what_is_written_outranks_the_setting(self) -> None:
        assert run("Solve 3x + 2x = 5", page_intent="simplify").first_invalid is None
        assert error_of(run("Simplify: 3x + 2x = 5", page_intent="solve")) == "lost_variable"

    def test_saying_so_for_this_line_outranks_everything(self) -> None:
        assert run("Simplify: 3x + 2x = 5", intent="solve").first_invalid is None

    def test_a_task_ends_where_its_working_ends(self) -> None:
        lines = [
            LineInput(0, "Simplify: 3x + 2x", continues="no"),
            LineInput(1, "= 5x", continues="yes"),
            LineInput(2, "4x + 4x = 8", continues="no"),
        ]
        report = verify_lines(lines)
        assert report.first_invalid is None
        assert report.steps[-1].reason == "ambiguous_intent"

    def test_the_model_label_never_settles_it(self) -> None:
        for label in ("combine_like_terms", "simplify", "arithmetic", "solve_equation"):
            report = verify_lines([LineInput(0, "3x + 2x = 5", continues="no", operation=label)])
            assert report.first_invalid is None, label

    def test_the_next_line_answers_the_question(self) -> None:
        report = run("3x + 2x = 5", "5x = 5", "x = 1")
        assert report.status == "valid"
        assert all(step.reason != "ambiguous_intent" for step in report.steps)

    def test_a_bare_expression_above_says_it_was_being_worked_out(self) -> None:
        assert error_of(run("3x + 2x", "= 5")) == "lost_variable"

    def test_unless_the_variable_has_a_value(self) -> None:
        assert run("x = 1", "3x + 2x", "= 5").first_invalid is None

    def test_a_substitution_with_nothing_to_simplify_still_abstains(self) -> None:
        assert run("2x + 3", "= 7").first_invalid is None

    @pytest.mark.parametrize("line", ["3x + 2x = 7", "x + x + 3 = 7", "3x + 2 = 5", "2x + 3x = 10", "x^2 + x = 2"])
    def test_ordinary_equations_are_not_questioned(self, line: str) -> None:
        report = run(line)
        assert report.status == "valid"
        assert report.first_invalid is None

    @pytest.mark.parametrize("line", ["5 = 3x + 2x", "x = 5"])
    def test_nothing_is_asked_about_lines_with_nothing_to_simplify(self, line: str) -> None:
        assert reason_of(run(line)) != "ambiguous_intent"

    def test_simplify_does_not_turn_an_answer_into_a_claim(self) -> None:
        assert run("x = 3", intent="simplify").first_invalid is None

    def test_a_correction_below_clears_it(self) -> None:
        report = run("3x + 2x = 5", "3x + 2x = 5x", intent="simplify")
        assert report.first_invalid is None

    def test_a_correction_with_an_equals_clears_it(self) -> None:
        report = run("3x + 2x = 5", "= 5x", intent="simplify")
        assert report.first_invalid is None


# The finite declared suite: each must come out exactly as stated.
CORRECT = [
    # Arithmetic
    "2 + 3 = 5", "10 - 7 = 3", "-3 + 5 = 2", "-2 * -3 = 6", "3 * 4 = 12", "12 / 4 = 3", "12 ÷ 4 = 3", "2 + 3 * 4 = 14",
    "(2 + 3) * 4 = 20", "1/2 + 1/3 = 5/6", "3/4 * 2/3 = 1/2", "2.5 + 1.5 = 4", "0.1 + 0.2 = 0.3", "1/4 = 0.25", "2^10 = 1024",
    "10 - 2 - 3 = 5", "1/3 ≈ 0.33", "1/3 = 0.33 (2dp)", "12 ≈ 10 (nearest ten)",
    # Algebra
    "3x + 2x = 5x", "2(x + 3) = 2x + 6", "(x + 1)(x + 2) = x^2 + 3x + 2", "x^2 + 5x + 6 = (x + 2)(x + 3)", "x^2 * x^3 = x^5",
    "x^5 / x^2 = x^3", "(x^2)^3 = x^6", "2x + 3x + 4 = 5x + 4", "x/2 + x/2 = x", "(x + 1)^2 = x^2 + 2x + 1",
    # Differentiation
    "d/dx(x^3) = 3x^2", "d/dx(5) = 0", "d/dx(x^2 + 2x) = 2x + 2", "d/dx(a x^2) = 2a x", "d/dx(x^3 + C) = 3x^2", "d/dx(x) = 1",
    "d/dx(x^-1) = -x^-2", "d/dx(sin x) = cos x", "d/dx(cos x) = -sin x", "d/dx(e^x) = e^x", "d/dx(ln x) = 1/x",
    "d/dx((x^2 + 1)^3) = 6x(x^2 + 1)^2", "d/dx(x sin(x)) = sin(x) + x cos(x)", "d/dx(x/(x + 1)) = 1/(x + 1)^2", "d/dx(sin(2x)) = 2cos(2x)",
    # Integration
    "∫ x dx = x^2/2 + C", "∫ x^2 dx = x^3/3 + C", "∫ 2x dx = x^2 + C", "∫ 5 dx = 5x + C", "∫ dx = x + C", "∫ (2x + 1) dx = x^2 + x + C",
    "∫ 2x + 1 dx = x^2 + x + c", "∫x dx = 1/2 x^2 + C", "∫ x dx = (1/2)x^2 + K", "∫ x dx = x²/2 + C", "∫ a dx = ax + C", "∫ a x dx = a x^2/2 + C",
    "∫ cos x dx = sin x + C", "∫ sin x dx = -cos x + C", "∫ e^x dx = e^x + C", "∫ 1/x dx = ln|x| + C", "∫ x^-2 dx = -1/x + C", "∫ cos(2x) dx = sin(2x)/2 + C",
    "∫_0^1 x dx = 1/2", "∫₀¹ x dx = 1/2", "∫₀² 2x dx = 4", "∫_1^2 1/x dx = ln(2)", "∫_0^pi sin(x) dx = 2", "∫_1^0 x dx = -1/2", "∫_a^b 2x dx = b^2 - a^2", "∫_{0}^{2} x dx = 2",
    # Valid under assumptions, or only one antiderivative
    "x/x = 1", "x^2/x = x", "sqrt(x^2) = |x|", "∫ 2x dx = x^2", "∫ 2x dx = x^2 + 3", "∫ 1/x dx = ln(x) + C",
]
INCORRECT = [
    # (line, error category)
    ("2 + 3 = 6", "arithmetic"), ("10 - 7 = 4", "arithmetic"), ("-3 + 5 = -2", "arithmetic"), ("3 * 4 = 10", "arithmetic"), ("12 / 4 = 4", "arithmetic"),
    ("2 + 3 * 4 = 20", "arithmetic"), ("1/2 + 1/3 = 2/5", "arithmetic"), ("3/4 * 2/3 = 6/7", "arithmetic"), ("2.5 + 1.5 = 5", "arithmetic"), ("1/4 = 0.3", "arithmetic"),
    ("1 ÷ 3 = 0", "arithmetic"), ("1/3 = 0.34 (2dp)", "arithmetic"),
    ("d/dx(x^3) = 2x", "differentiation"), ("d/dx(x^3) = 3x", "differentiation"), ("d/dx(5) = 1", "differentiation"), ("d/dx(x^2 + 2x) = 3x + 2", "differentiation"), ("d/dx(x^3) = 2x + C", "differentiation"),
    ("d/dx(x^3) = 3x^2 + C", "differentiation"), ("d/dx(sin x) = -cos x", "differentiation"), ("d/dx(sin(2x)) = cos(2x)", "differentiation"), ("d/dx(e^(2x)) = e^(2x)", "differentiation"), ("d/dx(ln x) = x", "differentiation"),
    ("d/dx(x sin(x)) = cos(x)", "differentiation"), ("d/dx((x^2 + 1)^3) = 3(x^2 + 1)^2", "differentiation"),
    ("d/dx(x^2/(x + 1)) = 2x", "differentiation"),
    ("∫ x dx = 2", "integration"), ("∫ x dx = x^3/3 + C", "integration"), ("∫ x dx = x^2 + C", "integration"), ("∫ x^2 dx = 2x + C", "integration"), ("∫ x^2 dx = x^3 + C", "integration"), ("∫ 2x dx = 2 + C", "integration"),
    ("∫ cos x dx = -sin x + C", "integration"), ("∫ e^x dx = x e^x + C", "integration"), ("∫ 1/x dx = x + C", "integration"),
    ("∫_0^1 x dx = 2", "integration"), ("∫_0^2 2x dx = 2", "integration"), ("∫_0^1 x dx = 1", "integration"), ("∫_1^0 x dx = 1/2", "integration"),
]
SIMPLIFYING_INCORRECT = [
    ("3x + 2x = 5", "algebra_like_terms"), ("3x + 2x = 6x", "algebra_like_terms"), ("2(x + 3) = 2x + 3", "algebra_expansion"), ("(x + 1)^2 = x^2 + 1", "algebra_expansion"),
    ("x^2 + 5x + 6 = (x + 2)(x + 4)", "algebra_factorisation"), ("x^2 * x^3 = x^6", "algebra_exponents"), ("2x + 3x + 4 = 5x", "algebra_like_terms"),
    ("x^5 / x^2 = x^7", "algebra_exponents"),
]
# These must never come out as a mistake, with no task established and no working to say otherwise.
NEVER_ACCUSED = [
    ("3x + 2x = 5",), ("3x + 2x = 7",), ("2x + 3 = 7",), ("x + 1 = 2x",), ("x^2 = 4",), ("x^2 = x", "x = 1"), ("2(x + 3) = 2x + 3",), ("(x + 1)^2 = x^2 + 5",),
    ("sqrt(x^2) = x",), ("x + 1 = 2", "x + 2 = 3"), ("2x = 4", "3x = 9"), ("x + y = 3", "x - y = 1"), ("x = 2", "3(x + 1) = 9"),
    ("1/3 = 0.33",), ("2π = 6.28",), ("sin(30) = 0.5",), ("2e = 4",), ("∫ u dx = u^2/2 + C",), ("∫ 2x dx = x^2 + y",), ("∫_0^1 1/x dx = 0",), ("∫_-1^1 1/x dx = 0",),
    ("∫ x^2 dx = 0.33x^3 + C",), ("∫_0^1 x^2 dx = 0.33",), ("∫ x dx",), ("∫ sin(x^2) dx = 3",), ("y = x^2", "y = 9", "dy/dx = 6"), ("|x| = 3", "x = 3"), ("∫ x",),
]
STUDENT_REPORTED = [
    # Earlier iPad rounds: exact equality and independent lines.
    (("1 ÷ 3 = 0",), True), (("2 ÷ 3 = 1",), True), (("3 × 4 = 10",), True), (("1 ÷ 3 = 0.33",), False), (("1/3 ≈ 0.33",), False), (("d/dx(x^2 + 2x) = 3x + 2",), True),
    # This round.
    (("∫ x dx = 2",), True), (("∫ x dx = x²/2 + C",), False), (("3x + 2x = 5",), False),
]


class TestDeclaredSuite:
    @pytest.mark.parametrize("line", CORRECT)
    def test_correct(self, line: str) -> None:
        report = run(line)
        assert report.first_invalid is None
        assert report.status == "valid", reason_of(report)

    @pytest.mark.parametrize(("line", "category"), INCORRECT)
    def test_incorrect(self, line: str, category: str) -> None:
        step = run(line).first_invalid
        assert step is not None
        assert step.error_category == category
        assert step.expected
        assert disposition_of(step) == "verified_incorrect"

    @pytest.mark.parametrize(("line", "category"), SIMPLIFYING_INCORRECT)
    def test_incorrect_when_simplifying(self, line: str, category: str) -> None:
        step = run(line, intent="simplify").first_invalid
        assert step is not None
        assert step.error_category == category

    @pytest.mark.parametrize("lines", NEVER_ACCUSED)
    def test_never_accused(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    @pytest.mark.parametrize(("lines", "wrong"), STUDENT_REPORTED)
    def test_student_reported(self, lines: tuple[str, ...], wrong: bool) -> None:
        assert (run(*lines).first_invalid is not None) == wrong

    @pytest.mark.parametrize(("line", "category"), [*INCORRECT, *SIMPLIFYING_INCORRECT])
    def test_every_mistake_has_a_topic(self, line: str, category: str) -> None:
        step = run(line, intent="simplify").first_invalid
        assert step is not None
        assert topic_of(step.error_category or "", step.error_type or "", step.parsed_operation or "") != "unclassified"

    @pytest.mark.parametrize(("line", "category"), [*INCORRECT, *SIMPLIFYING_INCORRECT])
    @pytest.mark.parametrize("level", [1, 2])
    def test_no_prepared_hint_gives_the_answer_away(self, line: str, category: str, level: int) -> None:
        step = run(line, intent="simplify").first_invalid
        assert step is not None
        mistake = _tracked(step)
        hint = template_text(level, mistake)
        assert not find_problems(level, hint, mistake), hint

    @pytest.mark.parametrize(("line", "category"), [*INCORRECT, *SIMPLIFYING_INCORRECT])
    def test_every_explanation_states_the_verified_result(self, line: str, category: str) -> None:
        step = run(line, intent="simplify").first_invalid
        assert step is not None
        mistake = _tracked(step)
        assert mistake.expected.replace(" ", "") in template_text(3, mistake).replace(" ", "")


class TestDomains:
    def test_a_cancelled_variable_is_correct_under_a_condition(self) -> None:
        step = run("x/x = 1").steps[0]
        assert step.status == "valid"
        assert step.assumption
        assert disposition_of(step) == "valid_under_assumptions"

    def test_sqrt_of_a_square_is_the_absolute_value(self) -> None:
        assert run("sqrt(x^2)", "= |x|").status == "valid"

    def test_sqrt_of_a_square_as_x_depends_on_x(self) -> None:
        report = run("sqrt(x^2)", "= x")
        assert report.first_invalid is None
        assert reason_of(report) == "domain_sensitive"
        assert disposition_of(report.steps[-1]) == "valid_under_assumptions"

    def test_ln_without_absolute_value_is_for_positive_x_only(self) -> None:
        assert run("∫ 1/x dx = ln(x) + C").steps[0].assumption

    @pytest.mark.parametrize("line", ["∫_0^1 1/x dx = 5", "∫_-1^1 1/x^2 dx = -2", "∫_-1^1 ln(x) dx = 0", "∫_0^2 tan(x) dx = 1", "∫_-1^1 sqrt(x) dx = 1"])
    def test_an_integral_across_a_break_is_not_judged(self, line: str) -> None:
        report = run(line)
        assert report.first_invalid is None
        assert report.status in ("unsupported", "uncertain")

    def test_absolute_values_are_compared_as_functions(self) -> None:
        assert run("|2x|", "= 2|x|").status == "valid"
        assert run("|x|", "= x").first_invalid is None


class TestBoundedIntegration:
    @pytest.mark.parametrize(
        "line",
        [
            "∫ x^3 e^x sin(x) cos(x) dx = 1",
            "∫ sin(x)^11 cos(x)^12 tan(x)^7 dx = 1",
            "∫ x^20 e^x dx = 1",
            "∫ e^(x^2) dx = 1",
            "∫ sin(x^2) dx = 1",
            "∫ 2^x 3^x 5^x x^9 dx = 1",
            "∫ ln(x)^9 x^9 dx = 1",
            "∫_0^1 x^9 e^x sin(x) dx = 1",
        ],
    )
    def test_a_hard_integral_comes_back_quickly_and_accuses_nobody_it_cant_show(self, line: str) -> None:
        started = time.perf_counter()
        report = run(line)
        assert time.perf_counter() - started < 3
        for step in report.invalid_steps:
            assert step.expected

    def test_a_timed_out_integral_is_no_answer(self) -> None:
        with patch("quack.services.integration.manualintegrate", side_effect=TimeoutError):
            antiderivative.cache_clear()
            assert run("∫ sin(x) cos(2x) x dx = 3").first_invalid is None
        antiderivative.cache_clear()

    def test_what_the_integrator_says_is_checked(self) -> None:
        with patch("quack.services.integration.manualintegrate", return_value=x**5):
            antiderivative.cache_clear()
            assert antiderivative(sp.sin(x), x) is None
        antiderivative.cache_clear()


class TestConstants:
    def test_general_and_particular_are_told_apart(self) -> None:
        assert run("∫ 2x dx = x^2 + C").steps[0].note is None
        assert run("∫ 2x dx = x^2").steps[0].note is None
        assert run("∫ 2x dx = x^2", posed=True).steps[0].note == "missing_constant"
        assert run("Integrate 2x = x^2").steps[0].note == "missing_constant"

    def test_a_note_is_never_a_mistake(self) -> None:
        report = run("∫ 2x dx = x^2", posed=True)
        assert report.status == "valid"
        assert report.first_invalid is None

    def test_a_multiplied_constant_is_wrong(self) -> None:
        assert run("∫ x dx = C x^2/2").first_invalid is not None

    def test_c_can_be_a_parameter_of_the_integrand(self) -> None:
        assert run("∫ c dx = c x + C").status == "valid"
        assert run("∫ c dx = c x").status == "valid"

    def test_the_same_constant_differentiates_away(self) -> None:
        assert run("d/dx(x^3 + C) = 3x^2").status == "valid"
        step = run("d/dx(x^3 + C) = 3x^2 + C").first_invalid
        assert step is not None

    def test_a_new_letter_in_a_derivative_is_only_excused_if_it_could_be_anything(self) -> None:
        assert run("d/dx(x^2) = 2u").first_invalid is None


class TestWorkingOverSeveralLines:
    def test_a_correct_chain_is_silent(self) -> None:
        assert run("3(x + 2) = 15", "3x + 6 = 15", "3x = 9", "x = 3").status == "valid"

    def test_only_the_first_wrong_step_is_reported(self) -> None:
        report = run("3(x + 2) = 15", "3x + 6 = 15", "3x = 21", "x = 7")
        invalid = report.invalid_steps
        assert [step.line for step in invalid] == [2]

    def test_a_first_wrong_step_doesnt_hide_a_separate_problem(self) -> None:
        report = verify_lines(
            [
                LineInput(0, "3 * 4 = 10", continues="no"),
                LineInput(1, "∫ x dx = 2", continues="no"),
                LineInput(2, "d/dx(x^3) = 2x", continues="no"),
            ]
        )
        assert [step.line for step in report.invalid_steps] == [0, 1, 2]
        assert len({step.fingerprint for step in report.invalid_steps}) == 3

    def test_integrals_on_one_line_after_another_are_separate(self) -> None:
        report = run("∫ x dx = 2", "∫ x^2 dx = 5")
        assert [step.line for step in report.invalid_steps] == [0, 1]

    def test_an_integral_put_right_on_the_next_line(self) -> None:
        report = run("∫ x dx = 2", "∫ x dx = x^2/2 + C")
        assert report.first_invalid is None
        assert report.steps[0].reason == "corrected_on_the_next_line"

    def test_an_integral_worked_through_with_equals(self) -> None:
        assert run("∫ 2x dx", "= 2 * x^2/2 + C", "= x^2 + C").status == "valid"

    def test_a_step_after_an_integral_is_checked(self) -> None:
        assert run("∫ 2x dx", "= x^2 + C", "= x^3 + C").first_invalid is not None

    def test_the_same_line_read_twice_is_one_identity(self) -> None:
        one = run("∫ x dx = 2").first_invalid
        two = run("∫x dx = 2").first_invalid
        assert one and two and one.fingerprint == two.fingerprint

    def test_quadratic_solutions(self) -> None:
        assert run("x^2 - 5x + 6 = 0", "(x - 2)(x - 3) = 0", "x = 2 or x = 3").status == "valid"
        assert run("x^2 - 5x + 6 = 0", "x = 2 or x = 4").first_invalid is not None


class TestParsingLimits:
    @pytest.mark.parametrize(
        "text",
        [
            "∫" * 40 + " x" + " dx" * 40,
            "∫ " + "(" * 30 + "x" + ")" * 30 + " dx",
            "|" * 30 + "x" + "|" * 30,
            "∫ x^99999 dx",
            "∫_0^99999999999999 x dx",
            "∫ " + " + ".join(f"x^{i}" for i in range(80)) + " dx",
            "∫ x " + "dx " * 150,
            "Simplify " * 40,
            "∫_(∫_0^1 x dx)^1 x dx",
        ],
    )
    def test_pathological_input_never_raises_anything_else(self, text: str) -> None:
        report = verify_lines([LineInput(0, f"{text} = 1", continues="no")])
        assert report.first_invalid is None or report.status == "invalid"

    def test_integration_never_reaches_sympify(self) -> None:
        with pytest.raises(MathParseError):
            parse_expression("∫ __import__('os') dx")
        with pytest.raises(MathParseError):
            parse_statement("Simplify: __class__")

    def test_a_differential_outside_an_integral_is_only_letters(self) -> None:
        assert format_node(parse_expression("dx")) == "dx"

    @pytest.mark.parametrize("text", ["sin x^2", "sin 2x", "1/2x", "x|", "|x"])
    def test_still_refused(self, text: str) -> None:
        with pytest.raises(MathParseError):
            parse_expression(text)

    def test_a_gap_says_where_a_fraction_stops(self) -> None:
        assert sp.simplify(sp.sympify("x**2/2", locals={"x": x}) - _value("1/2 x^2")) == 0
        with pytest.raises(Ambiguous):
            parse_expression("1/2x^2")

    def test_only_a_fraction_of_numbers(self) -> None:
        with pytest.raises(Ambiguous):
            parse_expression("x/2 y")

    def test_rendering_keeps_the_meaning(self) -> None:
        assert format_node(parse_expression("(1/2)x^2")) == "(1/2)x^2"
        assert format_node(parse_expression("∫_0^1 x dx")) == "∫_0^1 x dx"
        assert format_node(parse_expression("ln|x|")) == "ln(|x|)"
        assert format_math(sp.log(sp.Abs(x))) == "ln|x|"

    def test_a_cap_on_integrand_size_applies(self) -> None:
        with pytest.raises(Unsupported):
            parse_expression("∫ " + " + ".join(["x"] * 200) + " dx")


def _value(text: str) -> sp.Expr:
    from quack.services.math_parser import to_sympy

    return to_sympy(parse_expression(text))


def _poly_text(coefficients: dict[int, int]) -> str:
    terms = []
    for power in sorted(coefficients, reverse=True):
        value = coefficients[power]
        if value == 0:
            continue
        body = f"{abs(value)}" if power == 0 else f"{abs(value)}x" if power == 1 else f"{abs(value)}x^{power}"
        terms.append((" - " if value < 0 else " + ") + body if terms else ("-" if value < 0 else "") + body)
    return "".join(terms) or "0"


def _random_poly(rng: random.Random, low: int = 1) -> dict[int, int]:
    return {power: rng.choice([-5, -4, -3, -2, -1, 1, 2, 3, 4, 5]) for power in rng.sample(range(low, 6), rng.randint(1, 3))}


def _as_sympy(coefficients: dict[int, int]) -> sp.Expr:
    return sum((value * x**power for power, value in coefficients.items()), sp.Integer(0))


class TestProperties:
    """Seeded, so that a failure is the same failure every time. The truth comes from exact SymPy polynomials."""

    @pytest.mark.parametrize("seed", range(40))
    def test_derivatives_of_polynomials(self, seed: int) -> None:
        rng = random.Random(seed)
        poly = _random_poly(rng)
        derivative = {power - 1: value * power for power, value in poly.items()}
        assert sp.expand(sp.diff(_as_sympy(poly), x) - _as_sympy(derivative)) == 0
        assert run(f"d/dx({_poly_text(poly)}) = {_poly_text(derivative)}").status == "valid"
        wrong = dict(derivative)
        wrong[min(wrong)] += rng.choice([-2, -1, 1, 2])
        assert run(f"d/dx({_poly_text(poly)}) = {_poly_text(wrong)}").first_invalid is not None

    @pytest.mark.parametrize("seed", range(40))
    def test_integrals_of_polynomials_with_whole_answers(self, seed: int) -> None:
        rng = random.Random(1000 + seed)
        answer = _random_poly(rng)
        integrand = {power - 1: value * power for power, value in answer.items()}
        assert run(f"∫ {_poly_text(integrand)} dx = {_poly_text(answer)} + C").status == "valid"
        assert run(f"∫ {_poly_text(integrand)} dx = {_poly_text(answer)}").status == "valid"
        wrong = dict(answer)
        wrong[max(wrong)] += rng.choice([-2, -1, 1, 2])
        assert sp.expand(sp.diff(_as_sympy(wrong), x) - _as_sympy(integrand)) != 0
        assert run(f"∫ {_poly_text(integrand)} dx = {_poly_text(wrong)} + C").first_invalid is not None

    @pytest.mark.parametrize("seed", range(30))
    def test_definite_integrals_match_exact_integration(self, seed: int) -> None:
        rng = random.Random(2000 + seed)
        integrand = _random_poly(rng, low=0)
        low, high = sorted(rng.sample(range(-4, 5), 2))
        value = sp.integrate(_as_sympy(integrand), (x, low, high))
        line = f"∫_{low}^{high} {_poly_text(integrand)} dx"
        assert run(f"{line} = {value}" if value >= 0 else f"{line} = -{abs(value)}").first_invalid is None
        assert run(f"{line} = {value + 1}" if value + 1 >= 0 else f"{line} = -{abs(value + 1)}").first_invalid is not None

    @pytest.mark.parametrize("seed", range(30))
    def test_expansions_with_an_explicit_task(self, seed: int) -> None:
        rng = random.Random(3000 + seed)
        a, b, c = rng.randint(2, 9), rng.randint(1, 9), rng.randint(1, 9)
        expanded = sp.expand(a * (x + b) * (x + c))
        coefficients = {power: int(sp.Poly(expanded, x).coeff_monomial(x**power)) for power in range(3)}
        assert run(f"{a}(x + {b})(x + {c}) = {_poly_text(coefficients)}", intent="expand").status == "valid"
        coefficients[0] += 1
        assert run(f"{a}(x + {b})(x + {c}) = {_poly_text(coefficients)}", intent="expand").first_invalid is not None

    @pytest.mark.parametrize("seed", range(30))
    def test_like_terms_with_an_explicit_task(self, seed: int) -> None:
        rng = random.Random(4000 + seed)
        a, b = rng.randint(2, 9), rng.randint(2, 9)
        assert run(f"{a}x + {b}x = {a + b}x", intent="simplify").status == "valid"
        assert error_of(run(f"{a}x + {b}x = {a + b}", intent="simplify")) == "lost_variable"
        # And without the task, the same line is an equation until it's said otherwise.
        assert run(f"{a}x + {b}x = {a + b}").first_invalid is None

    @pytest.mark.parametrize("seed", range(30))
    def test_linear_equation_steps(self, seed: int) -> None:
        rng = random.Random(5000 + seed)
        a, solution, b = rng.randint(2, 9), rng.randint(-6, 9), rng.randint(1, 9)
        rhs = a * solution + b
        assert run(f"{a}x + {b} = {rhs}", f"{a}x = {rhs - b}", f"x = {solution}").status == "valid"
        step = run(f"{a}x + {b} = {rhs}", f"{a}x = {rhs + b}").first_invalid
        assert step is not None
        assert step.error_category == "equation_solving"

    @pytest.mark.parametrize("seed", range(20))
    def test_equivalent_forms_are_accepted(self, seed: int) -> None:
        rng = random.Random(6000 + seed)
        a, b = rng.randint(2, 9), rng.randint(1, 9)
        for form in (f"{a}x + {a * b}", f"{a * b} + {a}x", f"{a}(x + {b})"):
            assert run(f"{a}(x + {b}) = {form}", intent="expand").status == "valid"

    def test_every_answer_round_trips_through_completion(self) -> None:
        assert completes_answer("∫ 2x dx", "x^2 + C", "∫ 2x dx = x^2 + C")
        assert completes_answer("∫ 2x dx", "x^2 + C", "∫ 2x dx = x^2 + c")
        assert not completes_answer("∫ 2x dx", "x^2 + C", "∫ 2x dx = x^2")
        assert not completes_answer("∫ 2x dx", "x^2 + C", "∫ 2x dx = x^3 + C")
        assert completes_answer("∫_0^2 2x dx", "4", "∫_0^2 2x dx = 4")
        assert completes_answer("2x + 3 = 7", "x = 2", "x = 2")
        assert not completes_answer("2x + 3 = 7", "x = 2", "2x = 4")
        assert not completes_answer("2x + 3 = 7", "x = 2", "x = 3")


class TestGivenEquation:
    def line(self, math: str) -> LineInput:
        return LineInput(0, math, continues="yes", given="2x + 3 = 7", intent="solve")

    def test_the_answer_follows_from_the_equation_set(self) -> None:
        assert verify_lines([self.line("x = 2")]).status == "valid"
        assert verify_lines([self.line("x = 3")]).first_invalid is not None
        assert verify_lines([self.line("2x = 4"), LineInput(1, "x = 2", continues="yes", intent="solve")]).status == "valid"
        assert verify_lines([self.line("2x = 5")]).first_invalid is not None


class TestIndependentSumsWhoseValuesHappenToMeet:
    def _steps(self, continues: str):
        lines = [
            LineInput(0, "3 * 4 = 10", continues=continues, operation="arithmetic"),
            LineInput(1, "5 + 5 = 11", continues=continues, operation="arithmetic"),
        ]
        return verify_lines(lines).steps

    def test_a_line_known_to_be_its_own_is_its_own_mistake(self):
        assert [step.status for step in self._steps("no")] == ["invalid", "invalid"]

    def test_with_nothing_said_it_waits_for_the_first_to_be_dealt_with(self):
        steps = self._steps("unknown")
        assert steps[0].status == "invalid"
        assert (steps[1].status, steps[1].reason) == ("uncertain", "follows_a_new_mistake")

    def test_a_line_said_to_carry_on_is_still_one_slip(self):
        steps = self._steps("yes")
        assert steps[0].status == "invalid"
        assert steps[1].status != "invalid"
