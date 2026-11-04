"""Physical iPad test, round 3: `=` means equal, unless the line says otherwise.

Level A of the round 3 tests: transcribed text straight into the verifier, with no model.
"""

import pytest

from quack.services.math_parser import Precision, Unsupported, parse_statement
from quack.services.math_verifier import (
    LineInput,
    VerificationReport,
    plausible_alternatives,
    reads_the_same,
    still_present,
    verify_lines,
)

LABELS = ["yes", "no", "unknown"]
REPORTED = ["3 × 4 = 10", "3 × 4 = 9", "1 ÷ 3 = 0", "1 ÷ 4 = 0", "1 ÷ 5 = 0", "1 ÷ 2 = 3"]
SEQUENCE = ["3 × 4 = 10", "1 ÷ 2 = 1/2", "3 × 4 = 9", "1 ÷ 3 = 0", "1 ÷ 2 = 3", "1 ÷ 4 = 0", "1 ÷ 5 = 0", "1 × 4 = 4"]
WRONG_IN_SEQUENCE = [0, 2, 3, 4, 5, 6]


def page(*lines: str, continues: str = "unknown", known: frozenset[str] = frozenset()) -> VerificationReport:
    return verify_lines([LineInput(index=i, math=text, continues=continues) for i, text in enumerate(lines)], known)  # type: ignore[arg-type]


def status(*lines: str, continues: str = "unknown") -> str:
    return page(*lines, continues=continues).status


class TestWhatTheIpadTestFound:
    @pytest.mark.parametrize("line", REPORTED)
    @pytest.mark.parametrize("continues", LABELS)
    def test_each_alone_on_a_clean_page(self, line: str, continues: str) -> None:
        step = page(line, continues=continues).first_invalid
        assert step is not None
        assert step.error_type == "arithmetic_error"
        assert step.parsed_operation == ("multiplication" if "×" in line else "division")

    @pytest.mark.parametrize("continues", LABELS)
    def test_the_whole_page_gets_every_mistake_and_only_those(self, continues: str) -> None:
        report = page(*SEQUENCE, continues=continues)
        assert [step.line for step in report.invalid_steps] == WRONG_IN_SEQUENCE
        assert {step.line: step.status for step in report.steps}[1] == "valid"
        assert {step.line: step.status for step in report.steps}[7] == "valid"

    def test_every_mistake_has_its_own_identity(self) -> None:
        fingerprints = [step.fingerprint for step in page(*SEQUENCE).invalid_steps]
        assert len(set(fingerprints)) == len(WRONG_IN_SEQUENCE)

    def test_mistakes_with_the_same_wrong_answer_are_not_one_mistake(self) -> None:
        report = page("1 ÷ 3 = 0", "1 ÷ 4 = 0", "1 ÷ 5 = 0", "1 ÷ 10 = 0")
        assert len({step.fingerprint for step in report.invalid_steps}) == 4

    def test_each_comes_up_as_earlier_ones_become_known(self) -> None:
        known: set[str] = set()
        for count in range(1, len(SEQUENCE) + 1):
            report = page(*SEQUENCE[:count], known=frozenset(known))
            known |= {step.fingerprint or "" for step in report.invalid_steps}
        assert len(known) == len(WRONG_IN_SEQUENCE)
        lines = [LineInput(i, text) for i, text in enumerate(SEQUENCE)]
        assert still_present(lines, frozenset(known)) == frozenset(known)

    def test_the_same_sum_answered_wrongly_again_is_a_second_mistake(self) -> None:
        report = page("3 × 4 = 10", "3 × 4 = 9")
        assert [step.line for step in report.invalid_steps] == [0, 1]

    def test_the_same_sum_answered_rightly_still_corrects_it(self) -> None:
        assert page("3 × 4 = 10", "3 × 4 = 12").first_invalid is None


