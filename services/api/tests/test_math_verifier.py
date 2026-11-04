import random
import time
from collections import Counter

import pytest

from quack.services.math_verifier import LineInput, StepStatus, VerificationReport, still_present, verify_lines


def run(*lines: str, continues: str = "yes", operation: str = "unknown") -> VerificationReport:
    return verify_lines(
        [
            LineInput(index=i, math=text, continues=continues, operation=operation)  # type: ignore[arg-type]
            for i, text in enumerate(lines)
        ]
    )


def status(*lines: str, **kwargs: str) -> StepStatus:
    return run(*lines, **kwargs).status


class TestAlgebra:
    def test_demo_distribution_valid(self) -> None:
        assert status("2(x + 3)", "= 2x + 6") == "valid"

    def test_demo_distribution_invalid(self) -> None:
        report = run("2(x + 3)", "= 2x + 3")
        assert report.status == "invalid"
        step = report.first_invalid
        assert step is not None
        assert step.error_category == "algebra_expansion"
        assert step.error_type == "incorrect_distribution"
        assert step.expected == "2x + 6"
        assert step.from_line == 0 and step.line == 1

    def test_the_students_working_is_not_rewritten_into_the_answer(self) -> None:
        step = run("2(x + 3)", "= 2x + 3").first_invalid
        assert step is not None
        assert step.previous_step == "2(x + 3)"
        assert step.current_step == "2x + 3"

    def test_demo_correct_stays_silent(self) -> None:
        assert status("3(x + 2)", "= 3x + 6") == "valid"

    def test_like_terms(self) -> None:
        assert status("2x + 3x", "= 5x") == "valid"
        assert run("2x + 3x", "= 6x").first_invalid.error_type == "combining_like_terms"  # type: ignore[union-attr]

    def test_sign_error_is_named(self) -> None:
        step = run("-(x - 3)", "= -x - 3").first_invalid
        assert step is not None
        assert step.error_type == "sign_error"

    def test_binomial_square(self) -> None:
        assert status("(x + 1)^2", "= x^2 + 2x + 1") == "valid"
        step = run("(x + 1)^2", "= x^2 + 1").first_invalid
        assert step is not None
        assert step.error_type == "power_of_sum_expanded_termwise"

    def test_unconventional_but_correct_simplification(self) -> None:
        assert status("(x+1)(x+2)", "= x(x+3) + 2") == "valid"
        assert status("x/2", "= 0.5x") == "valid"
        assert status("2x + 6", "= 2(x + 3)") == "valid"

    def test_factorisation(self) -> None:
        assert status("x^2 + 3x + 2", "= (x + 1)(x + 2)") == "valid"
        step = run("x^2 + 3x + 2", "= (x + 1)(x + 3)").first_invalid
        assert step is not None
        assert step.error_category == "algebra_factorisation"

    def test_exponent_rule(self) -> None:
        assert status("x^2 * x^3", "= x^5") == "valid"
        step = run("x^2 * x^3", "= x^6").first_invalid
        assert step is not None
        assert step.error_type == "exponent_rule"

    def test_arithmetic_on_one_line_needs_no_model_hint(self) -> None:
        assert status("3 + 4 = 7", continues="unknown") == "valid"
        assert status("3 + 4 = 8", continues="unknown") == "invalid"

    def test_fractions(self) -> None:
        assert status("1/2 + 1/3", "= 5/6") == "valid"
        step = run("1/2 + 1/3", "= 2/5").first_invalid
        assert step is not None

    def test_one_line_identity_needs_a_simplify_intent(self) -> None:
        assert status("2(x + 3) = 2x + 3", operation="expand") == "invalid"
        assert status("2(x + 3) = 2x + 3", operation="solve_equation") == "valid"
        assert status("2(x + 3) = 2x + 3", operation="unknown") == "valid"

    def test_plain_equation_is_not_an_identity_even_if_labelled_simplify(self) -> None:
        assert status("2x + 3 = 7", operation="simplify") == "valid"
        assert status("x + 3 = 2x + 1", operation="simplify") == "valid"


class TestRoundingAndDomain:
    def test_rounded_decimal_abstains(self) -> None:
        assert status("1/3", "= 0.33") == "uncertain"
        assert status("2π", "= 6.28") == "uncertain"

    def test_badly_wrong_decimal_is_flagged(self) -> None:
        assert status("1/2", "= 0.75") == "invalid"

    def test_cancellation_is_accepted(self) -> None:
        assert status("x^2/x", "= x") == "valid"
        assert status("(x^2 - 1)/(x - 1)", "= x + 1") == "valid"

    def test_assumption_dependent_identity_abstains(self) -> None:
        assert status("sqrt(x^2)", "= x") == "uncertain"

    def test_log_rule_holds_where_defined(self) -> None:
        assert status("ln(x^2)", "= 2ln(x)") == "valid"

    def test_trig_identity_is_not_flagged_when_sympy_cannot_simplify(self) -> None:
        assert status("sin^2(x) + cos^2(x)", "= 1") in ("valid", "uncertain")
        assert status("sin(2x)", "= 2sin(x)cos(x)") == "valid"

    def test_substitution_is_not_a_mistake(self) -> None:
        assert status("2x + 3", "= 7") == "uncertain"

    def test_new_variable_is_not_a_mistake(self) -> None:
        assert status("x + 1", "= u") == "uncertain"


class TestDifferentiation:
    def test_demo_valid(self) -> None:
        assert status("d/dx (x^3 + 2x)", "= 3x^2 + 2") == "valid"

    def test_demo_invalid_is_power_rule(self) -> None:
        report = run("d/dx (x^3 + 2x)", "= 3x + 2")
        assert report.status == "invalid"
        step = report.first_invalid
        assert step is not None
        assert step.operation_type == "differentiation"
        assert step.error_category == "differentiation"
        assert step.error_type == "power_rule_exponent"
        assert step.expected == "3x^2 + 2"

    def test_not_confused_with_equivalence(self) -> None:
        # Equal as expressions, wrong as a derivative.
        assert status("d/dx(x^2)", "= x^2") == "invalid"

    def test_on_one_line(self) -> None:
        assert status("d/dx(x^2) = 2x", continues="unknown") == "valid"
        assert status("d/dx(x^2) = 2x^2", continues="unknown") == "invalid"

    def test_sum_rule_as_intermediate_step(self) -> None:
        assert status("d/dx(x^3 + 2x)", "= d/dx(x^3) + d/dx(2x)", "= 3x^2 + 2") == "valid"

    def test_constant_multiple(self) -> None:
        assert status("d/dx(5x^4)", "= 20x^3") == "valid"
        step = run("d/dx(5x^4)", "= 4x^3").first_invalid
        assert step is not None
        assert step.error_type == "power_rule_coefficient"

    def test_exponent_not_reduced(self) -> None:
        step = run("d/dx(x^3)", "= 3x^3").first_invalid
        assert step is not None
        assert step.error_type == "power_rule_exponent"

    def test_constant_must_vanish(self) -> None:
        step = run("d/dx(x^2 + 5)", "= 2x + 5").first_invalid
        assert step is not None
        assert step.error_type == "constant_not_dropped"

    def test_missing_term(self) -> None:
        step = run("d/dx(x^3 + 2x)", "= 3x^2").first_invalid
        assert step is not None
        assert step.error_type == "missing_term"

    def test_chain_rule(self) -> None:
        assert status("d/dx((2x + 1)^3)", "= 6(2x + 1)^2") == "valid"
        assert status("d/dx(sin(2x))", "= 2cos(2x)") == "valid"
        for wrong in ("= 3(2x + 1)^2",):
            step = run("d/dx((2x + 1)^3)", wrong).first_invalid
            assert step is not None
            assert step.error_type == "chain_rule_inner_missing"
        step = run("d/dx(sin(2x))", "= cos(2x)").first_invalid
        assert step is not None
        assert step.error_type == "chain_rule_inner_missing"

    def test_product_rule(self) -> None:
        assert status("d/dx(x sin(x))", "= sin(x) + x cos(x)") == "valid"
        step = run("d/dx(x sin(x))", "= cos(x)").first_invalid
        assert step is not None
        assert step.error_type == "product_rule_not_applied"

    def test_function_notation(self) -> None:
        assert status("f(x) = x^3 + 2x", "f'(x) = 3x^2 + 2") == "valid"
        assert status("f(x) = x^3 + 2x", "f'(x) = 3x + 2") == "invalid"

    def test_leibniz_notation(self) -> None:
        assert status("y = x^2", "dy/dx = 2x") == "valid"
        assert status("y = x^2", "dy/dx = x") == "invalid"
        assert status("y = x^2", "y' = 2x") == "valid"

    def test_derivative_without_a_source_abstains(self) -> None:
        assert status("dy/dx = 2x") == "uncertain"

    def test_unmarked_differentiation_is_never_flagged_as_inequivalent(self) -> None:
        assert status("x^3 + 2x", "3x^2 + 2") == "valid"

    def test_evaluating_at_a_point_is_not_a_mistake(self) -> None:
        assert status("d/dx(x^2)", "= 2x", "= 6") == "uncertain"

    def test_higher_order_is_unsupported(self) -> None:
        assert status("d^2y/dx^2 = 2") == "unsupported"


