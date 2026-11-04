"""A hint or explanation says what the mathematics shows, and nothing it doesn't."""

import pytest

from quack.services.hint_engine import (
    CLAIMS_WHAT_THE_STUDENT_DID,
    find_problems,
    has_prepared_hints,
    prepared_explanation,
    template_text,
)
from quack.services.math_verifier import LineInput, verify_lines
from quack.services.mistake_manager import TrackedMistake


def tracked(*lines: str) -> TrackedMistake:
    report = verify_lines([LineInput(index=i, math=text) for i, text in enumerate(lines)])
    step = report.invalid_steps[-1]
    return TrackedMistake(
        id=f"m_{step.fingerprint}",
        category=step.error_category or "",
        error_type=step.error_type or "",
        operation_type=step.operation_type,
        previous_step=step.previous_step or "",
        current_step=step.current_step or "",
        expected=step.expected or "",
        evidence=step.evidence or "",
        parsed_operation=step.parsed_operation or "",
        term_facts=step.term_facts,
        extra_terms=step.extra_terms,
    )


class TestThePowerRuleExplanation:
    mistake = tracked("d/dx(x^2 + 2x) = 3x + 2")

    def test_it_is_exactly_what_the_mathematics_shows(self) -> None:
        assert template_text(3, self.mistake) == (
            "The derivative of x^2 is 2x, not 3x. The derivative of 2x is 2, so the correct result is 2x + 2."
        )

    def test_it_does_not_tell_a_story_about_the_student(self) -> None:
        text = template_text(3, self.mistake)
        assert "multipl" not in text
        assert "instead" not in text
        assert "you " not in text.lower()

    def test_every_derivative_it_states_is_true(self) -> None:
        import sympy as sp

        x = sp.Symbol("x")
        for fact in self.mistake.term_facts:
            term = sp.sympify(fact.term.replace("^", "**").replace("2x", "2*x"))
            assert sp.diff(term, x) == sp.sympify(fact.derivative.replace("^", "**").replace("2x", "2*x"))

    def test_first_hints_point_at_the_term_without_the_answer(self) -> None:
        first, second = template_text(1, self.mistake), template_text(2, self.mistake)
        assert "x^2" in first
        assert "x^2" in second and "3x" in second
        assert "2x + 2" not in first + second
        assert not find_problems(1, first, self.mistake)
        assert not find_problems(2, second, self.mistake)
        assert has_prepared_hints(self.mistake)


@pytest.mark.parametrize(
    ("claim", "says", "never"),
    [
        ("d/dx(x^3 + 2x) = 3x + 2", ["The derivative of x^3 is 3x^2, not 3x.", "the correct result is 3x^2 + 2"], ["coefficient"]),
        ("d/dx(x^3) = 3x^3", ["The derivative of x^3 is 3x^2, not 3x^3."], []),
        ("d/dx(x^2 - 3x) = 2x + 3", ["The derivative of -3x is -3, not 3."], []),
        ("d/dx(x^2 + 5) = 2x + 5", ["5 is a constant, so its derivative is 0."], []),
        ("d/dx(x^3 + 2x) = 3x^2", ["The derivative of 2x is 2, and that term isn't in your answer."], []),
        (
            "d/dx(x^3 + x^2) = 2x^2 + 3x",
            ["The derivative of x^3 is 3x^2, not 2x^2.", "The derivative of x^2 is 2x, not 3x.", "the correct result is 3x^2 + 2x"],
            [],
        ),
    ],
)
def test_each_kind_of_derivative_mistake_says_what_differs(claim: str, says: list[str], never: list[str]) -> None:
    text = template_text(3, tracked(claim))
    for piece in says:
        assert piece in text
    for piece in never:
        assert piece not in text


@pytest.mark.parametrize(
    "claim",
    ["d/dx((x + 1)^2) = 2x + 1"],
)
def test_an_unexplained_mismatch_invents_no_reason(claim: str) -> None:
    mistake = tracked(claim)
    assert prepared_explanation(mistake) is None
    level_three = template_text(3, mistake)
    assert level_three == f"The derivative is {mistake.expected}, not {mistake.current_step}."


def test_a_standard_function_is_explained_by_its_rule_and_nothing_about_the_student() -> None:
    text = template_text(3, tracked("d/dx(sin(x)) = sin(x)"))
    assert "The derivative of sin(x) is cos(x), not sin(x)." in text
    assert CLAIMS_WHAT_THE_STUDENT_DID.search(text) is None


class TestArithmeticHintsFollowTheOperator:
    def test_a_wrong_division_gets_a_division_hint(self) -> None:
        mistake = tracked("2 × 3 = 6", "1 ÷ 2 = 2")
        assert template_text(1, mistake) == "Check that division again."
        assert template_text(2, mistake) == "Try multiplying your answer by 2. Do you get 1?"

    def test_a_wrong_multiplication_gets_a_multiplication_hint(self) -> None:
        mistake = tracked("3 × 4 = 10", "1 ÷ 6 = 1/6")
        assert template_text(1, mistake) == "Check that multiplication again."

    def test_a_fraction_as_the_answer_is_still_a_division_hint(self) -> None:
        mistake = tracked("1 ÷ 2 = 1/3")
        assert has_prepared_hints(mistake)
        assert template_text(1, mistake) == "Check that division again."
        assert "1/2" not in template_text(2, mistake)

    @pytest.mark.parametrize(
        "text",
        ["Check your multiplication.", "Look at your times tables here.", "Try the addition again."],
    )
    def test_a_generated_hint_about_another_operation_is_refused(self, text: str) -> None:
        mistake = tracked("1 ÷ 2 = 2")
        assert any("different operation" in problem for problem in find_problems(1, text, mistake))

    def test_checking_a_division_by_multiplying_is_allowed(self) -> None:
        mistake = tracked("1 ÷ 2 = 2")
        assert not find_problems(2, "Try multiplying your answer by 2.", mistake)


@pytest.mark.parametrize(
    "text",
    [
        "You multiplied x^2 by 3 instead of 2.",
        "You forgot to lower the exponent.",
        "It looks like you added the exponents.",
        "You've dropped a term.",
        "You probably used the wrong rule.",
    ],
)
def test_a_generated_story_about_the_student_is_refused(text: str) -> None:
    mistake = tracked("d/dx(x^2 + 2x) = 3x + 2")
    assert any("what the student did" in problem for problem in find_problems(3, text + " 2x + 2", mistake))


@pytest.mark.parametrize(
    "text",
    [
        "Check whether you multiplied both terms.",
        "Did you multiply both terms?",
        "What did you get when you added them?",
        "Look at what you wrote for x^2.",
    ],
)
def test_asking_is_not_claiming(text: str) -> None:
    assert not CLAIMS_WHAT_THE_STUDENT_DID.search(text)


def test_no_fixed_wording_states_a_mechanism() -> None:
    from quack.services.hint_engine import _DEFAULT_TEMPLATE, _TEMPLATES, _TERM_HINTS

    for template in [*_TEMPLATES.values(), _DEFAULT_TEMPLATE, *_TERM_HINTS.values()]:
        for wording in template:
            assert not CLAIMS_WHAT_THE_STUDENT_DID.search(wording), wording
