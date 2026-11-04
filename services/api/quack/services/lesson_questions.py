"""Question templates for guided lessons, each with its skill, tier and the wrong answers it predicts.

This extends practice rather than replacing it: a lesson question is a `practice.Question`, stored as a
practice problem, checked by the verifier before it's shown and answered through the same pipeline.
What's added is what a lesson needs to choose the next question: which skill a template exercises, how
hard it is, which misconceptions it can show up, and what those misconceptions would make the student
write. Answers are worked out in integers here, and a sample is checked independently with SymPy in
tests. Nothing comes from a model.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from math import gcd
from typing import Any, Literal

from quack.services.practice import EQUATION, Question, _pretty
from quack.services.verifier_pool import VerificationFailed, shared_pool

Tier = Literal["learn", "apply", "challenge", "demonstrate"]
TIERS: tuple[Tier, ...] = ("learn", "apply", "challenge", "demonstrate")
# How a question is checked. `equation` and `factorise` say what the student is doing, not just the value.
IDENTITY, ARITHMETIC, FACTORISE = "identity", "arithmetic", "factorise"
# A fraction in its lowest terms, or a decimal: the value alone doesn't finish these. And calculus, as practice has it.
LOWEST_TERMS, DECIMAL, DIFFERENTIATION, INTEGRATION = "lowest_terms", "decimal", "differentiation", "integration"
METHODS = (IDENTITY, ARITHMETIC, EQUATION, FACTORISE, LOWEST_TERMS, DECIMAL, DIFFERENTIATION, INTEGRATION)
# What a finished answer must look like, beyond its value, for the methods that ask for a form.
FORM_OF_METHOD = {FACTORISE: "factorised", LOWEST_TERMS: "lowest_terms", DECIMAL: "decimal"}
# The practice topic each skill's questions are filed under, for events and recaps.
TOPIC_OF_SKILL = {
    "algebra.variables": "combining_like_terms",
    "algebra.substitution": "addition_subtraction",
    "algebra.like_terms": "combining_like_terms",
    "algebra.simplifying": "combining_like_terms",
    "algebra.brackets": "expanding_brackets",
    "algebra.linear_equations": "solving_equations",
}
MAX_TRIES = 8
LETTERS = ("x", "y", "a", "n", "m", "p")
PAIRS = (("a", "b"), ("x", "y"), ("m", "n"), ("p", "q"))


@dataclass(frozen=True)
class LessonQuestion:
    question: Question
    template: str
    skill: str
    tier: Tier
    # What the student is asked to do, written before the maths: "Simplify", "Solve".
    instruction: str
    params: dict[str, Any]
    # Wrong answers a known misconception would give, as (misconception, value), in the parser's notation.
    probes: tuple[tuple[str, str], ...] = ()
    # What the question says before it asks anything: the values it gives its letters, as (letter, value)
    # in the parser's notation, and the expression they go into. `x = 3` written under it restates it.
    values: tuple[tuple[str, str], ...] = ()
    expression: str = ""

    @property
    def method(self) -> str:
        return self.question.method


@dataclass(frozen=True)
class Template:
    id: str
    skill: str
    tier: Tier
    method: str
    # The misconceptions this question can bring out, so reinforcement can choose it on purpose.
    targets: tuple[str, ...]
    # What the parameters may be, in words, for the docs and for anyone checking the generator.
    constraints: str
    # Nudges for a student who hasn't started. None of them states the answer.
    hints: tuple[str, ...]
    build: Callable[[random.Random, dict[str, Any] | None], LessonQuestion] = field(repr=False)


def _term(coefficient: int, letters: str) -> str:
    """One term, written as a student would: 1x is x, and -1y is -y."""

    if not letters:
        return str(coefficient)
    if coefficient == 1:
        return letters
    if coefficient == -1:
        return f"-{letters}"
    return f"{coefficient}{letters}"


def _sum(terms: list[tuple[int, str]]) -> str:
    """Terms in the order given, skipping zeros: [(3, "x"), (-2, "")] is 3x - 2."""

    text = ""
    for coefficient, letters in terms:
        if coefficient == 0:
            continue
        written = _term(abs(coefficient), letters)
        if not text:
            text = f"-{written}" if coefficient < 0 else written
        else:
            text += f" - {written}" if coefficient < 0 else f" + {written}"
    return text or "0"


def _shown(math: str) -> str:
    shown = _pretty(math).replace(" - ", " − ").replace("(-", "(−")
    return f"−{shown[1:]}" if shown.startswith("-") else shown


def _lesson(
    template: str, skill: str, tier: Tier, instruction: str, math: str, expected: str, method: str,
    params: dict[str, Any], probes: list[tuple[str, str]], shown: str | None = None,
    values: tuple[tuple[str, str], ...] = (), expression: str = "",
) -> LessonQuestion:
    topic = TOPIC_OF_SKILL[skill]
    display = shown or _shown(math)
    # A predicted wrong answer that happens to be right, for these numbers, predicts nothing. Nor does one
    # two misconceptions would both give: 58 - 9 and 40 + 9 are both 49, so 49 can't say which.
    probes = [(misconception, wrong) for misconception, wrong in probes if not _same_value(wrong, expected)]
    probes = [
        (misconception, wrong) for misconception, wrong in probes
        if not any(other != misconception and _same_value(wrong, value) for other, value in probes)
    ]
    prompt = f"{instruction} {display}" if instruction else display
    return LessonQuestion(
        Question(topic, tier, math, prompt, expected, method), template, skill, tier, instruction, params, tuple(probes),
        values, expression,
    )


def _same_value(one: str, other: str) -> bool:
    """For Quack's own strings, never a model's: these are built here from integers."""

    from quack.services.math_parser import parse_expression, to_sympy

    return to_sympy(parse_expression(one)).expand() == to_sympy(parse_expression(other)).expand()


def _pick(rng: random.Random, fixed: dict[str, Any] | None, name: str, choose: Callable[[], Any]) -> Any:
    return fixed[name] if fixed and name in fixed else choose()


# Meet the variables


def _repeated_letter(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    count = _pick(rng, fixed, "count", lambda: rng.randint(2, 6))
    letter = _pick(rng, fixed, "letter", lambda: rng.choice(LETTERS))
    math = " + ".join([letter] * count)
    probes = [("variable_dropped", str(count)), ("letters_multiplied", f"{letter}^{count}")]
    return _lesson(
        "variables.repeated_letter", "algebra.variables", "learn", "Simplify", math, _term(count, letter), IDENTITY,
        {"count": count, "letter": letter}, probes,
    )


def _one_and_more(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    more = _pick(rng, fixed, "more", lambda: rng.randint(2, 8))
    letter = _pick(rng, fixed, "letter", lambda: rng.choice(LETTERS))
    math = f"{letter} + {more}{letter}"
    probes = [("variable_dropped", str(more + 1)), ("letters_multiplied", f"{more}{letter}^2")]
    return _lesson(
        "variables.one_and_more", "algebra.variables", "learn", "Simplify", math, _term(more + 1, letter), IDENTITY,
        {"more": more, "letter": letter}, probes,
    )


def _take_away(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    count = _pick(rng, fixed, "count", lambda: rng.randint(3, 6))
    letter = _pick(rng, fixed, "letter", lambda: rng.choice(LETTERS))
    math = " + ".join([letter] * count) + f" - {letter}"
    probes = [("variable_dropped", str(count - 1)), ("sign_error", _term(count + 1, letter))]
    return _lesson(
        "variables.take_away", "algebra.variables", "apply", "Simplify", math, _term(count - 1, letter), IDENTITY,
        {"count": count, "letter": letter}, probes,
    )


def _letters_and_numbers(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    count = _pick(rng, fixed, "count", lambda: rng.randint(2, 4))
    constant = _pick(rng, fixed, "constant", lambda: rng.randint(1, 9))
    letter = _pick(rng, fixed, "letter", lambda: rng.choice(LETTERS))
    math = " + ".join([letter] * count) + f" + {constant}"
    probes = [("unlike_terms_combined", _term(count + constant, letter)), ("variable_dropped", str(count + constant))]
    return _lesson(
        "variables.letters_and_numbers", "algebra.variables", "apply", "Simplify", math,
        _sum([(count, letter), (constant, "")]), IDENTITY, {"count": count, "constant": constant, "letter": letter}, probes,
    )


def _two_letters_repeated(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    first = _pick(rng, fixed, "first", lambda: rng.randint(2, 4))
    second = _pick(rng, fixed, "second", lambda: rng.choice([n for n in range(2, 5) if n != first]))
    one, two = _pick(rng, fixed, "letters", lambda: rng.choice(PAIRS))
    math = " + ".join([one] * first + [two] * second)
    probes = [("unlike_terms_combined", f"{first + second}{one}{two}")]
    return _lesson(
        "variables.two_letters", "algebra.variables", "challenge", "Simplify", math,
        _sum([(first, one), (second, two)]), IDENTITY, {"first": first, "second": second, "letters": (one, two)}, probes,
    )


def _mixed_coefficients(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 6))
    constant = _pick(rng, fixed, "constant", lambda: rng.randint(1, 9))
    letter = _pick(rng, fixed, "letter", lambda: rng.choice(LETTERS))
    math = f"{a}{letter} + {letter} + {constant} + {letter}"
    probes = [("unlike_terms_combined", _term(a + 2 + constant, letter)), ("variable_dropped", str(a + 2 + constant))]
    return _lesson(
        "variables.mixed_coefficients", "algebra.variables", "challenge", "Simplify", math,
        _sum([(a + 2, letter), (constant, "")]), IDENTITY, {"a": a, "constant": constant, "letter": letter}, probes,
    )


# Substitute and evaluate. The question is the expression with its value put in, in brackets, which is
# what the student is asked to work out. Shown with the letter, as they'd see it in a book.


def _substitution(
    template: str, tier: Tier, letter_form: str, given: dict[str, int], math: str, value: int, params: dict[str, Any],
    probes: list[tuple[str, str]],
) -> LessonQuestion:
    stated = " and ".join(f"{letter} = {_shown(str(number))}" for letter, number in given.items())
    shown = f"If {stated}, work out {_shown(letter_form)}"
    values = tuple((letter, str(number)) for letter, number in given.items())
    return _lesson(
        template, "algebra.substitution", tier, "", math, str(value), ARITHMETIC, params, probes, shown, values, letter_form
    )


def _substitute_positive(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 9))
    value = _pick(rng, fixed, "value", lambda: rng.randint(2, 9))
    b = _pick(rng, fixed, "b", lambda: rng.randint(1, 9))
    # 3x with x = 4 written as 34: the digits side by side.
    probes = [("substitution_as_digits", str(int(f"{a}{value}") + b))]
    return _substitution(
        "substitution.positive", "learn", f"{a}x + {b}", {"x": value}, f"{a}({value}) + {b}", a * value + b,
        {"a": a, "value": value, "b": b}, probes,
    )


def _substitute_subtract(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 9))
    value = _pick(rng, fixed, "value", lambda: rng.randint(2, 9))
    b = _pick(rng, fixed, "b", lambda: rng.randint(1, min(9, a * value - 1)))
    probes = [("substitution_as_digits", str(int(f"{a}{value}") - b)), ("sign_error", str(a * value + b))]
    return _substitution(
        "substitution.subtract", "apply", f"{a}x - {b}", {"x": value}, f"{a}({value}) - {b}", a * value - b,
        {"a": a, "value": value, "b": b}, probes,
    )


def _substitute_negative(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 6))
    value = _pick(rng, fixed, "value", lambda: rng.randint(2, 6))
    b = _pick(rng, fixed, "b", lambda: rng.randint(1, 9))
    probes = [("sign_error", str(a * value + b))]
    return _substitution(
        "substitution.negative", "apply", f"{a}x + {b}", {"x": -value}, f"{a}(-{value}) + {b}", b - a * value,
        {"a": a, "value": value, "b": b}, probes,
    )


def _substitute_square(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    value = _pick(rng, fixed, "value", lambda: rng.randint(2, 5))
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 6))
    # Squaring a negative to a negative is the slip this one is about.
    probes = [("sign_error", str(-(value**2) - a * value)), ("sign_error", str(value**2 + a * value))]
    return _substitution(
        "substitution.square_negative", "challenge", f"x^2 + {a}x", {"x": -value}, f"(-{value})^2 + {a}(-{value})",
        value**2 - a * value, {"value": value, "a": a}, probes,
    )


def _substitute_two(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 6))
    p = _pick(rng, fixed, "p", lambda: rng.randint(2, 7))
    q = _pick(rng, fixed, "q", lambda: rng.randint(2, 7))
    probes = [("sign_error", str(a * p - q))]
    return _substitution(
        "substitution.two_values", "challenge", f"{a}x - y", {"x": p, "y": -q}, f"{a}({p}) - (-{q})", a * p + q,
        {"a": a, "p": p, "q": q}, probes,
    )


def _substitute_square_expression(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 4))
    value = _pick(rng, fixed, "value", lambda: rng.randint(2, 4))
    probes = [("sign_error", str(-a * value**2 + value)), ("sign_error", str(a * value**2 - value))]
    question = _substitution(
        "substitution.square_expression", "demonstrate", f"{a}x^2 - x", {"x": -value},
        f"{a}(-{value})^2 - (-{value})", a * value**2 + value, {"a": a, "value": value}, probes,
    )
    return question


# Combine like terms


def _add_two(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 9))
    b = _pick(rng, fixed, "b", lambda: rng.randint(2, 9))
    letter = _pick(rng, fixed, "letter", lambda: rng.choice(LETTERS))
    probes = [("variable_dropped", str(a + b)), ("letters_multiplied", f"{a + b}{letter}^2")]
    return _lesson(
        "like_terms.add_two", "algebra.like_terms", "learn", "Simplify", f"{a}{letter} + {b}{letter}",
        _term(a + b, letter), IDENTITY, {"a": a, "b": b, "letter": letter}, probes,
    )


def _subtract_two(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    b = _pick(rng, fixed, "b", lambda: rng.randint(2, 7))
    a = _pick(rng, fixed, "a", lambda: rng.randint(b + 2, 12))
    letter = _pick(rng, fixed, "letter", lambda: rng.choice(LETTERS))
    probes = [("variable_dropped", str(a - b)), ("sign_error", _term(a + b, letter))]
    return _lesson(
        "like_terms.subtract_two", "algebra.like_terms", "learn", "Simplify", f"{a}{letter} - {b}{letter}",
        _term(a - b, letter), IDENTITY, {"a": a, "b": b, "letter": letter}, probes,
    )


def _three_terms(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    b = _pick(rng, fixed, "b", lambda: rng.randint(2, 6))
    a = _pick(rng, fixed, "a", lambda: rng.randint(b + 1, 10))
    c = _pick(rng, fixed, "c", lambda: rng.randint(2, 6))
    letter = _pick(rng, fixed, "letter", lambda: rng.choice(LETTERS))
    total = a - b + c
    probes = [("variable_dropped", str(total)), ("sign_error", _term(a - b - c, letter)), ("sign_error", _term(a + b + c, letter))]
    return _lesson(
        "like_terms.three_terms", "algebra.like_terms", "apply", "Simplify", f"{a}{letter} - {b}{letter} + {c}{letter}",
        _term(total, letter), IDENTITY, {"a": a, "b": b, "c": c, "letter": letter}, probes,
    )


def _hidden_one(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 8))
    b = _pick(rng, fixed, "b", lambda: rng.randint(2, 8))
    letter = _pick(rng, fixed, "letter", lambda: rng.choice(LETTERS))
    probes = [("variable_dropped", str(a + b - 1)), ("sign_error", _term(a + b + 1, letter))]
    return _lesson(
        "like_terms.hidden_one", "algebra.like_terms", "apply", "Simplify", f"{a}{letter} + {b}{letter} - {letter}",
        _term(a + b - 1, letter), IDENTITY, {"a": a, "b": b, "letter": letter}, probes,
    )


def _two_letters(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    c = _pick(rng, fixed, "c", lambda: rng.randint(1, 4))
    a = _pick(rng, fixed, "a", lambda: rng.randint(c + 1, 9))
    b = _pick(rng, fixed, "b", lambda: rng.randint(2, 7))
    d = _pick(rng, fixed, "d", lambda: rng.randint(1, 5))
    one, two = _pick(rng, fixed, "letters", lambda: rng.choice(PAIRS))
    math = _sum([(a, one), (b, two), (-c, one), (d, two)])
    probes = [("variable_dropped", str(a - c + b + d)), ("sign_error", _sum([(a + c, one), (b + d, two)]))]
    return _lesson(
        "like_terms.two_letters", "algebra.like_terms", "challenge", "Simplify", math,
        _sum([(a - c, one), (b + d, two)]), IDENTITY, {"a": a, "b": b, "c": c, "d": d, "letters": (one, two)}, probes,
    )


def _two_letters_negative(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    c = _pick(rng, fixed, "c", lambda: rng.randint(1, 4))
    a = _pick(rng, fixed, "a", lambda: rng.randint(c + 1, 9))
    b = _pick(rng, fixed, "b", lambda: rng.randint(1, 4))
    d = _pick(rng, fixed, "d", lambda: rng.randint(b + 1, 8))
    one, two = _pick(rng, fixed, "letters", lambda: rng.choice(PAIRS))
    math = _sum([(a, one), (b, two), (-c, one), (-d, two)])
    probes = [("sign_error", _sum([(a - c, one), (d - b, two)])), ("sign_error", _sum([(a - c, one), (b + d, two)]))]
    return _lesson(
        "like_terms.two_letters_negative", "algebra.like_terms", "challenge", "Simplify", math,
        _sum([(a - c, one), (b - d, two)]), IDENTITY, {"a": a, "b": b, "c": c, "d": d, "letters": (one, two)}, probes,
    )


# Simplify expressions


def _terms_and_numbers(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a, b, c, d = (_pick(rng, fixed, name, lambda: rng.randint(2, 9)) for name in "abcd")
    probes = [("unlike_terms_combined", _term(a + b + c + d, "x"))]
    return _lesson(
        "simplifying.terms_and_numbers", "algebra.simplifying", "learn", "Simplify", f"{a}x + {b} + {c}x + {d}",
        _sum([(a + c, "x"), (b + d, "")]), IDENTITY, {"a": a, "b": b, "c": c, "d": d}, probes,
    )


def _subtract_term(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    c = _pick(rng, fixed, "c", lambda: rng.randint(1, 5))
    a = _pick(rng, fixed, "a", lambda: rng.randint(c + 1, 9))
    b = _pick(rng, fixed, "b", lambda: rng.randint(1, 9))
    d = _pick(rng, fixed, "d", lambda: rng.randint(1, 9))
    math = _sum([(a, "x"), (b, ""), (-c, "x"), (d, "")])
    probes = [("unlike_terms_combined", _term(a - c + b + d, "x")), ("sign_error", _sum([(a + c, "x"), (b + d, "")]))]
    return _lesson(
        "simplifying.subtract_term", "algebra.simplifying", "learn", "Simplify", math,
        _sum([(a - c, "x"), (b + d, "")]), IDENTITY, {"a": a, "b": b, "c": c, "d": d}, probes,
    )


def _number_first(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    c = _pick(rng, fixed, "c", lambda: rng.randint(1, 4))
    a = _pick(rng, fixed, "a", lambda: rng.randint(c + 1, 9))
    d = _pick(rng, fixed, "d", lambda: rng.randint(1, 6))
    b = _pick(rng, fixed, "b", lambda: rng.randint(d + 1, 12))
    math = _sum([(b, ""), (a, "x"), (-d, ""), (-c, "x")])
    probes = [("sign_error", _sum([(a + c, "x"), (b - d, "")])), ("sign_error", _sum([(a - c, "x"), (b + d, "")]))]
    return _lesson(
        "simplifying.number_first", "algebra.simplifying", "apply", "Simplify", math,
        _sum([(a - c, "x"), (b - d, "")]), IDENTITY, {"a": a, "b": b, "c": c, "d": d}, probes,
    )


def _minus_constant(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 8))
    c = _pick(rng, fixed, "c", lambda: rng.randint(2, 8))
    d = _pick(rng, fixed, "d", lambda: rng.randint(1, 6))
    b = _pick(rng, fixed, "b", lambda: rng.randint(d + 1, 12))
    math = _sum([(a, "x"), (b, ""), (c, "x"), (-d, "")])
    probes = [("unlike_terms_combined", _term(a + b + c - d, "x")), ("sign_error", _sum([(a + c, "x"), (b + d, "")]))]
    return _lesson(
        "simplifying.minus_constant", "algebra.simplifying", "apply", "Simplify", math,
        _sum([(a + c, "x"), (b - d, "")]), IDENTITY, {"a": a, "b": b, "c": c, "d": d}, probes,
    )


def _negative_result(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 6))
    c = _pick(rng, fixed, "c", lambda: rng.randint(a + 1, 9))
    b = _pick(rng, fixed, "b", lambda: rng.randint(2, 9))
    d = _pick(rng, fixed, "d", lambda: rng.choice([n for n in range(1, 10) if n != b]))
    math = _sum([(a, "x"), (-b, ""), (-c, "x"), (d, "")])
    probes = [("sign_error", _sum([(c - a, "x"), (d - b, "")])), ("sign_error", _sum([(a - c, "x"), (b + d, "")]))]
    return _lesson(
        "simplifying.negative_result", "algebra.simplifying", "challenge", "Simplify", math,
        _sum([(a - c, "x"), (d - b, "")]), IDENTITY, {"a": a, "b": b, "c": c, "d": d}, probes,
    )


def _two_letters_and_numbers(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    d = _pick(rng, fixed, "d", lambda: rng.randint(1, 4))
    a = _pick(rng, fixed, "a", lambda: rng.randint(d + 1, 9))
    e = _pick(rng, fixed, "e", lambda: rng.randint(1, 4))
    b = _pick(rng, fixed, "b", lambda: rng.randint(e + 1, 9))
    c = _pick(rng, fixed, "c", lambda: rng.randint(1, 9))
    math = _sum([(a, "x"), (b, "y"), (c, ""), (-d, "x"), (-e, "y")])
    probes = [("unlike_terms_combined", _sum([(a - d + b - e, "x"), (c, "")]))]
    return _lesson(
        "simplifying.two_letters", "algebra.simplifying", "challenge", "Simplify", math,
        _sum([(a - d, "x"), (b - e, "y"), (c, "")]), IDENTITY, {"a": a, "b": b, "c": c, "d": d, "e": e}, probes,
    )


def _two_letters_bracket(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    c = _pick(rng, fixed, "c", lambda: rng.randint(2, 4))
    a = _pick(rng, fixed, "a", lambda: rng.randint(c + 1, 8))
    d = _pick(rng, fixed, "d", lambda: rng.randint(1, 5))
    math = f"{a}a + {c}(b - a) + {_term(d, 'b')}"
    probes = [("distribution_incomplete", _sum([(a - c, "a"), (1 + d, "b")])), ("sign_error", _sum([(a + c, "a"), (c + d, "b")]))]
    return _lesson(
        "simplifying.two_letters_bracket", "algebra.simplifying", "demonstrate", "Simplify", math,
        _sum([(a - c, "a"), (c + d, "b")]), IDENTITY, {"a": a, "c": c, "d": d}, probes,
    )


# Brackets and common factors


def _expand(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 9))
    b = _pick(rng, fixed, "b", lambda: rng.randint(1, 9))
    probes = [("distribution_incomplete", _sum([(a, "x"), (b, "")]))]
    return _lesson(
        "brackets.expand", "algebra.brackets", "learn", "Expand", f"{a}(x + {b})", _sum([(a, "x"), (a * b, "")]),
        IDENTITY, {"a": a, "b": b}, probes,
    )


def _expand_minus(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 6))
    c = _pick(rng, fixed, "c", lambda: rng.randint(2, 5))
    b = _pick(rng, fixed, "b", lambda: rng.randint(1, 9))
    probes = [("distribution_incomplete", _sum([(a * c, "x"), (-b, "")])), ("sign_error", _sum([(a * c, "x"), (a * b, "")]))]
    return _lesson(
        "brackets.expand_minus", "algebra.brackets", "apply", "Expand", f"{a}({c}x - {b})",
        _sum([(a * c, "x"), (-a * b, "")]), IDENTITY, {"a": a, "b": b, "c": c}, probes,
    )


def _expand_negative(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 6))
    b = _pick(rng, fixed, "b", lambda: rng.randint(1, 9))
    probes = [("sign_error", _sum([(-a, "x"), (-a * b, "")])), ("distribution_incomplete", _sum([(-a, "x"), (-b, "")]))]
    return _lesson(
        "brackets.expand_negative", "algebra.brackets", "apply", "Expand", f"-{a}(x - {b})",
        _sum([(-a, "x"), (a * b, "")]), IDENTITY, {"a": a, "b": b}, probes,
    )


def _expand_and_collect(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 6))
    b = _pick(rng, fixed, "b", lambda: rng.randint(1, 9))
    c = _pick(rng, fixed, "c", lambda: rng.randint(2, 7))
    probes = [("distribution_incomplete", _sum([(a + c, "x"), (b, "")])), ("unlike_terms_combined", _term(a + c + a * b, "x"))]
    return _lesson(
        "brackets.expand_and_collect", "algebra.brackets", "challenge", "Simplify", f"{a}(x + {b}) + {c}x",
        _sum([(a + c, "x"), (a * b, "")]), IDENTITY, {"a": a, "b": b, "c": c}, probes,
    )


def _two_brackets(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    c = _pick(rng, fixed, "c", lambda: rng.randint(2, 4))
    a = _pick(rng, fixed, "a", lambda: rng.randint(c + 1, 7))
    b = _pick(rng, fixed, "b", lambda: rng.randint(1, 6))
    d = _pick(rng, fixed, "d", lambda: rng.randint(1, 6))
    probes = [("sign_error", _sum([(a - c, "x"), (a * b - c * d, "")]))]
    return _lesson(
        "brackets.two_brackets", "algebra.brackets", "challenge", "Simplify", f"{a}(x + {b}) - {c}(x - {d})",
        _sum([(a - c, "x"), (a * b + c * d, "")]), IDENTITY, {"a": a, "b": b, "c": c, "d": d}, probes,
    )


def _two_brackets_added(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 5))
    b = _pick(rng, fixed, "b", lambda: rng.randint(1, 6))
    c = _pick(rng, fixed, "c", lambda: rng.randint(2, 5))
    d = _pick(rng, fixed, "d", lambda: rng.randint(1, 6))
    probes = [("distribution_incomplete", _sum([(a + c, "x"), (b - d, "")])), ("sign_error", _sum([(a + c, "x"), (a * b + c * d, "")]))]
    return _lesson(
        "brackets.two_brackets_added", "algebra.brackets", "demonstrate", "Simplify", f"{a}(x + {b}) + {c}(x - {d})",
        _sum([(a + c, "x"), (a * b - c * d, "")]), IDENTITY, {"a": a, "b": b, "c": c, "d": d}, probes,
    )


def _coprime_pair(rng: random.Random) -> tuple[int, int]:
    while True:
        a, b = rng.randint(1, 5), rng.randint(1, 7)
        if gcd(a, b) == 1 and (a, b) != (1, 1):
            return a, b


def _factorise(template: str, tier: Tier, factor: int, a: int, b: int, minus: bool) -> LessonQuestion:
    sign = -1 if minus else 1
    math = _sum([(factor * a, "x"), (sign * factor * b, "")])
    inside = _sum([(a, "x"), (sign * b, "")])
    # A factor taken out of one term and not the other.
    probes = [
        ("common_factor_incomplete", f"{factor}({_sum([(a, 'x'), (sign * factor * b, '')])})"),
        ("common_factor_incomplete", f"{factor}({_sum([(factor * a, 'x'), (sign * b, '')])})"),
    ]
    return _lesson(
        template, "algebra.brackets", tier, "Factorise", math, f"{factor}({inside})", FACTORISE,
        {"factor": factor, "a": a, "b": b}, probes,
    )


def _factorise_plus(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    factor = _pick(rng, fixed, "factor", lambda: rng.randint(2, 6))
    a, b = (fixed["a"], fixed["b"]) if fixed and "a" in fixed else _coprime_pair(rng)
    return _factorise("brackets.factorise", "apply", factor, a, b, minus=False)


def _factorise_minus(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    factor = _pick(rng, fixed, "factor", lambda: rng.randint(2, 6))
    a, b = (fixed["a"], fixed["b"]) if fixed and "a" in fixed else _coprime_pair(rng)
    return _factorise("brackets.factorise_minus", "challenge", factor, a, b, minus=True)


def _factorise_larger(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    factor = _pick(rng, fixed, "factor", lambda: rng.randint(4, 9))
    a, b = (fixed["a"], fixed["b"]) if fixed and "a" in fixed else _coprime_pair(rng)
    return _factorise("brackets.factorise_larger", "demonstrate", factor, a, b, minus=True)


# Solve linear equations. Each has one whole-number solution, chosen first.


def _equation(template: str, tier: Tier, math: str, solution: int, params: dict[str, Any]) -> LessonQuestion:
    return _lesson(
        template, "algebra.linear_equations", tier, "Solve", math, f"x = {solution}", EQUATION,
        params | {"solution": solution}, [],
    )


def _one_step(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    solution = _pick(rng, fixed, "solution", lambda: rng.randint(1, 12))
    b = _pick(rng, fixed, "b", lambda: rng.randint(2, 9))
    return _equation("linear_equations.one_step", "learn", f"x + {b} = {solution + b}", solution, {"b": b})


def _two_step(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 9))
    b = _pick(rng, fixed, "b", lambda: rng.randint(1, 9))
    solution = _pick(rng, fixed, "solution", lambda: rng.randint(1, 9))
    return _equation("linear_equations.two_step", "learn", f"{a}x + {b} = {a * solution + b}", solution, {"a": a, "b": b})


def _two_step_minus(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 9))
    b = _pick(rng, fixed, "b", lambda: rng.randint(1, 9))
    solution = _pick(rng, fixed, "solution", lambda: rng.randint(2, 9))
    return _equation("linear_equations.two_step_minus", "apply", f"{a}x - {b} = {a * solution - b}", solution, {"a": a, "b": b})


def _both_sides(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    c = _pick(rng, fixed, "c", lambda: rng.randint(1, 4))
    a = _pick(rng, fixed, "a", lambda: rng.randint(c + 1, 8))
    b = _pick(rng, fixed, "b", lambda: rng.randint(1, 9))
    solution = _pick(rng, fixed, "solution", lambda: rng.randint(1, 8))
    d = (a - c) * solution + b
    math = f"{a}x + {b} = {_sum([(c, 'x'), (d, '')])}"
    return _equation("linear_equations.both_sides", "apply", math, solution, {"a": a, "b": b, "c": c})


def _equation_brackets(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 6))
    b = _pick(rng, fixed, "b", lambda: rng.randint(1, 6))
    solution = _pick(rng, fixed, "solution", lambda: rng.randint(b + 1, b + 7))
    return _equation(
        "linear_equations.brackets", "challenge", f"{a}(x - {b}) = {a * (solution - b)}", solution, {"a": a, "b": b}
    )


def _negative_solution(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 6))
    solution = _pick(rng, fixed, "solution", lambda: -rng.randint(1, 6))
    b = _pick(rng, fixed, "b", lambda: rng.randint(abs(a * solution) + 1, abs(a * solution) + 12))
    return _equation(
        "linear_equations.negative_solution", "challenge", f"{a}x + {b} = {a * solution + b}", solution, {"a": a, "b": b}
    )


def _brackets_both_sides(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    c = _pick(rng, fixed, "c", lambda: rng.randint(1, 2))
    a = _pick(rng, fixed, "a", lambda: rng.randint(c + 1, 5))
    b = _pick(rng, fixed, "b", lambda: rng.randint(1, 6))
    solution = _pick(rng, fixed, "solution", lambda: rng.randint(1, 8))
    d = a * (solution + b) - c * solution
    right = _sum([(c, "x"), (d, "")])
    return _equation(
        "linear_equations.brackets_both_sides", "demonstrate", f"{a}(x + {b}) = {right}", solution, {"a": a, "b": b, "c": c}
    )


_LIKE = ("Which terms have exactly the same letter?", "Add or subtract the numbers in front, and keep the letter.")
_SIMPLIFY = ("Underline the letter terms, then the number terms, each with its sign.", "Combine each kind of term on its own.")
_EXPAND = ("What does the number outside the bracket multiply?", "Multiply it by every term inside, and watch the signs.")
_FACTOR = ("What's the biggest number that divides every term?", "Write it outside a bracket, and divide each term by it inside.")
_SOLVE = ("What's being done to x? Undo the last thing first.", "Do the same thing to both sides, one step per line.")
_SUBSTITUTE = ("Write the expression again with the value in brackets.", "Multiply before you add or subtract.")

TEMPLATES: dict[str, Template] = {
    template.id: template
    for template in (
        Template("variables.repeated_letter", "algebra.variables", "learn", IDENTITY,
                 ("variable_dropped", "letters_multiplied"), "count 2-6 of one letter",
                 ("How many lots of the letter are there?", "Count them, and keep the letter."), _repeated_letter),
        Template("variables.one_and_more", "algebra.variables", "learn", IDENTITY,
                 ("variable_dropped", "letters_multiplied"), "x + bx, b 2-8",
                 ("A letter on its own means one of it.", "Count them, and keep the letter."), _one_and_more),
        Template("variables.take_away", "algebra.variables", "apply", IDENTITY,
                 ("variable_dropped", "sign_error"), "count 3-6 of one letter, then take one away",
                 ("How many are added, and how many taken away?", "Count them, and keep the letter."), _take_away),
        Template("variables.letters_and_numbers", "algebra.variables", "apply", IDENTITY,
                 ("unlike_terms_combined", "variable_dropped"), "count 2-4 of one letter, plus a constant 1-9",
                 ("Is the number a lot of the letter?", "Count the letters. The number stays on its own."), _letters_and_numbers),
        Template("variables.two_letters", "algebra.variables", "challenge", IDENTITY,
                 ("unlike_terms_combined",), "2-4 of one letter and a different 2-4 of another",
                 ("Count each letter separately.", "Different letters stay apart."), _two_letters_repeated),
        Template("variables.mixed_coefficients", "algebra.variables", "challenge", IDENTITY,
                 ("unlike_terms_combined", "variable_dropped"), "ax + x + c + x, a 2-6, c 1-9",
                 ("A letter on its own means one of it.", "Count the letters. The number stays on its own."), _mixed_coefficients),
        Template("substitution.positive", "algebra.substitution", "learn", ARITHMETIC,
                 ("substitution_as_digits",), "ax + b at x = v, all 1-9", _SUBSTITUTE, _substitute_positive),
        Template("substitution.subtract", "algebra.substitution", "apply", ARITHMETIC,
                 ("substitution_as_digits", "sign_error"), "ax - b at x = v, with ax > b", _SUBSTITUTE, _substitute_subtract),
        Template("substitution.negative", "algebra.substitution", "apply", ARITHMETIC,
                 ("sign_error",), "ax + b at x = -v, a and v 2-6", _SUBSTITUTE, _substitute_negative),
        Template("substitution.square_negative", "algebra.substitution", "challenge", ARITHMETIC,
                 ("sign_error",), "x^2 + ax at x = -v, v 2-5", ("A negative number squared is positive.", _SUBSTITUTE[1]),
                 _substitute_square),
        Template("substitution.two_values", "algebra.substitution", "challenge", ARITHMETIC,
                 ("sign_error",), "ax - y at x = p and y = -q", ("Taking away a negative is adding.", _SUBSTITUTE[1]),
                 _substitute_two),
        Template("substitution.square_expression", "algebra.substitution", "demonstrate", ARITHMETIC,
                 ("sign_error",), "ax^2 - x at x = -v, a 2-4, v 2-4",
                 ("Square first, then multiply. A negative squared is positive.", "Taking away a negative is adding."),
                 _substitute_square_expression),
        Template("like_terms.add_two", "algebra.like_terms", "learn", IDENTITY,
                 ("variable_dropped", "letters_multiplied", "letter_changed"), "ax + bx, a and b 2-9", _LIKE, _add_two),
        Template("like_terms.subtract_two", "algebra.like_terms", "learn", IDENTITY,
                 ("variable_dropped", "sign_error", "letter_changed"), "ax - bx, a at least b + 2", _LIKE, _subtract_two),
        Template("like_terms.three_terms", "algebra.like_terms", "apply", IDENTITY,
                 ("variable_dropped", "sign_error", "letter_changed"), "ax - bx + cx, a > b", _LIKE, _three_terms),
        Template("like_terms.hidden_one", "algebra.like_terms", "apply", IDENTITY,
                 ("variable_dropped", "sign_error", "letter_changed"), "ax + bx - x", ("A letter on its own means one of it.", _LIKE[1]), _hidden_one),
        Template("like_terms.two_letters", "algebra.like_terms", "challenge", IDENTITY,
                 ("unlike_terms_combined", "variable_dropped", "sign_error", "letter_changed"), "aA + bB - cA + dB, a > c", _LIKE, _two_letters),
        Template("like_terms.two_letters_negative", "algebra.like_terms", "challenge", IDENTITY,
                 ("unlike_terms_combined", "sign_error", "letter_changed"), "aA + bB - cA - dB, a > c, d > b", _LIKE, _two_letters_negative),
        Template("simplifying.terms_and_numbers", "algebra.simplifying", "learn", IDENTITY,
                 ("unlike_terms_combined",), "ax + b + cx + d, all 2-9", _SIMPLIFY, _terms_and_numbers),
        Template("simplifying.subtract_term", "algebra.simplifying", "learn", IDENTITY,
                 ("unlike_terms_combined", "sign_error"), "ax + b - cx + d, a > c", _SIMPLIFY, _subtract_term),
        Template("simplifying.number_first", "algebra.simplifying", "apply", IDENTITY,
                 ("sign_error",), "b + ax - d - cx, a > c, b > d", _SIMPLIFY, _number_first),
        Template("simplifying.minus_constant", "algebra.simplifying", "apply", IDENTITY,
                 ("unlike_terms_combined", "sign_error"), "ax + b + cx - d, b > d", _SIMPLIFY, _minus_constant),
        Template("simplifying.negative_result", "algebra.simplifying", "challenge", IDENTITY,
                 ("sign_error", "unlike_terms_combined"), "ax - b - cx + d, c > a", _SIMPLIFY, _negative_result),
        Template("simplifying.two_letters", "algebra.simplifying", "challenge", IDENTITY,
                 ("unlike_terms_combined", "sign_error"), "ax + by + c - dx - ey, a > d, b > e", _SIMPLIFY,
                 _two_letters_and_numbers),
        Template("simplifying.two_letters_bracket", "algebra.simplifying", "demonstrate", IDENTITY,
                 ("distribution_incomplete", "sign_error"), "aa + c(b - a) + db, a > c", _EXPAND, _two_letters_bracket),
        Template("brackets.expand", "algebra.brackets", "learn", IDENTITY,
                 ("distribution_incomplete",), "a(x + b), a 2-9, b 1-9", _EXPAND, _expand),
        Template("brackets.expand_minus", "algebra.brackets", "apply", IDENTITY,
                 ("distribution_incomplete", "sign_error"), "a(cx - b)", _EXPAND, _expand_minus),
        Template("brackets.expand_negative", "algebra.brackets", "apply", IDENTITY,
                 ("sign_error", "distribution_incomplete"), "-a(x - b)", ("A negative times a negative is positive.", _EXPAND[1]),
                 _expand_negative),
        Template("brackets.factorise", "algebra.brackets", "apply", FACTORISE,
                 ("common_factor_incomplete",), "f(ax + b) multiplied out, f 2-6, a and b coprime", _FACTOR, _factorise_plus),
        Template("brackets.expand_and_collect", "algebra.brackets", "challenge", IDENTITY,
                 ("distribution_incomplete", "unlike_terms_combined"), "a(x + b) + cx", _EXPAND, _expand_and_collect),
        Template("brackets.two_brackets", "algebra.brackets", "challenge", IDENTITY,
                 ("sign_error", "distribution_incomplete"), "a(x + b) - c(x - d), a > c", _EXPAND, _two_brackets),
        Template("brackets.factorise_minus", "algebra.brackets", "challenge", FACTORISE,
                 ("common_factor_incomplete",), "f(ax - b) multiplied out, f 2-6, a and b coprime", _FACTOR, _factorise_minus),
        Template("brackets.two_brackets_added", "algebra.brackets", "demonstrate", IDENTITY,
                 ("distribution_incomplete", "sign_error"), "a(x + b) + c(x - d)", _EXPAND, _two_brackets_added),
        Template("brackets.factorise_larger", "algebra.brackets", "demonstrate", FACTORISE,
                 ("common_factor_incomplete",), "f(ax - b) multiplied out, f 4-9", _FACTOR, _factorise_larger),
        Template("linear_equations.one_step", "algebra.linear_equations", "learn", EQUATION,
                 ("incorrect_inverse_operation",), "x + b = c, solution 1-12", _SOLVE, _one_step),
        Template("linear_equations.two_step", "algebra.linear_equations", "learn", EQUATION,
                 ("incorrect_inverse_operation",), "ax + b = c, solution 1-9", _SOLVE, _two_step),
        Template("linear_equations.two_step_minus", "algebra.linear_equations", "apply", EQUATION,
                 ("incorrect_inverse_operation",), "ax - b = c, solution 2-9", _SOLVE, _two_step_minus),
        Template("linear_equations.both_sides", "algebra.linear_equations", "apply", EQUATION,
                 ("incorrect_inverse_operation",), "ax + b = cx + d, a > c", _SOLVE, _both_sides),
        Template("linear_equations.brackets", "algebra.linear_equations", "challenge", EQUATION,
                 ("distribution_incomplete", "incorrect_inverse_operation"), "a(x - b) = c, positive solution",
                 ("Expand the bracket first, or divide both sides by the number outside.", _SOLVE[1]), _equation_brackets),
        Template("linear_equations.negative_solution", "algebra.linear_equations", "challenge", EQUATION,
                 ("incorrect_inverse_operation",), "ax + b = c, solution -1 to -6", _SOLVE, _negative_solution),
        Template("linear_equations.brackets_both_sides", "algebra.linear_equations", "demonstrate", EQUATION,
                 ("distribution_incomplete", "incorrect_inverse_operation"), "a(x + b) = cx + d, a > c", _SOLVE,
                 _brackets_both_sides),
    )
}


def templates_for(skills: list[str] | tuple[str, ...], tier: Tier | None = None) -> list[Template]:
    return [t for t in TEMPLATES.values() if t.skill in skills and (tier is None or t.tier == tier)]


def build(template_id: str, rng: random.Random, fixed: dict[str, Any] | None = None) -> LessonQuestion:
    return TEMPLATES[template_id].build(rng, fixed)


async def is_checkable(question: LessonQuestion, timeout_s: float) -> bool:
    """The verifier must accept the answer key as right, and as a finished answer to the question."""

    from quack.services.math_verifier import LineInput
    from quack.services.practice import is_checkable as practice_checkable

    form = FORM_OF_METHOD.get(question.method)
    if form is None:
        return await practice_checkable(question.question, timeout_s)
    line = f"{question.question.math} = {question.question.expected}"
    try:
        report, _ = await shared_pool.verify([LineInput(0, line, posed=True)], frozenset(), timeout_s)
    except VerificationFailed:
        return False
    if report.status != "valid":
        return False
    return await shared_pool.completes(question.question.math, question.question.expected, line, timeout_s, form=form)


async def new_lesson_question(
    template_id: str, rng: random.Random, avoid: set[str], timeout_s: float, fixed: dict[str, Any] | None = None
) -> LessonQuestion | None:
    """A question from this template that hasn't been asked here, and that the verifier can check."""

    for attempt in range(MAX_TRIES):
        question = build(template_id, rng, fixed if attempt == 0 else None)
        if question.question.math in avoid:
            continue
        if await is_checkable(question, timeout_s):
            return question
    return None


# The starter levels of the other paths, which add themselves to TEMPLATES. Last, as they're built from what's above.
import quack.services.starter_questions  # noqa: E402, F401