class TestEquations:
    def test_valid_solving_chain(self) -> None:
        assert status("2x + 3 = 7", "2x = 4", "x = 2") == "valid"

    def test_equation_steps_are_not_compared_as_expressions(self) -> None:
        # 2x + 3 and 2x differ as expressions; the step is still correct.
        assert status("2x + 3 = 7", "2x = 4") == "valid"

    def test_sign_error_moving_a_term(self) -> None:
        step = run("2x + 3 = 7", "2x = 10").first_invalid
        assert step is not None
        assert step.error_category == "equation_solving"
        assert step.error_type == "sign_when_moving_term"

    def test_inverse_operation_error(self) -> None:
        step = run("2x = 4", "x = 8").first_invalid
        assert step is not None
        assert step.error_type == "inverse_of_multiplication"

    def test_inverse_of_a_division_is_named_for_what_it_is(self) -> None:
        step = run("x/2 = 3", "x = 3/2").first_invalid
        assert step is not None
        assert step.error_type == "inverse_of_division"

    def test_multiplying_both_sides_is_valid(self) -> None:
        assert status("x/2 = 3", "x = 6") == "valid"
        assert status("2x + 4 = 10", "x + 2 = 5") == "valid"

    def test_quadratic_by_factorising(self) -> None:
        assert status("x^2 - 5x + 6 = 0", "(x - 2)(x - 3) = 0", "x = 2 or x = 3") == "valid"

    def test_wrong_factorisation_changes_solutions(self) -> None:
        assert status("x^2 - 5x + 6 = 0", "(x - 1)(x - 6) = 0") == "invalid"

    def test_plus_minus_solutions(self) -> None:
        assert status("x^2 = 4", "x = ±2") == "valid"

    def test_roots_written_on_separate_lines(self) -> None:
        assert status("x^2 = 4", "x = 2", "x = -2") == "uncertain"

    def test_dividing_by_a_variable_abstains(self) -> None:
        assert status("x^2 = x", "x = 1") == "uncertain"

    def test_clearing_denominators(self) -> None:
        assert status("1/x = 2", "1 = 2x") == "valid"

    def test_excluded_value_is_not_a_solution(self) -> None:
        assert status("x/(x - 2) = 2/(x - 2)", "x = 2") == "uncertain"

    def test_two_variable_rearrangement(self) -> None:
        assert status("y = 2x + 3", "y - 3 = 2x") == "valid"
        assert status("x + y = 3", "x = 3 - y") == "valid"

    def test_unrelated_two_variable_equations_abstain(self) -> None:
        assert status("x + y = 3", "x - y = 1") == "uncertain"

    def test_same_lhs_simplified_on_the_right(self) -> None:
        assert status("y = 2(x + 3)", "y = 2x + 6", operation="expand") == "valid"
        assert status("y = 2(x + 3)", "y = 2x + 3", operation="expand") == "invalid"

    def test_trig_equation_is_unsupported(self) -> None:
        assert status("sin(x) = 0", "x = 1") == "unsupported"

    def test_unknown_continuity_abstains(self) -> None:
        assert status("2x = 4", "x = 8", continues="unknown") == "uncertain"

    def test_separate_problems_are_not_compared(self) -> None:
        assert status("2x = 4", "3x = 100", continues="no") == "valid"


class TestReadingGates:
    def test_illegible_line_never_alerts(self) -> None:
        report = verify_lines(
            [
                LineInput(0, "2(x+3)", continues="no"),
                LineInput(1, "= 2x + 3", legible=False, continues="yes"),
            ]
        )
        assert report.status == "uncertain"
        assert report.steps[-1].reason == "low_legibility"

    def test_illegible_previous_line_never_alerts(self) -> None:
        report = verify_lines(
            [
                LineInput(0, "2(x+3)", legible=False, continues="no"),
                LineInput(1, "= 2x + 3", continues="yes"),
            ]
        )
        assert report.status == "uncertain"

    def test_alternative_reading_prevents_false_alarm(self) -> None:
        # "6" misread as "3" is the classic false positive.
        report = verify_lines(
            [
                LineInput(0, "2(x+3)", continues="no"),
                LineInput(1, "= 2x + 3", continues="yes", alternatives=("= 2x + 6",)),
            ]
        )
        assert report.status == "uncertain"
        assert report.steps[-1].reason == "alternative_reading_is_valid"

    def test_alternative_of_previous_line_prevents_false_alarm(self) -> None:
        report = verify_lines(
            [
                LineInput(0, "2(x+8)", continues="no", alternatives=("2(x+3)",)),
                LineInput(1, "= 2x + 6", continues="yes"),
            ]
        )
        assert report.status == "uncertain"

    def test_wrong_alternatives_keep_the_alert(self) -> None:
        report = verify_lines(
            [
                LineInput(0, "2(x+3)", continues="no"),
                LineInput(1, "= 2x + 3", continues="yes", alternatives=("= 2x + 5",)),
            ]
        )
        assert report.status == "invalid"

    def test_incomplete_line_abstains(self) -> None:
        assert verify_lines([LineInput(0, "2(x+3)"), LineInput(1, "", incomplete=True)]).status == "uncertain"

    def test_unparseable_line_breaks_the_chain(self) -> None:
        report = run("2(x+3)", "= 1/2x", "= 100")
        assert report.status == "uncertain"
        assert report.first_invalid is None

    def test_unsupported_notation_abstains(self) -> None:
        assert status("∫x") == "unsupported"
        assert status("lim(x)") == "unsupported"

    def test_dangling_equals_abstains(self) -> None:
        assert status("= 2x") == "uncertain"


class TestAcknowledgedMistakes:
    def lines(self, *texts: str) -> list[LineInput]:
        return [LineInput(i, text, continues="no" if i == 0 else "yes") for i, text in enumerate(texts)]

    def test_scanning_continues_past_a_known_mistake(self) -> None:
        lines = self.lines("2(x+3)", "= 2x+3", "= 5x+3")
        first = verify_lines(lines).first_invalid
        assert first is not None and first.line == 1
        report = verify_lines(lines, frozenset({first.fingerprint}))
        assert [step.line for step in report.invalid_steps] == [1, 2]

    def test_carrying_a_wrong_value_forward_is_not_a_second_mistake(self) -> None:
        lines = self.lines("2(x+3)", "= 2x+3", "= 3+2x")
        first = verify_lines(lines).first_invalid
        assert first is not None
        report = verify_lines(lines, frozenset({first.fingerprint}))
        assert [step.line for step in report.invalid_steps] == [1]


class TestMistakeIdentity:
    def fingerprint(self, *lines: str) -> str:
        step = run(*lines).first_invalid
        assert step is not None and step.fingerprint is not None
        return step.fingerprint

    def test_stable_across_rereads_and_spacing(self) -> None:
        assert self.fingerprint("2(x+3)", "= 2x+3") == self.fingerprint("2 (x + 3)", "= 2x + 3")

    def test_same_for_reordered_terms(self) -> None:
        assert self.fingerprint("2(x+3)", "= 2x+3") == self.fingerprint("2(x+3)", "= 3+2x")

    def test_differs_for_a_different_mistake(self) -> None:
        assert self.fingerprint("2(x+3)", "= 2x+3") != self.fingerprint("2(x+3)", "= 2x+5")

    def test_only_the_first_error_is_reported(self) -> None:
        report = run("2(x+3)", "= 2x+3", "= 5x+3")
        assert report.first_invalid is not None
        assert report.first_invalid.line == 1
        assert len([s for s in report.steps if s.status == "invalid"]) == 1

    def test_second_error_surfaces_once_the_first_is_fixed(self) -> None:
        report = run("2(x+3)", "= 2x+6", "= 8")
        # "= 8" drops the variable, which reads as substitution.
        assert report.first_invalid is None
        report = run("2(x+3)", "= 2x+6", "= 3x+6")
        assert report.first_invalid is not None
        assert report.first_invalid.line == 2