class TestExactEquality:
    @pytest.mark.parametrize(
        "line",
        ["1 ÷ 3 = 1/3", "1 ÷ 4 = 0.25", "1 ÷ 5 = 0.2", "3 × 4 = 12", "2 × 3 = 6", "0 ÷ 5 = 0", "1 ÷ 10 = 0.1", "1/8 = 0.125", "1 ÷ 2 = 1/2", "2.5 * 4 = 10", "1.5 * 10^3 = 1500"],
    )
    def test_true_statements_stay_silent(self, line: str) -> None:
        assert status(line) == "valid"

    @pytest.mark.parametrize(
        "line",
        [
            "1 ÷ 3 = 0", "1 ÷ 4 = 0", "1 ÷ 5 = 0", "1 ÷ 10 = 0", "3 × 4 = 10", "7 × 3 = 20", "1 ÷ 2 = 0", "2 ÷ 3 = 1",
            # Close to the right value, or rounded from it. Neither makes it equal.
            "22/7 = 3", "10/3 = 3", "1000/3 = 333", "1234 = 1200", "1234 = 1000", "47 = 50", "47 * 21 = 990", "12 = 10",
            "1/4 = 0.3", "1/8 = 0.13", "1/8 = 0.1", "0 = 1/3", "3 = 10/3", "-1/3 = 0", "1/1000 = 0", "1/3 = 0.0", "2pi = 6", "pi = 3", "sqrt(2) = 1", "7 = sqrt(50)",
        ],
    )
    def test_false_statements_are_mistakes(self, line: str) -> None:
        assert status(line) == "invalid"

    @pytest.mark.parametrize(
        "lines",
        [("3x = 10", "x = 3"), ("x^2 = 10", "x = 3"), ("x/3 = 1", "x = 2"), ("4x = 1", "x = 0.3")],
    )
    def test_the_same_goes_for_a_solved_equation(self, lines: tuple[str, ...]) -> None:
        assert status(*lines, continues="yes") == "invalid"

    @pytest.mark.parametrize(
        "lines",
        [
            # No decimal can say these exactly, so a student writing one is plainly approximating.
            ("1/3 = 0.33",), ("2/3 = 0.67",), ("2/3 = 0.66",), ("1/3 = 0.3",), ("2pi = 6.28",), ("sqrt(2) = 1.41",), ("pi = 22/7",),
            ("0.33 = 1/3",), ("3x = 1", "x = 0.33"),
            # Worked out from decimals, which have more figures than anyone writes down.
            ("3.14 * 4 = 12.57",), ("1000 * 1.05^3 = 1157.63",), ("1000 * 1.05^3 = 1158",), ("5.6 * 7.8 = 44",), ("12.5 = 13",),
            # Standard form, and answers rounded on the line below.
            ("23.7 * 52.1 = 1.23 * 10^3",), ("5x = 123", "x = 24.6", "x = 25"),
            # More figures than anyone would write out, though it does terminate.
            ("1/64 = 0.02",), ("1/64 = 0.016",),
        ],
    )
    def test_context_that_really_is_ambiguous_still_abstains(self, lines: tuple[str, ...]) -> None:
        assert status(*lines, continues="yes") == "uncertain"


