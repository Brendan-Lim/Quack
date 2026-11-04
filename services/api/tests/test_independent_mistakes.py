"""Physical iPad test, round 2: a page of lines is context, not one problem."""

import pytest

from quack.services.math_verifier import (
    LineInput,
    StepResult,
    VerificationReport,
    still_present,
    verify_lines,
)


def page(*lines: str, continues: str = "unknown", operation: str = "unknown", known: frozenset[str] = frozenset()) -> VerificationReport:
    return verify_lines(
        [LineInput(index=i, math=text, continues=continues, operation=operation) for i, text in enumerate(lines)],  # type: ignore[arg-type]
        known,
    )


def invalid(report: VerificationReport) -> list[tuple[int, str, str]]:
    return [(step.line, step.previous_step or "", step.current_step or "") for step in report.invalid_steps]


LABELS = ["yes", "no", "unknown"]


@pytest.mark.parametrize("continues", LABELS)
class TestIndependentLines:
    def test_two_wrong_sums_are_two_mistakes(self, continues: str) -> None:
        report = page("3 × 4 = 10", "1 ÷ 2 = 2", continues=continues)
        assert invalid(report) == [(0, "3 * 4", "10"), (1, "1/2", "2")]
        fingerprints = {step.fingerprint for step in report.invalid_steps}
        assert len(fingerprints) == 2

    def test_a_right_division_below_a_wrong_multiplication_is_not_part_of_it(self, continues: str) -> None:
        report = page("3 × 4 = 10", "1 ÷ 6 = 1/6", "1 ÷ 2 = 1/2", continues=continues)
        assert invalid(report) == [(0, "3 * 4", "10")]
        assert [step.status for step in report.steps[1:]] == ["valid", "valid"]

    def test_a_wrong_division_is_a_division(self, continues: str) -> None:
        step = page("2 × 3 = 6", "1 ÷ 2 = 1/3", continues=continues).first_invalid
        assert step is not None
        assert step.line == 1
        assert step.parsed_operation == "division"

    def test_the_operator_on_the_line_names_the_operation(self, continues: str) -> None:
        report = page("3 × 4 = 10", "1 ÷ 2 = 2", "7 + 5 = 11", "9 - 4 = 6", continues=continues)
        assert [step.parsed_operation for step in report.invalid_steps] == [
            "multiplication", "division", "addition", "subtraction",
        ]

    def test_right_sums_stay_silent(self, continues: str) -> None:
        report = page("2 × 3 = 6", "1 ÷ 6 = 1/6", "1 ÷ 2 = 1/2", continues=continues)
        assert report.status == "valid"

    def test_right_algebra_beside_a_wrong_derivative_is_not_accused(self, continues: str) -> None:
        report = page("2(x + 3) = 2x + 6", "d/dx(x^2 + 2x) = 3x + 2", continues=continues)
        assert invalid(report) == [(1, "d/dx(x^2 + 2x)", "3x + 2")]

    def test_a_wrong_derivative_below_wrong_arithmetic_is_its_own_mistake(self, continues: str) -> None:
        report = page("3 × 4 = 10", "d/dx(x^2) = 3x", continues=continues)
        assert invalid(report) == [(0, "3 * 4", "10"), (1, "d/dx(x^2)", "3x")]


class TestWorkingThatBelongsTogether:
    def test_equals_at_the_start_continues(self) -> None:
        assert page("2(x + 3)", "= 2x + 6").status == "valid"

    def test_a_correction_on_the_next_line_is_not_a_second_problem(self) -> None:
        assert page("2 × 3 = 5", "= 6").first_invalid is None

    def test_the_same_sum_written_again_is_a_correction_not_a_new_problem(self) -> None:
        assert page("2 × 3 = 5", "2 × 3 = 6").first_invalid is None

    def test_only_the_first_slip_of_one_piece_of_working_is_reported(self) -> None:
        # Later lines follow from a wrong value, so reporting them too would nag about one slip.
        report = page("2(x + 3)", "= 2x + 3", "= 2x + 4", continues="yes")
        assert invalid(report) == [(1, "2(x + 3)", "2x + 3")]

    def test_later_slips_come_up_once_the_first_is_known(self) -> None:
        first = page("2(x + 3)", "= 2x + 3", "= 2x + 4", continues="yes").first_invalid
        assert first is not None and first.fingerprint is not None
        report = page("2(x + 3)", "= 2x + 3", "= 2x + 4", continues="yes", known=frozenset({first.fingerprint}))
        assert len(report.invalid_steps) == 2

    def test_a_correction_further_down_still_clears_a_new_mistake(self) -> None:
        assert page("2(x + 3)", "= 2x + 3", "= 2x + 6", continues="yes").first_invalid is None

    def test_an_equation_that_loses_its_variable_is_not_a_sum_of_its_own(self) -> None:
        # 1 = 2 finds there are no solutions. Cut off from the line above, it would look like a false sum.
        assert page("x + 1 = x + 2", "1 = 2", continues="yes").first_invalid is None

    def test_a_value_being_tried_is_not_a_sum_of_its_own(self) -> None:
        assert page("3(x + 1) = 12", "3(2 + 1) = 12", continues="yes").first_invalid is None

    def test_a_relationship_that_is_unclear_abstains(self) -> None:
        # Two equations with nothing done between them may be two exercises.
        assert page("2x = 4", "3x = 10", continues="unknown").first_invalid is None