class TestAdversarialFalseAlerts:
    """Valid maths that used to alert, each found by independent review."""

    @pytest.mark.parametrize(
        "lines",
        [
            # A power binds to its own letter.
            ("xy^2 / y", "= xy"),
            ("y^2x", "= xy^2"),
            ("x*y^2", "= xy^2"),
            ("x^2y + xy^2", "= xy(x + y)"),
            ("xy^-1", "= x/y"),
            ("d/dx(ax^2)", "= 2ax"),
            ("d/dx(xe^x)", "= e^x + xe^x"),
            ("y = xe^x", "dy/dx = e^x + xe^x"),
            ("A = πr²", "dA/dr = 2πr"),
            # Unicode powers.
            ("x⁵ * x", "= x⁶"),
            ("x¹² / x²", "= x¹⁰"),
        ],
    )
    def test_valid(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status == "valid"

    @pytest.mark.parametrize(
        "lines",
        [
            ("sin(30)", "= 0.5"),
            ("cos(60)", "= 1/2"),
            ("tan(45)", "= 1"),
            ("sin(90)", "= 1"),
            ("sin(30) = 0.5",),
        ],
    )
    def test_trig_in_degrees_is_not_a_mistake(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status in ("valid", "uncertain")

    @pytest.mark.parametrize("lines", [("sin(30)", "= 0.7"), ("cos(60)", "= 3")])
    def test_numeric_trig_never_alerts_because_units_are_unknown(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    @pytest.mark.parametrize(
        "lines",
        [
            ("3x = 1", "x = 0.33"),
            ("x^2 = 2", "x = ±1.41"),
            ("x^2 - 5x + 3 = 0", "x = 4.30 or x = 0.70"),
            ("1/3", "= 0.3"),
            ("2/3", "= 0.7"),
            ("2/3", "= 0.6"),
            ("12.5", "= 13"),
            ("pi", "= 22/7"),
            ("sqrt(2)", "= 1.41"),
        ],
    )
    def test_rounding_is_not_a_mistake(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status == "uncertain"

    @pytest.mark.parametrize(
        "lines", [("0.5 + 0.2", "= 0.71"), ("1.5 + 1.5", "= 3.05"), ("1/2", "= 0.75"), ("2.5 * 2", "= 5.1"), ("3x = 1", "x = 0.5")]
    )
    def test_a_wrong_decimal_is_still_a_mistake(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status == "invalid"

    @pytest.mark.parametrize(
        "lines",
        [("(x-3)^2 = 25", "x - 3 = ±5", "x = 8"), ("3 ± 2", "= 5"), ("x - 3 = +-5",)],
    )
    def test_plus_or_minus_is_never_read_as_minus(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    @pytest.mark.parametrize("lines", [("x + 1 = x + 2", "1 = 2"), ("2x + 3 = 2x + 5", "3 = 5")])
    def test_reaching_a_contradiction_is_correct_working(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status == "valid"

    def test_a_contradiction_from_a_solvable_equation_is_a_mistake(self) -> None:
        assert run("2x = 4", "3 = 5").status == "invalid"

    @pytest.mark.parametrize("continues", ["no", "unknown"])
    def test_a_contradiction_stays_correct_whatever_the_continuity_label(self, continues: str) -> None:
        assert run("x + 1 = x + 2", "1 = 2", continues=continues).first_invalid is None

    def test_a_wrong_sum_reaching_the_same_conclusion_is_not_called_correct_or_wrong(self) -> None:
        assert run("x + 1 = x + 2", "1 = 3").status == "uncertain"

    def test_an_identity_reduces_to_a_true_statement(self) -> None:
        assert run("x + 1 = x + 1", "0 = 0").status == "valid"

    @pytest.mark.parametrize("lines", [("x + 1 = x + 1", "1 = 2"), ("x + 1 = x + 2", "0 = 0")])
    def test_true_and_false_cannot_swap(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status == "invalid"

    @pytest.mark.parametrize("continues", ["yes", "no", "unknown"])
    @pytest.mark.parametrize(
        "lines",
        [
            ("3(x + 1) = 12", "3(2 + 1) = 12"),
            ("2x + 1 = 7", "2(2) + 1 = 7"),
            ("x^2 = 4", "1 = 4"),
            ("x^2 = 4", "2^2 = 4"),
        ],
    )
    def test_trying_a_value_is_not_a_claim_that_it_works(self, lines: tuple[str, ...], continues: str) -> None:
        assert run(*lines, continues=continues).first_invalid is None

    def test_a_tried_value_does_not_replace_the_equation_being_solved(self) -> None:
        assert run("2x = 4", "2(1) = 4", "x = 2").first_invalid is None

    def test_a_false_sum_after_a_true_one_is_still_flagged(self) -> None:
        assert run("1 + 1 = 2", "2 + 2 = 5").status == "invalid"

    @pytest.mark.parametrize(
        ("lines", "operation"),
        [
            (("3(x + 1) = 2x + 5",), "expand"),
            (("3x + 2x = x + 8",), "combine_like_terms"),
            (("(x+1)^2 = 2x + 5",), "simplify"),
        ],
    )
    def test_a_wrong_label_cannot_turn_an_equation_into_an_identity(self, lines: tuple[str, ...], operation: str) -> None:
        assert run(*lines, operation=operation, continues="no").first_invalid is None

    @pytest.mark.parametrize(
        "lines", [("2x = 4", "3x = 9"), ("2(x+3)", "3x + 7"), ("2x = 4", "x = 2", "3x = 9"), ("2x", "x^2")]
    )
    def test_a_wrong_continuity_label_cannot_accuse_unrelated_work(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    def test_unreadable_lines_break_the_chain(self) -> None:
        lines = [LineInput(0, "2x = 4", continues="no"), LineInput(1, "3x +", incomplete=True), LineInput(2, "3x = 9", continues="yes")]
        assert verify_lines(lines).first_invalid is None

    def test_definitions_do_not_survive_a_new_problem(self) -> None:
        assert run("y = x^2", "dy/dx = 3x^2", continues="no").first_invalid is None
        lines = [LineInput(0, "y = x^2"), LineInput(1, "y = x^3 +", incomplete=True), LineInput(2, "dy/dx = 3x^2", continues="yes")]
        assert verify_lines(lines).first_invalid is None

    @pytest.mark.parametrize(
        "lines",
        [
            ("sqrt((x-3)^2)", "= x - 3"),
            ("sqrt(x^2 - 6x + 9)", "= x - 3"),
            ("sqrt(x-3)sqrt(x-4)", "= sqrt((x-3)(x-4))"),
        ],
    )
    def test_steps_that_hold_only_on_part_of_the_domain_abstain(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status == "uncertain"

    @pytest.mark.parametrize("lines", [("d/dx(xy)", "= y + x dy/dx"), ("y = ux", "dy/dx = x du/dx + u")])
    def test_implicit_differentiation_is_unsupported(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status == "unsupported"

    @pytest.mark.parametrize("lines", [("(1 + i)^2", "= 2i"), ("x^2 + 1 = 0", "x = +-i")])
    def test_complex_numbers_are_unsupported(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    @pytest.mark.parametrize("lines", [("2e = 4",), ("E = 10",)])
    def test_e_as_a_variable_is_not_called_wrong(self, lines: tuple[str, ...]) -> None:
        assert run(*lines, continues="unknown").first_invalid is None

    @pytest.mark.parametrize("lines", [("2e", "= 4"), ("2ex", "= 4x"), ("2e = 4", "e = 3")])
    def test_a_step_that_only_fails_for_eulers_number_abstains(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    @pytest.mark.parametrize("lines", [("3e + 2e", "= 6e"), ("e^0", "= 2")])
    def test_a_step_wrong_for_any_e_is_still_flagged(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status == "invalid"

    @pytest.mark.parametrize("lines", [("e^0", "= 1"), ("ln(e)", "= 1"), ("2e + 3e", "= 5e")])
    def test_true_statements_about_e_stay_valid(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status == "valid"

    @pytest.mark.parametrize("lines", [("3x", "x^3"), ("x", "2x"), ("x^2", "x^3")])
    def test_two_simplified_expressions_are_not_a_step(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    @pytest.mark.parametrize("lines", [("x^2 * x^3", "x^6"), ("3x + 4x", "8x"), ("x^2 - 4", "(x-2)^2")])
    def test_an_unmarked_simplification_can_still_be_wrong(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status == "invalid"

    def test_question_numbers_are_not_multipliers(self) -> None:
        assert run("(2) x + 3", "= x + 3").first_invalid is None

    def test_words_are_not_maths(self) -> None:
        assert run("or").status == "uncertain"
        assert run("x = or").status == "uncertain"

    def test_undefined_values_do_not_crash(self) -> None:
        assert run("x^(0*ln(0))", "= x").first_invalid is None

    def test_anchor_legibility_blocks_an_alert(self) -> None:
        lines = [LineInput(0, "2x = 5", legible=False, continues="no"), LineInput(1, "x = 2", continues="yes"), LineInput(2, "x = 2", continues="yes")]
        assert verify_lines(lines).first_invalid is None

    def test_anchor_alternative_blocks_an_alert(self) -> None:
        lines = [
            LineInput(0, "2x = 5", alternatives=("2x = 4",), continues="no"),
            LineInput(1, "3x + 1", continues="no"),
            LineInput(2, "x = 2", continues="yes"),
        ]
        assert verify_lines(lines).first_invalid is None


class TestSecondAudit:
    """Each one reproduced by an independent audit, and each a false alarm or a false hint."""

    @pytest.mark.parametrize(
        "lines",
        [
            ("2(x+3)", "= 2x+3", "= 2x+6"),
            ("2x+3 = 7", "2x = 10", "x = 2"),
            ("d/dx(x^2)", "= x", "= 2x"),
            ("(x+1)^2 = x^2+1", "(x+1)^2 = x^2+2x+1"),
            ("2x = 4", "x = 8", "x = 2"),
            ("3x + 5 = 20", "3x = 25", "3x = 15", "x = 5"),
            ("5x - 2 = 8", "5x = 6", "5x = 10", "x = 2"),
        ],
    )
    def test_a_correction_on_the_next_line_is_not_a_mistake(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    def test_a_correction_clears_a_known_mistake(self) -> None:
        wrong = run("2(x+3)", "= 2x+3").first_invalid
        assert wrong is not None
        lines = [LineInput(i, text, continues="yes") for i, text in enumerate(("2(x+3)", "= 2x+3", "= 2x+6"))]
        assert still_present(lines, frozenset({wrong.fingerprint})) == frozenset()

    def test_a_later_mistake_after_a_correction_is_still_found(self) -> None:
        assert run("2(x+3)", "= 2x+3", "= 2x+6", "= 3x+6").status == "invalid"

    def test_a_different_right_answer_does_not_excuse_a_wrong_one(self) -> None:
        assert run("1 + 1 = 3", "2 + 2 = 4").status == "invalid"

    def test_a_correction_after_carrying_on_from_the_wrong_value(self) -> None:
        lines = ("2(x+3)", "= 2x+3", "= 3+2x", "= 2x+6")
        wrong = run(*lines[:2]).first_invalid
        assert wrong is not None
        inputs = [LineInput(i, text, continues="yes") for i, text in enumerate(lines)]
        report = verify_lines(inputs, frozenset({wrong.fingerprint}))
        assert report.first_invalid is None
        assert still_present(inputs, frozenset({wrong.fingerprint})) == frozenset()

    @pytest.mark.parametrize("below", ["3 + 4 = 7", "5 = 5", "2 + 2 = 4 = 4"])
    def test_a_true_line_with_nothing_to_do_with_it_is_no_correction(self, below: str) -> None:
        lines = ("2(x+3)", "= 2x+3", below)
        assert run(*lines).status == "invalid"
        wrong = run(*lines[:2]).first_invalid
        assert wrong is not None
        inputs = [LineInput(i, text, continues="yes") for i, text in enumerate(lines)]
        assert still_present(inputs, frozenset({wrong.fingerprint})) == {wrong.fingerprint}

    def test_an_unlabelled_simplification_below_is_no_correction(self) -> None:
        assert run("2(x+3)", "= 2x+3", "3x+4x = 7x", operation="simplify").status == "invalid"

    def test_a_line_read_unclearly_is_no_correction(self) -> None:
        inputs = [LineInput(0, "2(x+3)"), LineInput(1, "= 2x+3", continues="yes"), LineInput(2, "= 2x+6", continues="yes", legible=False)]
        assert verify_lines(inputs).first_invalid is not None

    @pytest.mark.parametrize(
        "lines", [("2(x+3) = 10", "2x+3 = 10"), ("5(x-2) = 20", "5x-2 = 20"), ("2(x+3) = 10", "2x+6 = 12")]
    )
    def test_the_classic_bracket_slip_in_an_equation(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status == "invalid"

    @pytest.mark.parametrize(
        "lines",
        [
            ("x sin(30) + 1", "= 2x"),
            ("x^2 = 2", "x = sqrt(-2) or x = 1"),
            ("x + 1/0 = 3", "1 + 1 = 3"),
            ("x = 1/0", "x = 3 + 1"),
        ],
    )
    @pytest.mark.parametrize("continues", ["yes", "no", "unknown"])
    @pytest.mark.parametrize("operation", ["unknown", "arithmetic"])
    def test_a_line_that_cannot_be_worked_out_never_raises(self, lines: tuple[str, ...], continues: str, operation: str) -> None:
        run(*lines, continues=continues, operation=operation)
        wrong = frozenset({"x"})
        assert still_present([LineInput(i, text, continues=continues, operation=operation) for i, text in enumerate(lines)], wrong) is not None

    def test_a_tried_value_either_way_round(self) -> None:
        assert run("x + 1 = 4", "4 = 2 + 1").first_invalid is None

    def test_non_ascii_digits_are_not_part_of_a_number(self) -> None:
        assert run("1٣ + 1", "= 14").status != "valid"

    def test_repeating_the_wrong_value_is_not_a_correction(self) -> None:
        assert run("2(x+3)", "= 2x+3", "= 3 + 2x").status == "invalid"

    @pytest.mark.parametrize(
        "lines",
        [
            ("(x-2)(x-3) = 0", "x - 2 = 0", "x - 3 = 0"),
            ("(x+1)^2 = 4", "x + 1 = 2", "x + 1 = -2"),
            ("x^2 = 16", "x = sqrt(16)", "x = -4"),
            ("(x+1)^2 = 4", "x + 1 = 2", "x + 1 = -2", "x = 1", "x = -3"),
        ],
    )
    def test_roots_on_separate_lines(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    def test_a_root_of_nothing_written_is_still_wrong(self) -> None:
        assert run("(x-2)(x-3) = 0", "x - 2 = 0", "x = 9").status == "invalid"

    @pytest.mark.parametrize(
        "lines",
        [
            ("x^1/2", "= sqrt(x)"),
            ("9^1/2", "= 3"),
            ("8^2/3", "= 4"),
            ("x^3/2", "= sqrt(x^3)"),
            ("d/dx(e^2x)", "= 2e^2x"),
            ("d/dx(x^n)", "= nx^n-1"),
        ],
    )
    def test_where_an_exponent_ends_is_unclear(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    @pytest.mark.parametrize(
        "lines", [("x^2/2 + x^2/2", "= x^2"), ("e^x + 1 + e^x", "= 2e^x + 1"), ("x^2y * y", "= x^2y^2"), ("d/dx(e^(2x))", "= 2e^(2x)")]
    )
    def test_clear_exponents_still_work(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status == "valid"

    @pytest.mark.parametrize(
        "lines",
        [
            ("y = u^2", "u = 2x", "dy/dx = 4u"),
            ("x = 2t", "y = 3t", "dy/dx = 3/2"),
            ("A = pi r^2", "dA/dt = 2 pi r"),
            ("y = 2x", "d/dx(y)", "= 2"),
            ("d/dx(y^2)", "= 2y"),
            ("y = x^2", "y = 9", "dy/dx = 6"),
            ("f(x) = 2x", "f(x) = 6", "f'(x) = 2"),
            ("y = 2a", "y' = 0"),
            ("y = x^3", "y' = 3x^2", "y' = 6x"),
        ],
    )
    def test_derivatives_that_depend_on_what_the_letters_mean(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    @pytest.mark.parametrize("lines", [("y = x^3", "dy/dx = 2x^2"), ("d/dx(5)", "= 1"), ("y = 3t", "y' = 4")])
    def test_derivatives_that_are_simply_wrong(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status == "invalid"

    @pytest.mark.parametrize("lines", [("sqrt((x-42)^2)", "= x-42"), ("sqrt((x-100)^2)", "= x-100"), ("sqrt((x^2-2000)^2)", "= x^2-2000")])
    def test_a_root_branch_beyond_the_sample_points(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    def test_a_sign_error_is_not_mistaken_for_a_root_branch(self) -> None:
        assert run("x - 3", "= 3 - x").status == "invalid"

    @pytest.mark.parametrize(
        "lines",
        [
            ("x/3", "= 0.3x"),
            ("3.14*4", "= 12.57"),
            ("0.33 + 0.33", "= 0.67"),
            ("1.41*1.41", "= 2.00"),
            ("x/3 = 0.33", "x = 1"),
        ],
    )
    def test_more_rounding(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    @pytest.mark.parametrize(
        "lines",
        [
            ("50 + 50", "= 200"),
            ("20 + 30", "= 60"),
            ("1/2 * 1/2", "= 1"),
            ("0.1 * 0.1", "= 0.1"),
            ("2.1 + 3.1", "= 6"),
            ("5.1", "= 6"),
            ("0.5x + 0.2x", "= 0.71x"),
            # 2.5 stayed as it was, so nothing suggests the 1.2 next to it is being rounded.
            ("2.5x + 1.2", "= 2.5x + 1"),
            # Small sums are worked out exactly. Rounding them is just getting them wrong.
            ("5 + 4", "= 10"),
            ("5 * 3", "= 10"),
            ("6 * 9", "= 50"),
            ("9 * 9", "= 80"),
            ("23 + 14", "= 30"),
            ("6 * 18", "= 100"),
            # The exact value can be written, so there's no reason to round.
            ("2x = 7", "x = 3"),
            ("2x = 7", "x = 4"),
            ("2x + 1 = 8", "x = 3"),
            ("1.5x + 2x", "= 3x"),
            ("2.5 + 1", "= 3"),
            # Round 3: `=` means equal, and nothing here says otherwise. See docs/ABSTENTION.md.
            ("1/8", "= 0.13"),
            ("1000/3", "= 333"),
            ("1234", "= 1200"),
            ("1000/3", "= 330"),
            ("47*21", "= 990"),
            ("3x = 10", "x = 3"),
            ("x^2 = 10", "x = 3"),
        ],
    )
    def test_not_rounding(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status == "invalid"

    @pytest.mark.parametrize(
        ("lines", "operation"),
        [
            (("2+3", "4+5"), "unknown"),
            (("(x+1)^2", "(x+2)^2"), "unknown"),
            (("x+1 = 3", "x+2 = 7"), "unknown"),
            (("x^2 = 4", "x^2 = 9"), "unknown"),
            (("2x = 4", "3x + 1", "x = 5"), "unknown"),
            (("x + 1", "x + 2"), "differentiate"),
            (("y = 2x", "y = 3x"), "simplify"),
        ],
    )
    def test_a_label_alone_cannot_create_an_alert(self, lines: tuple[str, ...], operation: str) -> None:
        assert run(*lines, operation=operation).first_invalid is None

    @pytest.mark.parametrize(
        ("lines", "operation"),
        [(("x^2", "3x"), "differentiate"), (("y = 2(x+1)", "y = 2x+1"), "simplify"), (("2 + 3", "6"), "unknown")],
    )
    def test_a_label_can_still_choose_the_check(self, lines: tuple[str, ...], operation: str) -> None:
        assert run(*lines, operation=operation).status == "invalid"

    @pytest.mark.parametrize("lines", [("y = 2x", "x = 3", "= 6"), ("A = lw", "l = 3", "= 12")])
    def test_a_continuation_after_a_value_may_belong_to_the_formula(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    @pytest.mark.parametrize("lines", [("3cm * 4cm", "= 12cm^2"), ("1m + 50cm", "= 150cm"), ("2km + 3km", "= 5km")])
    def test_units_are_not_variables(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    @pytest.mark.parametrize(
        ("lines", "not_this"),
        [
            (("(x+1)^2 + 3", "= (x+1)^2 + 4"), "power_of_sum_expanded_termwise"),
            (("(x+1)^2", "= (x+1)(x+2)"), "power_of_sum_expanded_termwise"),
            (("2(x+1) + 3", "= 2(x+1) + 4"), "incorrect_distribution"),
            (("x^2 - 5x + 6 = 0", "(x+2)(x+3) = 0"), "sign_when_moving_term"),
            (("d/dx(x^2)", "= x^2"), "missing_term"),
            (("d/dx(5x)", "= x"), "missing_term"),
            (("x^2 * y^3", "= xy^5"), "exponent_rule"),
            (("x^6 / y^2", "= x^3/y"), "dividing_powers"),
        ],
    )
    def test_a_hint_must_not_blame_what_did_not_happen(self, lines: tuple[str, ...], not_this: str) -> None:
        step = run(*lines).first_invalid
        assert step is not None
        assert step.error_type != not_this

    def test_moving_a_term_is_still_recognised(self) -> None:
        step = run("2x + 3 = 7", "2x = 10").first_invalid
        assert step is not None
        assert step.error_type == "sign_when_moving_term"

    @pytest.mark.parametrize("lines", [("d/dx(e^x)", "= 2e^x"), ("d/dx(e^(2x))", "= e^(2x)"), ("2e^x", "= e^x")])
    def test_tiny_values_do_not_hide_a_mistake(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status == "invalid"

    def test_the_same_equation_written_either_way_is_one_mistake(self) -> None:
        one = run("2x = 4", "x = 9").first_invalid
        two = run("4 = 2x", "x = 9").first_invalid
        three = run("2x + 4 = 8", "x + 2 = 4", "x = 9").first_invalid
        assert one is not None and two is not None and three is not None
        assert one.fingerprint == two.fingerprint == three.fingerprint


class TestFinalReview:
    """Reproduced by a final independent review, on a corpus written from scratch."""

    @pytest.mark.parametrize("continues", ["yes", "no", "unknown"])
    def test_division_sign_with_fractions_is_not_read_in_a_row(self, continues: str) -> None:
        assert run("3/4 ÷ 2/3", "= 3/4 * 3/2", "= 9/8", continues=continues).first_invalid is None

    def test_division_sign_alone_still_works(self) -> None:
        assert run("6 ÷ 3", "= 3").status == "invalid"

    @pytest.mark.parametrize("operation", ["expand", "simplify", "unknown"])
    def test_a_textbook_equation_is_not_an_identity(self, operation: str) -> None:
        lines = ("(x + 1)^2 = x^2 + 5", "x^2 + 2x + 1 = x^2 + 5", "2x = 4", "x = 2")
        assert run(*lines, operation=operation).first_invalid is None

    @pytest.mark.parametrize("operation", ["expand", "simplify"])
    def test_a_false_expansion_on_one_line_is_still_flagged(self, operation: str) -> None:
        assert run("(x + 1)^2 = x^2 + 1", operation=operation).status == "invalid"
        assert run("2(x + 3) = 2x + 3", operation=operation).status == "invalid"

    @pytest.mark.parametrize(
        "lines",
        [
            ("5x = 123", "x = 24.6", "x = 25"),
            ("3x = 3700", "x = 1233.33", "x = 1230"),
            ("F = 23.7 * 52.1", "F = 1230"),
            ("2x = 7", "x = 3.5", "x = 4"),
        ],
    )
    def test_rounding_the_answer_above(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    def test_a_further_value_below_an_answer_has_nothing_to_follow_from(self) -> None:
        # Quack alerted on this once. x = 30 may be a value being tried, or another exercise.
        assert run("5x = 123", "x = 24.6", "x = 30").first_invalid is None
        assert run("x^2 - 5x + 6 = 0", "x = 2 or x = 3", "x = 0", "y = 6").first_invalid is None

    @pytest.mark.parametrize("lines", [("y = e^-2x", "dy/dx = -2e^-2x"), ("e^-2x", "= 2")])
    def test_a_negative_exponent_with_an_unclear_end(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    def test_a_clear_negative_exponent_works(self) -> None:
        assert run("d/dx(e^(-2x))", "= -2e^(-2x)").status == "valid"

    @pytest.mark.parametrize("continues", ["yes", "no", "unknown"])
    def test_equals_as_shorthand_for_the_answer(self, continues: str) -> None:
        assert run("2x = 10", "= 5", continues=continues).first_invalid is None

    def test_a_running_total(self) -> None:
        assert run("5 + 3 = 8 - 2 = 6").first_invalid is None
        assert run("5 + 3 = 9").status == "invalid"

    def test_a_new_exercise_after_a_finished_one(self) -> None:
        assert run("x^2 - 1", "= (x - 1)(x + 1)", "x^2 - 4").first_invalid is None

    @pytest.mark.parametrize(
        "lines",
        [("(x + 1)^2 = x^2 + 1",), ("y = (x + 1)^2", "y = x^2 + 1")],
    )
    @pytest.mark.parametrize("flipped_to", ["solve_equation", "unknown"])
    def test_a_flipped_operation_label_does_not_clear_a_mistake(self, lines: tuple[str, ...], flipped_to: str) -> None:
        wrong = run(*lines, operation="simplify").first_invalid
        assert wrong is not None
        reread = [LineInput(i, text, continues="yes", operation=flipped_to) for i, text in enumerate(lines)]
        assert wrong.fingerprint in still_present(reread, frozenset({wrong.fingerprint}))

    @pytest.mark.parametrize("continues", ["yes", "no", "unknown"])
    @pytest.mark.parametrize("below", ["2x + 6", "= 2x + 6", "2(x + 3) = 2x + 6"])
    def test_every_way_of_writing_a_correction_clears_it(self, continues: str, below: str) -> None:
        lines = ("2(x + 3)", "= 2x + 3", below)
        wrong = run(*lines[:2]).first_invalid
        assert wrong is not None
        inputs = [LineInput(i, text, continues=continues if i == 2 else "yes") for i, text in enumerate(lines)]
        assert verify_lines(inputs).first_invalid is None
        assert still_present(inputs, frozenset({wrong.fingerprint})) == frozenset()

    def test_a_correction_that_is_also_wrong_is_no_correction(self) -> None:
        assert run("2(x + 3)", "= 2x + 3", "2(x + 3) = 2x + 7").status == "invalid"


class TestStandaloneSums:
    """One line on its own, written and corrected, as in the first thing most students try."""

    @staticmethod
    def alone(*lines: str, continues: str = "no") -> list[LineInput]:
        return [LineInput(i, text, continues="no" if i == 0 else continues, operation="arithmetic") for i, text in enumerate(lines)]

    @pytest.mark.parametrize("line", ["2 * 3 = 5", "2×3=5", "3 * 4 = 10", "6 / 2 = 2", "6÷2=2", "7 + 5 = 11"])
    def test_a_wrong_sum_is_a_mistake(self, line: str) -> None:
        step = verify_lines(self.alone(line)).first_invalid
        assert step is not None
        assert step.error_type == "arithmetic_error"

    @pytest.mark.parametrize("line", ["2 * 3 = 6", "2×3=6", "3 * 4 = 12", "6 / 2 = 3", "6÷2=3", "7 + 5 = 12"])
    def test_a_right_sum_is_not(self, line: str) -> None:
        assert verify_lines(self.alone(line)).status == "valid"

    @pytest.mark.parametrize("line", ["2x3 = 5", "3x4=10"])
    def test_a_number_after_x_is_not_guessed_to_be_multiplication(self, line: str) -> None:
        report = verify_lines(self.alone(line))
        assert report.first_invalid is None
        assert report.status == "uncertain"

    def test_x_between_a_number_and_a_bracket_is_still_a_variable(self) -> None:
        assert verify_lines(self.alone("2x + 3 = 5")).first_invalid is None

    @pytest.mark.parametrize("continues", ["yes", "no", "unknown"])
    @pytest.mark.parametrize(
        "wrong, below",
        [
            ("2 * 3 = 5", "2 * 3 = 6"),
            ("2 * 3 = 5", "= 6"),
            ("3 * 4 = 10", "= 12"),
            ("6 / 2 = 2", "= 3"),
            ("2(x + 3) = 2x + 3", "= 2x + 6"),
        ],
    )
    def test_answering_it_again_below_clears_it(self, wrong: str, below: str, continues: str) -> None:
        known = verify_lines(self.alone(wrong)).first_invalid
        assert known is not None
        lines = self.alone(wrong, below, continues=continues)
        assert verify_lines(lines).first_invalid is None
        assert still_present(lines, frozenset({known.fingerprint})) == frozenset()

    @pytest.mark.parametrize("wrong, below", [("2 * 3 = 5", "= 6"), ("3 * 4 = 10", "= 12"), ("2(x + 3) = 2x + 3", "= 2x + 6")])
    def test_the_answer_below_counts_as_read(self, wrong: str, below: str) -> None:
        """Otherwise it looks like an equals sign with nothing before it, which can't show a fix."""

        reasons = [step.reason for step in verify_lines(self.alone(wrong, below, continues="yes")).steps]
        assert "dangling_equals" not in reasons

    @pytest.mark.parametrize("continues", ["yes", "no", "unknown"])
    @pytest.mark.parametrize("below", ["7 + 5 = 12", "3 * 4 = 12", "2 * 3 = 7", "= 7", "= 5"])
    def test_another_line_below_is_not_the_correction(self, below: str, continues: str) -> None:
        known = verify_lines(self.alone("2 * 3 = 5")).first_invalid
        assert known is not None
        lines = self.alone("2 * 3 = 5", below, continues=continues)
        assert still_present(lines, frozenset({known.fingerprint})) == {known.fingerprint}

    @pytest.mark.parametrize(
        "wrong, below",
        [("5 + 3 = 9", "= 5 + 3"), ("5 + 3 = 9", "= 4 + 4"), ("2 * 3 = 5", "= 2 * 3"), ("d/dx(x^2) = 3x", "= d/dx(x^2)")],
    )
    def test_restating_the_question_is_no_answer(self, wrong: str, below: str) -> None:
        known = verify_lines(self.alone(wrong)).first_invalid
        assert known is not None
        lines = self.alone(wrong, below, continues="yes")
        assert still_present(lines, frozenset({known.fingerprint})) == {known.fingerprint}

    @pytest.mark.parametrize(
        "wrong, below",
        [
            ("sqrt(8) = 4", "= 2sqrt(2)"),
            ("sqrt(2)*sqrt(3) = 6", "= sqrt(6)"),
            ("2pi + pi = 2pi", "= 3pi"),
        ],
    )
    def test_a_worked_out_answer_need_not_be_a_plain_number(self, wrong: str, below: str) -> None:
        known = verify_lines(self.alone(wrong)).first_invalid
        assert known is not None
        lines = self.alone(wrong, below, continues="yes")
        assert verify_lines(lines, frozenset({known.fingerprint})).invalid_steps == ()
        assert still_present(lines, frozenset({known.fingerprint})) == frozenset()

    @pytest.mark.parametrize(
        "wrong, below",
        [("5 + 3 = 9", "= 4 + 4"), ("2 * 3 = 5", "= 2 * 3"), ("sqrt(8) = 4", "= sqrt(8)"), ("(x+1)^2 = x^2+1", "= (x+1)(x+1)"), ("2(x+3) = 2x+3", "= 2x+3+3")],
    )
    def test_a_true_line_that_isnt_an_answer_is_neither_accused_nor_a_fix(self, wrong: str, below: str) -> None:
        for operation in ("arithmetic", "expand"):
            lines = [LineInput(0, wrong, continues="no", operation=operation), LineInput(1, below, continues="yes", operation=operation)]
            known = verify_lines(lines[:1]).first_invalid
            assert known is not None
            again = verify_lines(lines, frozenset({known.fingerprint}))
            assert [step.line for step in again.invalid_steps] == [0]
            assert still_present(lines, frozenset({known.fingerprint})) == {known.fingerprint}

    @pytest.mark.parametrize(
        "wrong, below",
        [
            ("1/2 + 1/3 = 2/5", "= 5/6"),
            ("6/4 = 2", "= 3/2"),
            ("sin(pi/6) = 1", "= 1/2"),
            ("5/2 = 3", "= 2.5"),
            ("2 - 5 = 3", "= -3"),
            ("d/dx(x/2) = 2", "= 1/2"),
        ],
    )
    def test_a_fraction_or_decimal_is_an_answer(self, wrong: str, below: str) -> None:
        known = verify_lines(self.alone(wrong)).first_invalid
        assert known is not None
        lines = self.alone(wrong, below, continues="yes")
        assert still_present(lines, frozenset({known.fingerprint})) == frozenset()

    @pytest.mark.parametrize(
        "wrong, below",
        [("2 * 3 = 5", "= sqrt(36)"), ("2^3 = 6", "= sqrt(64)"), ("sqrt(8) = 4", "= sqrt(2*4)"), ("sqrt(8) = 4", "= 8^(1/2)")],
    )
    def test_the_same_number_in_unworked_form_is_no_fix(self, wrong: str, below: str) -> None:
        known = verify_lines(self.alone(wrong)).first_invalid
        assert known is not None
        lines = self.alone(wrong, below, continues="yes")
        assert still_present(lines, frozenset({known.fingerprint})) == {known.fingerprint}

    def test_the_left_side_reordered_is_no_fix(self) -> None:
        lines = [LineInput(0, "x^2+2x+1 = (x+1)^2+1", continues="no", operation="simplify"), LineInput(1, "= 1+2x+x^2", continues="yes", operation="simplify")]
        known = verify_lines(lines[:1]).first_invalid
        assert known is not None
        assert still_present(lines, frozenset({known.fingerprint})) == {known.fingerprint}

    @pytest.mark.parametrize(
        "lines",
        [
            ("2(x+3) = 2x+3", "= 2x+2*3 = 2x+6"),
            ("2(x+3) = 2x+3", "= 2x+6 = 2x+6"),
            ("(x+1)^2 = x^2+1", "= x^2+x+x+1 = x^2+2x+1"),
        ],
    )
    def test_a_true_chain_below_an_unlabelled_line_is_not_accused(self, lines: tuple[str, str]) -> None:
        assert run(*lines).first_invalid is None

    @pytest.mark.parametrize("continues", ["yes", "no", "unknown"])
    def test_a_correction_doesnt_get_compared_with_an_unrelated_sum_above(self, continues: str) -> None:
        lines = [LineInput(0, "3 + 4 = 7", continues="no", operation="arithmetic")]
        for index, text in enumerate(("2 * 3 = 5", "= 5", "= 6"), start=1):
            lines.append(LineInput(index, text, continues=continues, operation="arithmetic"))
        known = verify_lines(lines[:2]).first_invalid
        assert known is not None
        assert verify_lines(lines, frozenset({known.fingerprint})).invalid_steps == ()

    @pytest.mark.parametrize("between", ["= 2 * 3", "= 5 + 1"])
    def test_working_towards_the_answer_below_still_clears_it(self, between: str) -> None:
        known = verify_lines(self.alone("2 * 3 = 5")).first_invalid
        assert known is not None
        lines = self.alone("2 * 3 = 5", between, "= 6", continues="yes")
        assert still_present(lines, frozenset({known.fingerprint})) == frozenset()

    @pytest.mark.parametrize("lines", [("2(x+3) = 2x+3", "= 2x+6"), ("(x+1)^2 = x^2+1", "= x^2+2x+1")])
    def test_the_right_value_below_an_unlabelled_line_is_not_accused(self, lines: tuple[str, str]) -> None:
        report = run(*lines)
        assert report.first_invalid is None
        assert report.status != "valid"

    @pytest.mark.parametrize("line", ["d/dx(x^3 + 2x) = 3x + 2", "d/dx(x^3) = 3x"])
    def test_a_wrong_derivative_on_one_line_is_a_mistake(self, line: str) -> None:
        step = verify_lines(self.alone(line)).first_invalid
        assert step is not None
        assert step.error_category == "differentiation"

    @pytest.mark.parametrize("line", ["d/dx(x^3 + 2x) = 3x^2 + 2", "d/dx(x^3) = 3x^2"])
    def test_a_right_derivative_on_one_line_is_not(self, line: str) -> None:
        assert verify_lines(self.alone(line)).status == "valid"


class TestReleaseReview:
    """Found by an independent review on topics earlier corpora hadn't reached."""

    @pytest.mark.parametrize(
        "lines",
        [
            ("sin(180 - x)", "= sin(x)"),
            ("cos(90 - x)", "= sin(x)"),
            ("sin(x + 90)", "= cos(x)"),
            ("tan(180 + x)", "= tan(x)"),
        ],
    )
    def test_angles_in_degrees_with_a_variable(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    def test_a_trig_slip_in_radians_is_still_flagged(self) -> None:
        assert run("sin(x) + sin(x)", "= 3sin(x)").status == "invalid"

    @pytest.mark.parametrize(
        "lines",
        [
            ("1000 * 1.05^3", "= 1158"),
            ("A = 1000(1.05)^3", "A = 1158"),
            ("5.6 * 7.8 = 44",),
        ],
    )
    def test_rounding_a_long_decimal_to_a_whole_number(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    @pytest.mark.parametrize(
        "lines",
        [
            ("(x + 2)(x + 3) = x^2 + 3x", "+ 2x + 6", "= x^2 + 5x + 6"),
            ("(x + 2)(x + 3)", "= x^2 + 3x", "+ 2x + 6", "= x^2 + 5x + 6"),
            ("127", "+ 45", "= 172"),
            ("10", "- 3", "= 7"),
        ],
    )
    @pytest.mark.parametrize("operation", ["unknown", "expand"])
    def test_a_line_wrapped_onto_the_next(self, lines: tuple[str, ...], operation: str) -> None:
        assert run(*lines, operation=operation).first_invalid is None

    def test_a_line_starting_with_a_negative_is_still_a_step(self) -> None:
        assert run("3 - x", "= -x + 4").status == "invalid"

    @pytest.mark.parametrize("lines", [("pi = 180",), ("pi/6 = 30",), ("2pi = 360",), ("90 = pi/2",)])
    def test_an_angle_with_its_degree_sign_left_off(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    def test_pi_can_still_be_got_wrong(self) -> None:
        assert run("2pi + pi", "= 4pi").status == "invalid"

    @pytest.mark.parametrize("continues", ["yes", "unknown"])
    def test_two_curves_named_y(self, continues: str) -> None:
        lines = ("y = x^2", "y = x + 2", "x^2 = x + 2", "x^2 - x - 2 = 0", "x = 2 or x = -1", "dy/dx = 2x")
        assert run(*lines, continues=continues).first_invalid is None

    def test_one_curve_rewritten_keeps_its_derivative_check(self) -> None:
        assert run("y = (x+1)^2", "y = x^2 + 2x + 1", "dy/dx = 5x").status == "invalid"

    @pytest.mark.parametrize(
        "lines", [("5 * 3 = 15 * 2 = 30",), ("10 / 2 = 5^2 = 25",), ("5 + 3", "= 8 - 2", "= 6")]
    )
    def test_more_running_totals(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    def test_a_chain_with_a_real_slip_is_still_flagged(self) -> None:
        assert run("5 * 3 = 16 * 2 = 32").status == "invalid"
        assert run("x + x", "= 2x + 1").status == "invalid"

    def test_a_list_of_exercises(self) -> None:
        assert run("2(x + 3)", "2x + 6", "2(x - 1)", "2x - 2").first_invalid is None

    @pytest.mark.parametrize("operation", ["expand", "simplify", "factor", "arithmetic"])
    def test_a_posed_equation_with_root_zero(self, operation: str) -> None:
        lines = ("(x + 3)^2 = (x - 3)^2", "x^2 + 6x + 9 = x^2 - 6x + 9", "12x = 0", "x = 0")
        assert run(*lines, operation=operation).first_invalid is None


# Pages with a mistake on them, in the shapes that can raise one.
LABEL_FLIP_PAGES = [
    ("x + 1 = 3", "x = 2", "1/2 + 1/3 = 2/5"),
    ("2x = 4", "x = 2", "2(x + 3) = 2x + 3"),
    ("2(x + 3)", "= 2x + 3"),
    ("2(x + 3)", "2x + 3"),
    ("(x + 1)^2 = x^2 + 1",),
    ("y = (x + 1)^2", "y = x^2 + 1"),
    ("y = x^3", "dy/dx = 2x^2"),
    ("y = x^3", "y' = 2x^2"),
    ("f(x) = x^2", "f'(x) = x"),
    ("x^2", "3x"),
    ("2x + 3 = 7", "2x = 10"),
    ("2x = 4", "x = 8"),
    ("x^2 - 5x + 6 = 0", "(x - 2)(x - 2) = 0"),
    ("x + 1 = x + 1", "1 = 2"),
    ("2x = 4", "3 = 5"),
    ("7 * 8 = 54",),
    ("5 + 3", "= 9"),
    ("x^2 * x^3 = x^6",),
    ("3(x - 1) = 12", "3x - 1 = 12", "3x = 13", "x = 13/3"),
    ("2(x + 3)", "= 2x + 3", "= 3 + 2x"),
    ("x + 3", "x + 4 = 9", "x = 5", "6 * 7 = 40"),
]
LABELS = ("yes", "no", "unknown")
OPERATIONS = ("unknown", "expand", "simplify", "factor", "differentiate", "arithmetic", "solve_equation")


@pytest.mark.parametrize("page", LABEL_FLIP_PAGES)
def test_label_flips_never_make_a_mistake_quack_twice(page: tuple[str, ...]) -> None:
    """The same ink, read over and over with the model's labels changing every time.

    A mistake once raised must stay known until the ink changes. Otherwise the duck goes quack,
    "that's it", quack, on a page nobody has touched.
    """

    rng = random.Random(str(page))
    known: set[str] = set()
    quacks: Counter[str] = Counter()
    for _ in range(60):
        lines = [
            LineInput(i, text, continues=rng.choice(LABELS), operation=rng.choice(OPERATIONS)) for i, text in enumerate(page)
        ]
        report = verify_lines(lines, frozenset(known))
        fresh = next((s for s in report.invalid_steps if s.fingerprint not in known), None)
        known &= still_present(lines, frozenset(known))
        if fresh is not None:
            assert fresh.fingerprint is not None
            quacks[fresh.fingerprint] += 1
            known.add(fresh.fingerprint)
    assert all(count == 1 for count in quacks.values()), quacks


class TestLastReview:
    @pytest.mark.parametrize("lines", [("x = (5 + 1)/2", "x = (5 - 1)/2"), ("y = 2(1) + 1", "y = 2(2) + 1")])
    @pytest.mark.parametrize("operation", ["arithmetic", "simplify"])
    def test_a_table_of_values_or_two_roots(self, lines: tuple[str, ...], operation: str) -> None:
        assert run(*lines, operation=operation).first_invalid is None

    @pytest.mark.parametrize("lines", [("23.7 * 52.1 = 1.23 * 10^3",), ("(3.2 * 10^4)(5.1 * 10^3) = 1.6 * 10^8",)])
    def test_standard_form_is_rounded(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    def test_a_wrong_power_in_standard_form_is_flagged(self) -> None:
        assert run("(2 * 10^3)(3 * 10^4) = 6 * 10^12").status == "invalid"

    def test_a_correction_after_a_full_wrong_solution_clears_it(self) -> None:
        lines = ("3x + 2 = 11", "3x = 13", "x = 13/3", "3x = 9", "x = 3")
        wrong = run(*lines[:2]).first_invalid
        assert wrong is not None
        inputs = [LineInput(i, text, continues="yes") for i, text in enumerate(lines)]
        assert still_present(inputs, frozenset({wrong.fingerprint})) == frozenset()

    @pytest.mark.parametrize("line", ["x^2 * x^3 = x^6", "x^6/x^2 = x^3", "(x^2)^3 = x^5", "x^2 + x^2 = x^4"])
    def test_one_line_power_slips(self, line: str) -> None:
        assert run(line, operation="simplify").status == "invalid"

    def test_a_new_exercise_after_an_answer_can_be_wrong(self) -> None:
        assert run("x = 2", "2(x + 3) = 2x + 3", continues="no", operation="expand").status == "invalid"

    @pytest.mark.parametrize(
        "lines",
        [("a^2 = 100 - 36", "a^2 = 74"), ("x/2 + 1 = 4", "x + 1 = 8")],
    )
    def test_clear_slips_in_equations(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status == "invalid"

    def test_the_right_versions_stay_silent(self) -> None:
        assert run("a^2 = 100 - 36", "a^2 = 64").first_invalid is None
        assert run("x/2 + 1 = 4", "x + 2 = 8").first_invalid is None


class TestClearSlipsAreCaught:
    @pytest.mark.parametrize(
        "lines",
        [
            ("x^4 * x^5", "= x^20"),
            ("x^5 * x^3", "= x^15"),
            ("5x - 2 = 3x + 8", "2x = 6"),
            ("4x + 1 = 2x + 7", "6x = 6"),
        ],
    )
    def test_flagged(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status == "invalid"

    @pytest.mark.parametrize("lines", [("x^4 * x^5", "= x^9"), ("x^30 / x^10", "= x^20"), ("5x - 2 = 3x + 8", "2x = 10")])
    def test_the_right_answers_stay_silent(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None

    @pytest.mark.parametrize("lines", [("2x + 1 = 5", "3y = 9"), ("x + 2 = 5", "3x = 1")])
    def test_a_new_exercise_is_not_collecting_terms(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).first_invalid is None


class TestBoundedWork:
    @pytest.mark.parametrize(
        "lines",
        [
            ("2^x^5", "= 1"),
            ("10^x^5", "= 1"),
            ("3^x^6", "= 1"),
            ("e^x^2 + 3^x^6 = 1",),
            ("sin(2^x^5)", "= 1"),
            ("d/dx(2^x^5)", "= 1"),
            ("2^(99x)", "= 1"),
            ("x^x^x", "= 1"),
            ("(x+y+z+a+b+c+d)^(3*4)", "= x"),
            ("(x+y+z)^(2*6)*(p+q+r)^(2*6)", "= x"),
        ],
    )
    def test_huge_powers_abstain_quickly(self, lines: tuple[str, ...]) -> None:
        started = time.perf_counter()
        assert run(*lines).first_invalid is None
        assert time.perf_counter() - started < 2


class TestMissedMistakes:
    """Wrong work in the supported domain that used to go unflagged."""

    @pytest.mark.parametrize(
        "lines",
        [
            ("y = 3x + 1", "dy/dx = 3x"),
            ("f(x) = 5x", "f'(x) = 5x"),
            ("d/dx(4x)", "= 4x"),
            ("(xy)^2", "= xy^2"),
            ("(x+1)(x-1) - x^2", "= 1"),
            ("3x - 3x", "= x"),
            ("x^2 - 4 = 0", "(x-2)(x-2) = 0"),
            ("x^2 - 5x + 6 = 0", "(x-2)(x-2) = 0"),
            ("2x + 4 = 10", "x + 2 = 6"),
        ],
    )
    def test_flagged(self, lines: tuple[str, ...]) -> None:
        assert run(*lines).status == "invalid"

    def test_identity_includes_the_written_problem(self) -> None:
        one = run("d/dx(x^2 + 1)", "= x").first_invalid
        two = run("d/dx(x^2)", "= x").first_invalid
        assert one is not None and two is not None
        assert one.fingerprint != two.fingerprint
        three = run("3x + 3x", "= 5x").first_invalid
        four = run("4x + 2x", "= 5x").first_invalid
        assert three is not None and four is not None
        assert three.fingerprint != four.fingerprint


class TestDiagnosisMatchesTheActualError:
    """What goes wrong here is a hint telling the student something false."""

    @pytest.mark.parametrize(
        ("lines", "error_type"),
        [
            (("x^2 + 3x^2", "= 5x^2"), "combining_like_terms"),
            (("(x^2)^3", "= x^5"), "power_of_a_power"),
            (("x^6/x^2", "= x^3"), "dividing_powers"),
            (("6x/2", "= 4x"), "incorrect_simplification"),
            (("(x+1)^-1", "= x^-1 + 1"), "incorrect_simplification"),
            (("1/2 + 1/3", "= 2/5"), "arithmetic_error"),
            (("1/x + 1/y", "= 2/(x+y)"), "fraction_addition"),
        ],
    )
    def test_error_type(self, lines: tuple[str, ...], error_type: str) -> None:
        step = run(*lines).first_invalid
        assert step is not None
        assert step.error_type == error_type

    def test_factorisation_expects_a_factorised_form(self) -> None:
        step = run("x^2 - 4", "= (x-2)^2").first_invalid
        assert step is not None
        assert step.expected is not None
        assert "(x - 2)" in step.expected and "(x + 2)" in step.expected


class TestPosedQuestions:
    """Practice only: Quack asked for this, so a wrong answer can't be an exercise in solving for x."""

    def test_a_standalone_algebra_line_is_still_left_alone(self) -> None:
        assert verify_lines([LineInput(0, "3(x + 5) = 3x + 16", continues="no")]).status != "invalid"

    def test_the_same_line_answering_a_posed_question_is_wrong(self) -> None:
        assert verify_lines([LineInput(0, "3(x + 5) = 3x + 16", continues="no", posed=True)]).status == "invalid"

    def test_a_right_answer_to_a_posed_question_is_fine(self) -> None:
        assert verify_lines([LineInput(0, "3(x + 5) = 3x + 15", continues="no", posed=True)]).status == "valid"