class TestExplicitApproximation:
    @pytest.mark.parametrize(
        "lines",
        [
            ("1/3 ≈ 0.33",), ("2/3 ≈ 0.67",), ("1/4 ≈ 0.3",), ("pi ≈ 3.14",), ("2pi ≈ 6.28",), ("sqrt(2) ≈ 1.41",), ("1/2 ≈ 0.5",),
            ("1/3", "≈ 0.33"),
            ("1/3 ≈ 0 (nearest integer)",), ("1/3 ≈ 0 to the nearest whole number",), ("12 ≈ 10 (nearest ten)",), ("12 ≈ 10 to the nearest 10",),
            ("1/4 ≈ 0.3 (1dp)",), ("1/4 ≈ 0.3 to 1 decimal place",), ("1/3 ≈ 0.33 (2 d.p.)",), ("1234 ≈ 1200 (2 s.f.)",), ("1234 ≈ 1230 (3sf)",),
            # The line says what convention it means, so `=` isn't claiming they're equal.
            ("1/3 = 0.33 (2dp)",), ("12 = 10 (nearest ten)",), ("1/3 = 0 (nearest integer)",),
        ],
    )
    def test_right_rounding_is_right(self, lines: tuple[str, ...]) -> None:
        assert status(*lines, continues="yes") == "valid"

    @pytest.mark.parametrize(
        "line",
        ["1/3 ≈ 0.34 (2dp)", "1/3 = 0.34 (2dp)", "12 ≈ 20 (nearest ten)", "1/3 ≈ 1 (nearest integer)", "1234 ≈ 1300 (2sf)", "1/4 ≈ 0.4 (1dp)", "3 * 4 ≈ 100", "1/3 ≈ 5"],
    )
    def test_wrong_rounding_is_a_mistake(self, line: str) -> None:
        step = page(line).first_invalid
        assert step is not None
        assert step.error_type == "rounding_error"
        assert step.fingerprint

    @pytest.mark.parametrize(
        "line",
        # With no precision given, a whole number could be any rounding at all.
        ["1/3 ≈ 0", "12 ≈ 10", "1234 ≈ 1200", "22/7 ≈ 3", "1/3 ≈ 0.34", "3 * 4 ≈ 9", "1/4 ≈ 0.2 (1dp)"],
    )
    def test_a_precision_nobody_stated_abstains(self, line: str) -> None:
        assert status(line) == "uncertain"

    @pytest.mark.parametrize("line", ["x ≈ 3", "2x ≈ 6.1", "1/3 ≈ 0.33 ≈ 0.3", "1/3 = 0.333 ≈ 0.33"])
    def test_what_isnt_a_plain_number_stays_unsupported(self, line: str) -> None:
        assert status(line) == "unsupported"
        with pytest.raises(Unsupported):
            parse_statement(line)

    @pytest.mark.parametrize("line", ["x = 3.33 (2dp)", "2x + 1 = 7 (nearest ten)"])
    def test_a_precision_on_a_line_with_letters_is_left_alone(self, line: str) -> None:
        assert status(line) == "uncertain"

    def test_an_approximate_line_doesnt_stop_the_next_being_checked(self) -> None:
        report = page("1/3 ≈ 0.33", "3 × 4 = 10", "1/4 ≈ 0.25")
        assert [step.line for step in report.invalid_steps] == [1]

    @pytest.mark.parametrize(
        ("line", "precision"),
        [
            ("1/3 ≈ 0.33 (2dp)", Precision(places=2)),
            ("1/3 ≈ 0.33 2 d.p.", Precision(places=2)),
            ("1/3 ≈ 0.3 to 1 decimal place", Precision(places=1)),
            ("1234 ≈ 1200 (2 sig figs)", Precision(figures=2)),
            ("1234 ≈ 1200 to 2 significant figures", Precision(figures=2)),
            ("12 ≈ 10 to the nearest ten", Precision(places=-1)),
            ("1250 ≈ 1300 (nearest hundred)", Precision(places=-2)),
            ("0.26 ≈ 0.3 (nearest tenth)", Precision(places=1)),
        ],
    )
    def test_ways_of_saying_how_its_rounded(self, line: str, precision: Precision) -> None:
        assert parse_statement(line).precision == precision

    @pytest.mark.parametrize("line", ["y = 2dp", "3dp", "x = 2sf + 1", "2dp = 4"])
    def test_letters_that_only_look_like_a_precision(self, line: str) -> None:
        assert parse_statement(line).precision is None