class TestDivisionNotation:
    def test_division_and_a_fraction_on_different_sides_are_clear(self) -> None:
        assert page("1 ÷ 6 = 1/6").status == "valid"
        assert page("1/6 = 1 ÷ 6").status == "valid"
        assert page("1 ÷ 2 = 1/3").status == "invalid"

    def test_both_on_one_side_stays_ambiguous(self) -> None:
        assert page("3/4 ÷ 2/3 = 9/8").status == "uncertain"


class TestStillPresent:
    def test_each_mistake_is_found_again_on_its_own(self) -> None:
        lines = ["3 × 4 = 10", "1 ÷ 2 = 2"]
        report = page(*lines)
        ids = frozenset(step.fingerprint for step in report.invalid_steps if step.fingerprint)
        inputs = [LineInput(index=i, math=text) for i, text in enumerate(lines)]
        assert still_present(inputs, ids) == ids
        fixed = [LineInput(index=0, math="3 × 4 = 12"), LineInput(index=1, math="1 ÷ 2 = 2")]
        assert len(still_present(fixed, ids)) == 1


def derivative(claim: str) -> StepResult:
    step = page(claim).first_invalid
    assert step is not None
    return step


def facts(step: StepResult) -> list[tuple[str, str, str | None, str]]:
    return [(fact.term, fact.derivative, fact.written, fact.problem) for fact in step.term_facts]


class TestDerivativeDiagnosis:
    def test_the_reported_page(self) -> None:
        step = derivative("d/dx(x^2 + 2x) = 3x + 2")
        # A wrong number in front of x. The old diagnosis saw the 2 of the constant term and blamed the exponent.
        assert step.error_type == "power_rule_coefficient"
        assert step.expected == "2x + 2"
        assert facts(step) == [("x^2", "2x", "3x", "coefficient"), ("2x", "2", "2", "right")]

    def test_wrong_exponent(self) -> None:
        step = derivative("d/dx(x^3 + 2x) = 3x + 2")
        assert step.error_type == "power_rule_exponent"
        assert facts(step)[0] == ("x^3", "3x^2", "3x", "exponent")

    def test_exponent_left_alone(self) -> None:
        step = derivative("d/dx(x^3) = 3x^3")
        assert step.error_type == "power_rule_exponent"
        assert facts(step) == [("x^3", "3x^2", "3x^3", "exponent")]

    def test_wrong_sign(self) -> None:
        step = derivative("d/dx(x^2 - 3x) = 2x + 3")
        assert step.error_type == "derivative_sign"
        assert facts(step)[1] == ("-3x", "-3", "3", "sign")

    def test_constant_kept(self) -> None:
        step = derivative("d/dx(x^2 + 5) = 2x + 5")
        assert step.error_type == "constant_not_dropped"
        assert ("5", "0", "5", "constant") in facts(step)

    def test_constant_added_to_a_right_term(self) -> None:
        step = derivative("d/dx(x^2 + 2x + 5) = 2x + 7")
        assert step.error_type == "constant_not_dropped"

    def test_missing_term(self) -> None:
        step = derivative("d/dx(x^3 + 2x) = 3x^2")
        assert step.error_type == "missing_term"
        assert facts(step)[1] == ("2x", "2", None, "missing")

    def test_two_things_wrong_says_so(self) -> None:
        step = derivative("d/dx(x^3 + x^2) = 2x^2 + 3x")
        assert step.error_type == "multiple_derivative_errors"
        assert [fact[3] for fact in facts(step)] == ["coefficient", "coefficient"]

    def test_chain_rule_factor(self) -> None:
        assert derivative("d/dx(sin(2x)) = cos(2x)").error_type == "chain_rule_inner_missing"

    def test_product_rule(self) -> None:
        assert derivative("d/dx(x sin(x)) = cos(x)").error_type == "product_rule_not_applied"

    def test_no_terms_are_claimed_when_the_student_did_not_write_them(self) -> None:
        # Expanding (x+1)^2 to compare terms would discuss an x^2 that isn't on the page.
        step = derivative("d/dx((x + 1)^2) = 2x + 1")
        assert step.error_type == "incorrect_derivative"
        assert step.term_facts == ()

    def test_an_unexplained_mismatch_stays_unclassified(self) -> None:
        step = derivative("d/dx((x + 1)^2) = 2x + 1")
        assert step.error_type == "incorrect_derivative"
        assert step.term_facts == ()

    @pytest.mark.parametrize(
        ("claim", "kind"),
        [
            ("d/dx(sin(x)) = sin(x)", "derivative_of_sine"),
            ("d/dx(3sin(x)) = 3sin(x)", "derivative_of_sine"),
            ("d/dx(cos(x)) = sin(x)", "derivative_of_cosine"),
            ("d/dx(tan(x)) = sin(x)", "derivative_of_tangent"),
            ("d/dx(ln(x)) = x", "derivative_of_logarithm"),
        ],
    )
    def test_a_standard_function_is_named_by_its_rule(self, claim: str, kind: str) -> None:
        assert derivative(claim).error_type == kind

    def test_diagnosis_never_changes_the_verdict(self) -> None:
        assert page("d/dx(x^2 + 2x) = 2x + 2").status == "valid"