class TestWhatTheIndependentReviewFound:
    @pytest.mark.parametrize(
        "lines",
        [
            ("1/3 = 0", "= 0.33"), ("1/3 = 0", "≈ 0.33"), ("1/3 = 0", "= 0.33 (2dp)"), ("sqrt(2) = 1", "= 1.41"), ("22/7 = 3", "= 3.14"),
            ("2/3 = 1", "= 0.67"), ("1/3 = 0", "1/3 = 0.33"), ("1/3 = 0", "1/3 ≈ 0.33"),
        ],
    )
    def test_rounding_the_right_value_puts_the_line_above_right(self, lines: tuple[str, ...]) -> None:
        report = page(*lines)
        assert report.first_invalid is None
        wrong = page(lines[0]).first_invalid
        assert wrong is not None
        known = page(*lines, known=frozenset({wrong.fingerprint or ""}))
        assert [step.line for step in known.invalid_steps if step.line == 1] == []

    @pytest.mark.parametrize(
        "lines",
        [("3x = 10", "≈ 3.3"), ("3x = 10", "= 3.33 (2dp)"), ("x^2 = 2", "= 1.41 (2dp)"), ("l = 3", "≈ 12"), ("r = 10", "≈ 314")],
    )
    def test_a_rounded_shorthand_under_an_equation_isnt_a_claim_about_it(self, lines: tuple[str, ...]) -> None:
        assert page(*lines, continues="yes").first_invalid is None

    @pytest.mark.parametrize(
        "line",
        [
            "e ≈ 7", "e = 7 (1dp)", "pi/6 ≈ 30", "0.5 ≈ sin(30)", "0.5 = sin(30) (1dp)", "0.33 = 1/3 (2dp)", "3.14 = pi (2dp)",
            "1.41 ≈ sqrt(2) (2dp)", "10 ≈ 12 (nearest ten)", "15 ≈ 10 (nearest ten)", "25 ≈ 20 (nearest ten)", "1250 = 1200 (2sf)", "125 = 120 (2sf)",
        ],
    )
    def test_rounding_it_cant_be_sure_of_abstains(self, line: str) -> None:
        assert status(line) == "uncertain"

    def test_a_product_isnt_a_precision(self) -> None:
        assert parse_statement("6 = 3(2dp)").precision is None
        assert page("6 = 3(2dp)").first_invalid is None

    @pytest.mark.parametrize("continues", LABELS)
    @pytest.mark.parametrize("above", ["2(x+3) = 2x+6", "d/dx(x^2) = 2x", "d/dx(x^2 + 2x) = 3x + 2", "(x+1)^2 = x^2 + 2x + 1"])
    def test_a_false_sum_below_what_has_no_value_to_find_is_a_false_sum(self, above: str, continues: str) -> None:
        report = page(above, "3 * 4 = 10", continues=continues)
        assert 1 in [step.line for step in report.invalid_steps]

    @pytest.mark.parametrize("continues", ["yes", "unknown"])
    @pytest.mark.parametrize(
        "lines",
        [
            ("x + 1 = x + 2", "1 - 2 = 0"), ("x + 3 = x + 5", "x + 3 - x = 5"), ("2x + 3y = 12", "x = 1", "y = 1", "2*1 + 3*1 = 12"),
            ("y = 2x + 1", "2*3 + 1 = 8"), ("2x = 6", "x = 4", "2*4 = 6"), ("A = pi*r^2", "pi*5^2 = 79"), ("C = 2*pi*r", "2*pi*5 = 31"),
            # Still a miss, knowingly: a sum below an equation may be a value being tried.
            ("y = 2x", "3 * 4 = 10"), ("x = 2", "3 * 4 = 10"),
        ],
    )
    def test_correct_working_below_an_equation_is_left_alone(self, lines: tuple[str, ...], continues: str) -> None:
        report = page(*lines, continues=continues)
        assert [step for step in report.invalid_steps if step.line == len(lines) - 1] == []

    def test_a_scratch_sum_doesnt_lose_a_correction(self) -> None:
        assert page("2x + 3 = 11", "2x = 9", "11 - 3 = 8", "2x = 8", "x = 4", continues="yes").first_invalid is None

    @pytest.mark.parametrize(
        "lines",
        [
            ("x + 1 = x + 2", "1 = 2"), ("3(x + 1) = 12", "3(2 + 1) = 12"), ("x + y = 3", "1 + 1 = 3"), ("x + 1 = 4", "4 = 2 + 1"),
        ],
    )
    @pytest.mark.parametrize("continues", ["yes", "unknown"])
    def test_what_may_follow_from_the_equation_above_still_abstains(self, lines: tuple[str, ...], continues: str) -> None:
        assert page(*lines, continues=continues).first_invalid is None

    @pytest.mark.parametrize(
        "lines",
        [("x/4", "= 0.2x"), ("3x/4 = 0.8x",), ("7 = sqrt(50)",), ("5 = sqrt(26)",), ("10 = pi^2",), ("2.99 + 3 = 6.05",), ("1.01 + 1 = 2.03",), ("5.01 + 3 = 8.1",), ("0.5 * 0.5 = 0",), ("0.5 * 0.1 = 0",), ("1/2 + 0.25 = 0",)],
    )
    def test_closeness_alone_excuses_nothing(self, lines: tuple[str, ...]) -> None:
        for operation in ("simplify",):
            report = verify_lines([LineInput(i, text, continues="yes", operation=operation) for i, text in enumerate(lines)])
            assert report.first_invalid is not None

    @pytest.mark.parametrize(
        "line",
        ["1.5*2.5 = 4", "1.9*1.9 = 4", "1/0.3 = 3", "1.5*pi = 5", "2.5*sqrt(2) = 4", "1.1 * 1.1 = 1", "pi*5^2 = 79", "sqrt(50) = 7", "12345 ≈ 1.2 * 10^4 (1dp)", "1/3 ≈ 3.3 * 10^-1 (1dp)"],
    )
    def test_rounding_in_decimals_or_irrationals_still_abstains(self, line: str) -> None:
        assert status(line) == "uncertain"

    @pytest.mark.parametrize("lines", [("pi = 3", "= 3.2"), ("sqrt(50) = 8", "= 7.2")])
    def test_close_doesnt_put_a_mistake_right(self, lines: tuple[str, ...]) -> None:
        assert 0 in [step.line for step in page(*lines, continues="yes").invalid_steps]

    @pytest.mark.parametrize("line", ["pi*5^2 ≈ 80 (nearest integer)", "pi ≈ 4 (1 s.f.)"])
    def test_a_stated_precision_on_pi_is_held_to(self, line: str) -> None:
        assert status(line) == "invalid"

    def test_readings_that_differ_on_a_precision_dont_agree(self) -> None:
        assert not reads_the_same("3*4 = 10", "3*4 = 10 (nearest ten)")
        assert not reads_the_same("1/3 = 0.3 (12dp)", "1/3 = 0.3 (1dp)")
        assert reads_the_same("1 ÷ 5 = 0", "1/5 = D")

    @pytest.mark.parametrize("line", ["10^-16 = 0", "1/10^15 = 0", "10^30 + 1 = 10^30"])
    def test_exact_values_are_compared_exactly(self, line: str) -> None:
        assert status(line) == "invalid"

    def test_a_chain_cant_hide_behind_a_note(self) -> None:
        assert page("1/3 = 1/3", "= 0.33 = 7 (2dp)").first_invalid is None
        assert page("1/3 = 1/3", "= 0.33 = 7 (2dp)").status == "uncertain"

    @pytest.mark.parametrize("line", ["1/3 = 0.33 (99999999999dp)", "1/3 ≈ 0.33 (13dp)"])
    def test_an_absurd_precision_is_refused_at_once(self, line: str) -> None:
        assert status(line) == "unsupported"


class TestZeroReadAsALetter:
    @pytest.mark.parametrize(("line", "same_as"), [("1 ÷ 5 = O", "1 ÷ 5 = 0"), ("1/5=o", "1/5=0"), ("3 * 4 = 1O", "3 * 4 = 10"), ("3 * 4 = 1o", "3 * 4 = 10"), ("1 + 1 = O.5", "1 + 1 = 0.5")])
    def test_its_a_zero(self, line: str, same_as: str) -> None:
        first, second = page(line).first_invalid, page(same_as).first_invalid
        assert first is not None and second is not None
        assert first.fingerprint == second.fingerprint

    @pytest.mark.parametrize("line", ["O + 2 = 5", "x + O = 5", "2x + 1 = O", "O = 2", "y = o", "2O = 60", "180 - 2O = 60", "5 = O", "2o = 10"])
    def test_a_letter_among_letters_is_left_alone(self, line: str) -> None:
        assert page(line).first_invalid is None


class TestAlternativesThatArentReadings:
    @pytest.mark.parametrize(
        ("line", "alternative"),
        [
            ("1 ÷ 5 = 0", "1 ÷ 5 = O"), ("1 ÷ 5 = 0", "1 ÷ 5 = D"), ("3 * 4 = 10", "3 * 4 = 1O"), ("3 * 4 = 10", "3*4=1D"),
            ("1 ÷ 4 = 0", "1 ÷ 4 = D"), ("1 ÷ 4 = 0", "1 / 4 = o"),
        ],
    )
    def test_a_zero_read_as_a_letter_excuses_nothing(self, line: str, alternative: str) -> None:
        assert verify_lines([LineInput(0, line, alternatives=(alternative,))]).status == "invalid"

    @pytest.mark.parametrize(
        ("line", "alternative"),
        [("5 + 1 = 3", "s + 1 = 3"), ("2 * 3 = 5", "2x = 5"), ("4 + 1 = 3", "x + 1 = 3"), ("1 ÷ 5 = 0", "1 ÷ s = 0"), ("3 * 4 = 9", "3 * 4 = g"), ("1 ÷ 2 = 3", "1 ÷ z = 3"), ("1/3 = 0", "1/3 ≈ 0")],
    )
    def test_any_other_letter_or_sign_is_a_real_doubt(self, line: str, alternative: str) -> None:
        assert verify_lines([LineInput(0, line, alternatives=(alternative,))]).first_invalid is None

    def test_a_real_doubt_isnt_lost_among_letters_that_arent(self) -> None:
        lines = [LineInput(0, "1 ÷ 5 = 0", alternatives=("1 ÷ 5 = D", "1 ÷ 5 = O", "1 ÷ 5 = o", "1 ÷ 5 = 0.2"))]
        assert verify_lines(lines).first_invalid is None

    @pytest.mark.parametrize(
        ("line", "alternative"),
        [("3 * 4 = 10", "3 * 4 = 12"), ("1 ÷ 5 = 0", "1 ÷ 5 = 0.2"), ("7 * 3 = 20", "7 * 3 = 21"), ("3 * 4 = 13", "3 * 4 = 12")],
    )
    def test_another_digit_still_does(self, line: str, alternative: str) -> None:
        report = verify_lines([LineInput(0, line, alternatives=(alternative,))])
        assert report.first_invalid is None
        assert report.steps[0].reason == "alternative_reading_is_valid"

    def test_a_letter_in_the_line_above_doesnt_excuse_a_sum_below(self) -> None:
        lines = [LineInput(0, "1 ÷ 5 = 0", alternatives=("1 ÷ 5 = D",)), LineInput(1, "3 * 4 = 10")]
        assert [step.line for step in verify_lines(lines).invalid_steps] == [0, 1]

    def test_letters_stay_plausible_where_the_line_has_letters(self) -> None:
        assert plausible_alternatives("2x + 1 = 5", ("2z + 1 = 5",)) == ["2z + 1 = 5"]
        lines = [LineInput(0, "2x + 3", continues="no"), LineInput(1, "= 2x + 5", continues="yes", alternatives=("= 2x + 3",))]
        assert verify_lines(lines).first_invalid is None

    def test_unsure_handwriting_still_abstains(self) -> None:
        assert verify_lines([LineInput(0, "3 * 4 = 10", legible=False)]).first_invalid is None
