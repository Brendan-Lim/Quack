"""Deterministic checking of written mathematical steps.

The verifier is the only component allowed to call a step wrong. It works on
transcribed text and knows nothing about the model that produced it, beyond the
hints in `LineInput`, and those hints can only ever make it abstain or choose
which check applies. They cannot turn a correct step into an error.

Bias: when in doubt, return `uncertain`. A missed mistake costs little. A false
alarm teaches the student to ignore the duck.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Literal

import sympy as sp

from quack.services.integration import antiderivative
from quack.services.math_parser import (
    Ambiguous,
    MathParseError,
    Node,
    Precision,
    Statement,
    Unsupported,
    contains_kind,
    format_math,
    format_node,
    has_decimal,
    has_distribution,
    leaf_count,
    parse_expression,
    parse_statement,
    to_sympy,
)

log = logging.getLogger("quack.verifier")

StepStatus = Literal["valid", "invalid", "uncertain", "unsupported"]
Continues = Literal["yes", "no", "unknown"]
# What the student is doing on a line, where that's been established. See task_intent.py.
TaskIntent = Literal["auto", "simplify", "expand", "factorise", "solve", "differentiate", "integrate"]
# A line in any of these says its two sides are the same thing, whatever else it could be.
INTENTS_THAT_CLAIM_AN_IDENTITY = {"simplify", "expand", "factorise"}
# What a step means, as distinct from whether it counts as an error. See `disposition_of`.
Disposition = Literal[
    "verified_correct", "verified_incorrect", "incomplete", "ambiguous_intent", "transcription_uncertain",
    "unsupported", "valid_under_assumptions", "needs_clarification",
]

SIMPLIFY_OPERATIONS = {"expand", "simplify", "factor", "combine_like_terms", "arithmetic"}
ROUNDING_TOLERANCE = sp.Rational(2, 100)
MAX_ALTERNATIVES = 3
MAX_ROOT_DEGREE = 6
MIN_SAMPLE_POINTS = 3
MAX_POWER_BITS = 4096
MIN_VALUE_ROUNDED_TO_TENS = 100
ROUNDING_TO_TENS_TOLERANCE = sp.Rational(3, 100)
NEGLIGIBLE = sp.Float("1e-15")
MAX_EQUATIONS_REMEMBERED = 6
LIKELY_CONSTANTS = {"a", "b", "c", "k", "m", "n", "p", "q"}
# An exact value this long is a nuisance to write out, so a shorter one may be rounded on purpose.
MAX_PLACES_WRITTEN_OUT = 3
# Further out than this, `≈` is wrong whatever precision was meant: 3 * 4 ≈ 100.
APPROXIMATION_FAR_OFF = sp.Rational(1, 2)
# Letters that can only stand for the constant of integration, or are the usual way of writing it.
ARBITRARY_CONSTANTS = {"C", "c", "K", "k"}
ROUNDED_INPUTS = ("3.14", "3.142", "1.41", "1.414", "1.73", "1.732", "2.24", "2.72", "2.718", "0.707", "0.866")

# Deterministic, irrational-free sample points on both sides of zero, near and far from it.
# Far points matter: sqrt((x-3)^2) = x - 3 only holds for x above 3.
_POSITIVE_SAMPLES = (
    sp.Rational(3, 7), sp.Rational(11, 5), sp.Rational(7, 3), sp.Rational(19, 11),
    sp.Rational(23, 2), sp.Rational(83, 2),
)
_SAMPLES = (
    *_POSITIVE_SAMPLES,
    sp.Rational(-5, 7), sp.Rational(-13, 5), sp.Rational(-8, 3), sp.Rational(-17, 11),
    sp.Rational(-23, 2), sp.Rational(-83, 2),
)


@dataclass(frozen=True)
class LineInput:
    index: int
    math: str
    legible: bool = True
    continues: Continues = "unknown"
    operation: str = "unknown"
    alternatives: tuple[str, ...] = ()
    incomplete: bool = False
    # Quack asked for this itself, as a practice question, so there's no doubt what's being claimed.
    posed: bool = False
    # What the student or the practice question has said this line is doing. Never from the model.
    intent: TaskIntent = "auto"
    # What the student has said this whole page is doing, which a task written on the page outranks.
    page_intent: TaskIntent = "auto"
    # An equation Quack set, standing above the page as if it had been written there.
    given: str = ""
    # What a question Quack set gives its letters, as (letter, value): `If x = -4, work out x^2 + 6x`.
    # Quack's own, never a model's. Lines are then compared by their value with those put in.
    values: tuple[tuple[str, str], ...] = ()


TermProblem = Literal["right", "coefficient", "exponent", "sign", "constant", "missing", "wrong_term"]


@dataclass(frozen=True)
class TermFact:
    """One written term of a differentiated sum, compared with what the student wrote for it."""

    term: str
    derivative: str
    written: str | None
    problem: TermProblem


@dataclass(frozen=True)
class StepResult:
    status: StepStatus
    operation_type: str
    line: int
    from_line: int | None = None
    error_category: str | None = None
    error_type: str | None = None
    evidence: str | None = None
    reason: str | None = None
    previous_step: str | None = None
    current_step: str | None = None
    expected: str | None = None
    fingerprint: str | None = None
    # What the written line actually does, from its parsed operator. Never from a model's label.
    parsed_operation: str | None = None
    term_facts: tuple[TermFact, ...] = ()
    # Terms in the student's answer that no term of the source produces.
    extra_terms: tuple[str, ...] = ()
    # The line that the piece of working this step belongs to starts on.
    problem_line: int | None = None
    # True, but with a condition the line doesn't state: `x/x = 1` where x isn't 0.
    assumption: str | None = None
    # True and worth a word, though not a mistake: an antiderivative with no constant.
    note: str | None = None
    # Only a claim because of a written task or the page's setting. The next line can say otherwise.
    intent_from_context: bool = False


@dataclass(frozen=True)
class VerificationReport:
    status: StepStatus
    steps: tuple[StepResult, ...] = ()

    @property
    def first_invalid(self) -> StepResult | None:
        return next((step for step in self.steps if step.status == "invalid"), None)

    @property
    def invalid_steps(self) -> tuple[StepResult, ...]:
        return tuple(step for step in self.steps if step.status == "invalid")

    def without(self, fingerprints: frozenset[str]) -> VerificationReport:
        steps = tuple(step for step in self.steps if step.status != "invalid" or step.fingerprint not in fingerprints)
        return VerificationReport.of(steps)

    @staticmethod
    def of(steps: tuple[StepResult, ...]) -> VerificationReport:
        return VerificationReport(_overall(list(steps)), steps)


@dataclass(frozen=True)
class _Context:
    prev: Statement | None = None
    prev_line: LineInput | None = None
    anchor: Statement | None = None
    anchor_line: int | None = None
    anchor_input: LineInput | None = None
    # None marks a name that has been given conflicting definitions.
    definitions: dict[str, Node | None] = field(default_factory=dict)
    # How many expression lines in a row, each a step from the last.
    expression_run: int = 0
    # Recent equations, for when several branches of one are worked out on separate lines.
    equations: tuple[Statement, ...] = ()
    # A task written on the page, until the working it's about comes to an end.
    task: str = ""
    # Letters given a value on the page, so that `3x + 2x`, `= 5` may be that value put in.
    valued: frozenset[str] = frozenset()
    # A line above couldn't be read, so it may have given one: `Let x = 1`.
    unread_above: bool = False
    # The written task has had a right answer, so a later line may be another exercise.
    task_answered: bool = False


Verdict = Literal["equal", "different", "domain_sensitive", "unknown"]


def _is_too_big_to_evaluate(expr: sp.Expr, point: dict[sp.Symbol, sp.Rational]) -> bool:
    """Would a power at this point have more bits than is worth working out? 3^(x^6) at x = 41."""

    for node in sorted(expr.atoms(sp.Pow, sp.exp), key=sp.count_ops):
        base, exponent = node.as_base_exp()
        if not exponent.free_symbols:
            continue
        try:
            size = abs(float(exponent.subs(point))) * math.log2(abs(float(base.subs(point))) + 2)
        except (TypeError, ValueError, OverflowError):
            return True
        if size > MAX_POWER_BITS:
            return True
    return False


def _evaluate_at(expr: sp.Expr, point: dict[sp.Symbol, sp.Rational]) -> sp.Float | None:
    if _is_too_big_to_evaluate(expr, point):
        return None
    try:
        value = sp.N(expr.subs(point), 25)
    except (TypeError, ValueError, ZeroDivisionError, ArithmeticError):
        return None
    if value.has(sp.zoo, sp.nan, sp.oo, -sp.oo) or not value.is_number:
        return None
    real, imag = value.as_real_imag()
    if abs(imag) > 1e-15:
        return None
    return sp.Float(real, 25)


def _sample_points(symbols: list[sp.Symbol]) -> list[dict[sp.Symbol, sp.Rational]]:
    points = [
        {symbol: _SAMPLES[(i + offset * 3) % len(_SAMPLES)] for offset, symbol in enumerate(symbols)}
        for i in range(len(_SAMPLES))
    ]
    if len(symbols) > 1:
        # sqrt(a + b) = sqrt(a) + sqrt(b) needs both positive to be evaluable at all.
        points += [
            {symbol: _POSITIVE_SAMPLES[(i + offset) % len(_POSITIVE_SAMPLES)] for offset, symbol in enumerate(symbols)}
            for i in range(len(_POSITIVE_SAMPLES))
        ]
    return points


def compare(a: sp.Expr, b: sp.Expr) -> Verdict:
    """Decide whether two expressions agree.

    Symbolic zero proves equality. Symbolic non-zero proves nothing, because
    SymPy can fail to simplify a true identity, so a difference is established
    numerically. Agreement at some points and not others means the step depends
    on assumptions about the variable, and we report that instead of an error.
    """

    difference = a - b
    if difference == 0 or sp.expand(difference) == 0:
        return "equal"
    try:
        if sp.cancel(sp.together(difference)) == 0:
            return "equal"
    except (sp.PolynomialError, TypeError, ValueError):
        pass

    symbols = sorted(a.free_symbols | b.free_symbols, key=str)
    if not symbols:
        # Exact values have no need of a tolerance: 10^-16 isn't 0.
        if a.is_Rational and b.is_Rational:
            return "different"
        left, right = _evaluate_at(a, {}), _evaluate_at(b, {})
        if left is None or right is None:
            return "unknown"
        return "equal" if abs(left - right) < 1e-15 else "different"

    agree = disagree = 0
    same_size = True
    for point in _sample_points(symbols):
        left, right = _evaluate_at(a, point), _evaluate_at(b, point)
        if left is None or right is None:
            continue
        largest = max(abs(left), abs(right))
        # e^x at x = -41 says nothing: any tolerance would call it equal to 2e^x.
        if largest < NEGLIGIBLE:
            continue
        if abs(left - right) <= 1e-12 * largest:
            agree += 1
        else:
            disagree += 1
            same_size = same_size and abs(abs(left) - abs(right)) <= 1e-12 * largest
    if agree + disagree < MIN_SAMPLE_POINTS:
        return "unknown"
    if disagree == 0:
        return "equal"
    if agree == 0:
        # sqrt((x - 50)^2) = x - 50 holds beyond the sample points, which only reach x = 41.
        return "domain_sensitive" if same_size and _has_root(a, b) else "different"
    return "domain_sensitive"


def _has_root(*exprs: sp.Expr) -> bool:
    # SymPy turns sqrt(y^2) into |y| for a real y.
    return any(expr.has(sp.Abs) or any(not power.exp.is_integer for power in expr.atoms(sp.Pow)) for expr in exprs)


def _decimal_places(node: Node) -> int:
    own = len(node.value.split(".")[1]) if node.kind == "num" and "." in node.value else 0
    return max([own, *(_decimal_places(child) for child in node.args)])


def _is_plain_number(node: Node) -> bool:
    if node.kind == "num":
        return True
    return node.kind == "neg" and _is_plain_number(node.args[0])


def _rounds_to(exact: sp.Expr, written: sp.Expr, places: int) -> bool:
    """Could `written` be `exact` rounded or cut off to this many decimal places?"""

    if exact.is_real is not True or written.is_real is not True:
        return False
    scale = sp.Integer(10) ** places
    scaled = sp.N(abs(exact) * scale, 40)
    target = abs(written) * scale
    nearest = sp.floor(scaled + sp.Rational(1, 2))
    return (exact >= 0) == (written >= 0) and target in (nearest, sp.floor(scaled))


def _rounds_to_tens(exact: sp.Expr, written: sp.Expr) -> bool:
    """Significant figures: 1234 written as 1200. Close to it, too, or 5 + 4 = 10 would pass."""

    if not written.is_Integer or written == 0 or exact.is_real is not True or abs(exact) < MIN_VALUE_ROUNDED_TO_TENS:
        return False
    digits = str(abs(written))
    zeros = len(digits) - len(digits.rstrip("0"))
    nearest = sp.floor(sp.N(abs(exact) / sp.Integer(10) ** zeros, 40) + sp.Rational(1, 2)) * sp.Integer(10) ** zeros
    close = abs(exact - written) <= ROUNDING_TO_TENS_TOLERANCE * abs(exact)
    return zeros > 0 and nearest == abs(written) and (exact > 0) == (written > 0) and close


def _is_terminating(value: sp.Expr) -> bool:
    """Can this be written exactly as a decimal? If so, there's no need to round it to a whole number."""

    if not value.is_Rational:
        return False
    denominator = int(value.q)
    for prime in (2, 5):
        while denominator % prime == 0:
            denominator //= prime
    return denominator == 1


def _could_just_be_written(value: sp.Expr) -> bool:
    """3.5 is there to be written, so 3 is no rounding of it. 1157.625 is another matter."""

    return _is_terminating(value) and (value * 10).is_Integer


def _coefficients_round(left: Node, right: Node, a: sp.Expr, b: sp.Expr) -> bool:
    """0.3x for x/3, compared a coefficient at a time, because 0.3 is 10% out as a value."""

    symbols = sorted(a.free_symbols | b.free_symbols, key=str)
    try:
        first, second = sp.Poly(sp.expand(a), *symbols), sp.Poly(sp.expand(b), *symbols)
    except sp.PolynomialError:
        return False
    if {monomial for monomial, _ in first.terms()} != {monomial for monomial, _ in second.terms()}:
        return False
    places = range(_decimal_places(right) + 1)
    return all(
        any(
            _rounds_to(first.coeff_monomial(monomial), coefficient, place)
            for place in places
            if (place > 0 or not _could_just_be_written(first.coeff_monomial(monomial)))
            # x/4 comes to exactly 0.25x, so 0.2x is wrong, as with 1/4 = 0.3.
            and _needs_rounding_to_write(left, first.coeff_monomial(monomial))
        )
        for monomial, coefficient in second.terms()
    )


def _is_rounded_input(node: Node) -> bool:
    """A value that is only ever approximate as written, like 3.14 or 0.33. Sums with it land nearby."""

    if node.kind == "num" and "." in node.value:
        places = len(node.value.split(".")[1])
        thirds = sp.Rational(node.value) * 3
        # 0.33 and 0.67 are thirds. 2.99 and 1.01 are near a whole number, and are only themselves.
        recurring = (
            places >= 2
            and thirds.q != 1
            and round(thirds) % 3 != 0
            and abs(thirds - round(thirds)) <= sp.Rational(3, 10**places)
        )
        return recurring or node.value in ROUNDED_INPUTS
    return any(_is_rounded_input(child) for child in node.args)


def _is_standard_form(node: Node) -> bool:
    return (
        node.kind == "mul"
        and len(node.args) == 2
        and node.args[0].kind == "num"
        and node.args[1].kind == "pow"
        and node.args[1].args[0] == Node("num", value="10")
    )


def _is_possible_rounding(left: Node, right: Node, a: sp.Expr, b: sp.Expr) -> bool:
    if a.free_symbols or b.free_symbols:
        # Coefficients such as 0.33x, compared at sample points.
        if not (has_decimal(left) or has_decimal(right)):
            return False
        if _coefficients_round(left, right, a, b):
            return True
        # Otherwise only a value that's approximate to begin with, like 0.33x, can land nearby.
        if not _is_rounded_input(left):
            return False
        points = _sample_points(sorted(a.free_symbols | b.free_symbols, key=str))
        return all(
            abs(lhs - rhs) <= ROUNDING_TOLERANCE * max(abs(lhs), abs(rhs))
            for lhs, rhs in ((_evaluate_at(a, point), _evaluate_at(b, point)) for point in points)
            if lhs is not None and rhs is not None
        )
    return _is_numeric_rounding(left, right, a, b)


def _needs_rounding_to_write(left: Node, exact: sp.Expr) -> bool:
    """Is there any reason for a decimal answer to be approximate?

    `=` means equal. 1/4 comes to exactly 0.25, so 0.3 is wrong. But no decimal can say 1/3 or 2pi,
    and a value worked out from decimals such as 1.05^3 has more figures than anyone writes down.
    """

    return has_decimal(left) or not _can_be_written_out(exact)


def _can_be_written_out(value: sp.Expr) -> bool:
    return value.is_Rational is True and (value * 10**MAX_PLACES_WRITTEN_OUT).is_Integer is True


def _is_numeric_rounding(left: Node, right: Node, a: sp.Expr, b: sp.Expr) -> bool:
    """Could this `=` be a decimal rounded in the usual way? Being close isn't evidence of that.

    An answer written with no decimal point is only rounding if the working was in decimals to
    begin with: 1/3 = 0, 2/3 = 1, 22/7 = 3, and 1234 = 1200 are all just false. See docs/ABSTENTION.md.
    """

    written_as_decimal = has_decimal(right)
    close = abs(a - b) <= ROUNDING_TOLERANCE * abs(a)
    # A value with pi or a root in it can only ever be approximated in writing.
    only_approximable = a.has(sp.pi, sp.E) or not a.is_rational or not b.is_rational
    # pi * 5^2 = 79 is usual, and a tenth out at 2pi = 6 isn't. 7 = sqrt(50) has it the wrong way round.
    whole_on_the_left = a.is_Integer and not has_decimal(left)
    if close and only_approximable and not whole_on_the_left:
        return True
    if close and _is_rounded_input(left):
        return True
    can_round = _needs_rounding_to_write(left, a)
    if _is_standard_form(right):
        # 1.23 * 10^3 for 1234.77: it's the leading figures that are rounded.
        scale = to_sympy(right.args[1])
        if can_round and _rounds_to(a / scale, b / scale, _decimal_places(right.args[0])):
            return True
    if _is_plain_number(right):
        # 2.5 + 1 = 3 isn't rounding, because 3.5 was there to be written. 12.5 = 13 might be.
        # Nothing worth working out comes to nothing, so 0.5 * 0.5 = 0 is simply wrong.
        from_decimals = has_decimal(left) and (_is_plain_number(left) or (b != 0 and not _could_just_be_written(a)))
        if (can_round if written_as_decimal else from_decimals) and _rounds_to(a, b, _decimal_places(right)):
            return True
    # 0.33 = 1/3, the same thing the other way round.
    backwards = _is_plain_number(left) and has_decimal(left) and _needs_rounding_to_write(right, b)
    return backwards and _rounds_to(b, a, _decimal_places(left))


def _nearest_at(value: sp.Expr, places: int) -> sp.Expr:
    """`value` rounded to this many decimal places, with a half going up, as it's taught."""

    scale = sp.Integer(10) ** places
    scaled = abs(value) * scale if value.is_Rational else sp.Rational(str(sp.N(abs(value), 60))) * scale
    return (1 if value >= 0 else -1) * sp.floor(scaled + sp.Rational(1, 2)) / scale


def _places_of(precision: Precision, exact: sp.Expr) -> int | None:
    if precision.places is not None:
        return precision.places
    if exact == 0 or precision.figures is None or precision.figures < 1:
        return None
    return precision.figures - 1 - int(sp.floor(sp.log(abs(exact), 10)))


def _implied_places(right: Node, value: sp.Expr) -> int:
    """What the way a number is written says it's rounded to: 0.33 is 2 places, 1200 the nearest 100."""

    if has_decimal(right):
        return _decimal_places(right)
    if not value.is_Integer or value == 0:
        return 0
    digits = str(abs(value))
    return len(digits.rstrip("0")) - len(digits)


def _rounding_mistake(
    left: Node, right: Node, a: sp.Expr, b: sp.Expr, line: int, from_line: int | None, expected: sp.Expr
) -> StepResult:
    previous, current = format_node(left), format_node(right)
    # A rounded value is a decimal, so it's said as one: 0.33 and not 33/100.
    rounded = format(Decimal(int(expected.p)) / Decimal(int(expected.q)), "f")
    return StepResult(
        "invalid", "arithmetic", line, from_line,
        error_category="arithmetic",
        error_type="rounding_error",
        evidence=f"rounded, {previous} is {rounded}, got {current}",
        previous_step=previous,
        current_step=current,
        expected=rounded,
        fingerprint=_fingerprint("rounding", a, b, _written_form(left)),
        parsed_operation="rounding",
    )


def _check_rounding(left: Node, right: Node, precision: Precision | None, line: int, from_line: int | None) -> StepResult:
    """A line that says it's approximate: `1/3 ≈ 0.33`, or `12 = 10 (nearest ten)`.

    With the precision stated, it's right or wrong. Without one, the only guide is how the number
    is written, and that's enough to accept `≈ 0.33`. A whole number like `≈ 0` or `≈ 10` might be
    any rounding at all, so nothing is claimed unless it can't be right at any precision.
    """

    operation = "approximation"
    try:
        a, b = to_sympy(left), to_sympy(right)
    except MathParseError as exc:
        return StepResult("unsupported", operation, line, from_line, reason=exc.reason)
    previous, current = format_node(left), format_node(right)
    if (
        a.free_symbols or b.free_symbols or a.is_real is not True or b.is_real is not True
        or _uses_numeric_trig(a) or _uses_numeric_trig(b)
        or _mentions_e(left) or _mentions_e(right)
        # pi/6 ≈ 30 is an angle with its degree sign left off.
        or (precision is None and a.has(sp.pi) != b.has(sp.pi) and not has_decimal(right))
        # It's the answer that's rounded. 0.33 = 1/3 (2dp) has it backwards.
        or not _is_plain_number(right)
    ):
        return StepResult("uncertain", operation, line, from_line, reason="approximation_unclear")
    if compare(a, b) == "equal":
        return StepResult("valid", operation, line, from_line, previous_step=previous, current_step=current)
    # 10 ≈ 12 (nearest ten) has it backwards too.
    if _is_plain_number(left):
        backwards = _places_of(precision, b) if precision is not None else _implied_places(left, a)
        if backwards is not None and compare(_nearest_at(b, backwards), a) == "equal":
            return StepResult("uncertain", operation, line, from_line, reason="approximation_unclear")
    if precision is not None:
        places = _places_of(precision, a)
        if places is None:
            return StepResult("uncertain", operation, line, from_line, reason="approximation_precision_unknown")
        nearest = _nearest_at(a, places)
        if compare(nearest, b) == "equal":
            return StepResult("valid", operation, line, from_line, previous_step=previous, current_step=current)
        # 1/4 to 1dp, with 0.25 written as 0.2. A half can be taught to go other ways.
        scale = sp.Integer(10) ** places
        scaled = abs(a) * scale
        if scaled - sp.floor(scaled) == sp.Rational(1, 2) and compare(abs(b) * scale, sp.floor(scaled)) == "equal":
            return StepResult("uncertain", operation, line, from_line, reason="approximation_half_way")
        return _rounding_mistake(left, right, a, b, line, from_line, nearest)
    implied = _implied_places(right, b)
    if compare(_nearest_at(a, implied), b) == "equal":
        if has_decimal(right):
            return StepResult("valid", operation, line, from_line, previous_step=previous, current_step=current)
        return StepResult("uncertain", operation, line, from_line, reason="approximation_precision_unknown")
    largest = max(abs(a), abs(b))
    if abs(a - b) <= APPROXIMATION_FAR_OFF * largest:
        return StepResult("uncertain", operation, line, from_line, reason="approximation_precision_unknown")
    return _rounding_mistake(left, right, a, b, line, from_line, _nearest_at(a, max(implied, 0)))


def _fingerprint(category: str, *parts: sp.Expr | str) -> str:
    payload = category + "|" + "|".join(part if isinstance(part, str) else sp.srepr(part) for part in parts)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _written_form(node: Node) -> str:
    """Distinguishes problems with the same value, like 3x + 3x and 6x. Ignores term order, so
    that reading the same line twice gives the same identity."""

    return "".join(sorted(format_node(node).replace(" ", "").replace("*", "")))


def _term_sign_flipped(a: sp.Expr, b: sp.Expr) -> bool:
    a_terms = set(sp.Add.make_args(sp.expand(a)))
    b_terms = set(sp.Add.make_args(sp.expand(b)))
    only_a, only_b = a_terms - b_terms, b_terms - a_terms
    return bool(only_a) and {-term for term in only_a} == only_b


def _uses_numeric_trig(expr: sp.Expr) -> bool:
    # sin(180 - x) too, where the number gives away that the angle is in degrees.
    angles = (node.args[0] for node in expr.atoms(sp.sin, sp.cos, sp.tan))
    return any(not angle.has(sp.pi) and (angle.is_number or angle.as_coeff_Add()[0] != 0) for angle in angles)


def _agrees_in_degrees(a: sp.Expr, b: sp.Expr) -> bool:
    def in_degrees(expr: sp.Expr) -> sp.Expr:
        return expr.replace(
            lambda node: isinstance(node, sp.sin | sp.cos | sp.tan) and node.args[0].is_number,
            lambda node: node.func(node.args[0] * sp.pi / 180),
        )

    left, right = in_degrees(a), in_degrees(b)
    if compare(left, right) == "equal":
        return True
    difference = sp.N(left - right)
    return difference.is_number and abs(difference) < sp.Rational(1, 100)


def _diagnose_derivative(source: sp.Expr, variable: sp.Symbol, claimed: sp.Expr) -> tuple[str, str]:
    """Name a mistake only if the mathematics shows it. Otherwise it's an unclassified mismatch."""

    expected = sp.diff(source, variable)

    inners = [
        node.base if isinstance(node, sp.Pow) else node.args[0]
        for node in sp.preorder_traversal(source)
        if (isinstance(node, sp.Pow) and not node.base.is_Symbol and not node.base.is_number)
        or isinstance(node, sp.Function)
    ]
    for inner in inners:
        rate = sp.diff(inner, variable)
        if rate.is_number and rate == 1:
            continue
        if rate != 0 and compare(claimed * rate, expected) == "equal":
            return "differentiation", "chain_rule_inner_missing"

    if isinstance(source, sp.Mul):
        factors = [f for f in source.args if f.has(variable)]
        if len(factors) == 2:
            naive = sp.diff(factors[0], variable) * sp.diff(factors[1], variable)
            coefficient = source / (factors[0] * factors[1])
            if compare(coefficient * naive, claimed) == "equal":
                return "differentiation", "product_rule_not_applied"
        numerator, denominator = sp.fraction(source)
        if denominator.has(variable) and numerator.has(variable):
            naive = sp.diff(numerator, variable) / sp.diff(denominator, variable)
            if compare(naive, claimed) == "equal":
                return "differentiation", "quotient_rule_not_applied"

    # One standard function of the variable, perhaps with a number in front. Its rule is what's needed.
    _, function = source.as_independent(variable, as_Add=False)
    if isinstance(function, sp.Function) and function.args == (variable,) and function.func in _STANDARD_DERIVATIVES:
        return "differentiation", _STANDARD_DERIVATIVES[function.func]

    compared = _compare_terms(source, variable, claimed)
    return "differentiation", _kind_of_derivative_error(compared[0]) if compared else "incorrect_derivative"


_STANDARD_DERIVATIVES = {
    sp.sin: "derivative_of_sine",
    sp.cos: "derivative_of_cosine",
    sp.tan: "derivative_of_tangent",
    sp.exp: "derivative_of_exponential",
    sp.log: "derivative_of_logarithm",
}
_KIND_BY_PROBLEM: dict[TermProblem, str] = {
    "coefficient": "power_rule_coefficient",
    "exponent": "power_rule_exponent",
    "sign": "derivative_sign",
    "constant": "constant_not_dropped",
    "missing": "missing_term",
}


def _kind_of_derivative_error(terms: tuple[TermFact, ...]) -> str:
    wrong = [term.problem for term in terms if term.problem != "right"]
    if not wrong or "wrong_term" in wrong:
        return "incorrect_derivative"
    # A sum whose every sign flipped is one slip. Anything else that differs in two places is two.
    if len(wrong) > 1 and set(wrong) != {"sign"}:
        return "multiple_derivative_errors"
    # With only one term to differentiate, none of them can have been left out.
    if wrong[0] == "missing" and sum(term.problem != "constant" for term in terms) == 1:
        return "incorrect_derivative"
    return _KIND_BY_PROBLEM[wrong[0]]


def _compare_terms(
    source: sp.Expr, variable: sp.Symbol, claimed: sp.Expr
) -> tuple[tuple[TermFact, ...], tuple[str, ...]] | None:
    """Compare a polynomial's derivative with the answer, one written term at a time.

    Only for a sum of powers written out as such. Expanding (x + 1)^2 to compare terms would talk
    about an x^2 the student never wrote.
    """

    try:
        written = sp.Poly(source, variable)
        answer = sp.Poly(claimed, variable)
    except sp.PolynomialError:
        return None
    if sp.expand(source) != source or written.is_zero:
        return None

    remaining: dict[int, sp.Expr] = {power: coefficient for (power,), coefficient in answer.terms() if coefficient != 0}

    def term(coefficient: sp.Expr, power: int) -> str:
        return format_math(coefficient * variable**power)

    sources = sorted(((power, coefficient) for (power,), coefficient in written.terms()), reverse=True)
    constant = next((coefficient for power, coefficient in sources if power == 0), sp.Integer(0))
    moving = [(power, coefficient) for power, coefficient in sources if power > 0]
    wanted = {power: (power - 1, coefficient * power) for power, coefficient in moving}
    found: dict[int, TermFact] = {}

    def settle(power: int, problem: TermProblem, answered: str | None) -> None:
        new_power, value = wanted[power]
        source_term = term(written.coeff_monomial(variable**power), power)
        found[power] = TermFact(source_term, term(value, new_power), answered, problem)

    # A constant carried into the answer lands on the same power as the derivative of a linear term.
    carried = constant != 0 and remaining.get(0) is not None
    if carried:
        expected_constant = next((value for _, (new_power, value) in wanted.items() if new_power == 0), sp.Integer(0))
        if remaining[0] == expected_constant + constant:
            remaining[0] = expected_constant
            if remaining[0] == 0:
                del remaining[0]
        else:
            carried = False

    for power, (new_power, value) in wanted.items():
        if remaining.get(new_power) == value:
            del remaining[new_power]
            settle(power, "right", term(value, new_power))
    for power, (new_power, value) in wanted.items():
        if power in found or new_power not in remaining:
            continue
        answered = remaining.pop(new_power)
        settle(power, "sign" if answered == -value else "coefficient", term(answered, new_power))
    for power, (_, value) in wanted.items():
        if power in found:
            continue
        exponent_only = next((other for other, coefficient in remaining.items() if coefficient == value), None)
        if exponent_only is not None:
            del remaining[exponent_only]
            settle(power, "exponent", term(value, exponent_only))
    for power in wanted:
        if power in found:
            continue
        if remaining:
            other = max(remaining)
            settle(power, "wrong_term", term(remaining.pop(other), other))
        else:
            settle(power, "missing", None)

    facts = [found[power] for power, _ in moving]
    if constant != 0 and carried:
        facts.append(TermFact(format_math(constant), "0", format_math(constant), "constant"))
    return tuple(facts), tuple(term(coefficient, power) for power, coefficient in sorted(remaining.items(), reverse=True))


def _is_antiderivative_before_limits(left: Node, right: sp.Expr) -> bool:
    """`∫_1^3 2x dx = x^2`: the antiderivative, written down on the way to putting the limits in."""

    if left.kind != "integral" or len(left.args) != 3:
        return False
    variable = sp.Symbol(str(left.value), real=True)
    if variable not in right.free_symbols:
        return False
    try:
        return compare(sp.diff(right, variable), to_sympy(left.args[0])) == "equal"
    except MathParseError:
        return False


def _diagnose_identity(
    left: Node, right: Node, a: sp.Expr, b: sp.Expr, source: tuple[sp.Expr, sp.Symbol] | None
) -> tuple[str, str, sp.Expr]:
    """Name the kind of error. Purely descriptive: the verdict is already decided."""

    if contains_kind(left, "integral"):
        return "integration", "incorrect_definite_integral", a
    if source is not None:
        if b.free_symbols & {sp.Symbol(name, real=True) for name in ARBITRARY_CONSTANTS} - a.free_symbols:
            # d/dx(x^3) = 3x^2 + C: a derivative has one value, and there's nothing to be arbitrary.
            return "differentiation", "constant_added_to_derivative", a
        category, kind = _diagnose_derivative(source[0], source[1], b)
        return category, kind, a
    if contains_kind(left, "deriv") or contains_kind(right, "deriv"):
        return "differentiation", "incorrect_derivative", a
    if _loses_the_variable(a, b):
        return "algebra_like_terms", "lost_variable", sp.expand(a)
    if _renamed_letter(a, b) is not None:
        return "algebra_like_terms", VARIABLE_CHANGED, sp.expand(a)
    if _copied_term_changed_letter(left, right) is not None:
        return "algebra_like_terms", TERM_LETTER_CHANGED, sp.expand(a)
    if _copied_term_flipped_sign(left, right) is not None:
        return "algebra_sign", "sign_error", sp.expand(a)
    if _terms_left_out(left, right):
        return "algebra_like_terms", TERMS_LEFT_OUT, sp.expand(a)
    if not a.free_symbols and not b.free_symbols:
        return "arithmetic", "sign_error" if a == -b else "arithmetic_error", a
    if _term_sign_flipped(a, b):
        return "algebra_sign", "sign_error", sp.expand(a)
    if has_distribution(left) and _keeps_a_bracket(left, right):
        # The bracket is still there, so the slip was elsewhere, not in multiplying it out.
        return "algebra", "incorrect_simplification", a
    if has_distribution(left):
        if _has_positive_power_of_sum(left):
            return "algebra_expansion", "power_of_sum_expanded_termwise", sp.expand(a)
        if _has_power_of_sum(left):
            return "algebra", "incorrect_simplification", a
        return "algebra_expansion", "incorrect_distribution", sp.expand(a)
    if has_distribution(right):
        return "algebra_factorisation", "incorrect_factorisation", sp.factor(a)
    if left.kind == "add" and contains_kind(left, "div"):
        return "algebra_fractions", "fraction_addition", sp.cancel(sp.together(a))
    if left.kind == "add" and _has_variable_power_or_term(left):
        return "algebra_like_terms", "combining_like_terms", a
    if left.kind == "div" and all(part.kind == "pow" for part in left.args) and left.args[0].args[0] == left.args[1].args[0]:
        return "algebra_exponents", "dividing_powers", a
    if left.kind == "pow" and left.args[0].kind == "pow":
        return "algebra_exponents", "power_of_a_power", a
    if left.kind == "mul" and _powers_share_a_base(left):
        return "algebra_exponents", "exponent_rule", a
    # Nothing specific can be said, so a general hint is better than a wrong one.
    return "algebra", "incorrect_simplification", a


def _powers_share_a_base(node: Node) -> bool:
    bases = [factor.args[0] for factor in node.args if factor.kind == "pow"]
    return len(bases) >= 2 and any(base in bases[index + 1 :] for index, base in enumerate(bases))


def _brackets(node: Node) -> list[Node]:
    own = [child for child in node.args if child.kind == "add"] if node.kind in ("mul", "pow", "neg") else []
    return own + [bracket for child in node.args for bracket in _brackets(child)]


def _contains_node(node: Node, wanted: Node) -> bool:
    return node == wanted or any(_contains_node(child, wanted) for child in node.args)


def _keeps_a_bracket(left: Node, right: Node) -> bool:
    return any(_contains_node(right, bracket) for bracket in _brackets(left))


def _has_variable_power_or_term(node: Node) -> bool:
    return bool(_written_variables(node))


def _written_variables(node: Node) -> set[str]:
    own = {node.value} if node.kind == "var" else set()
    return own.union(*(_written_variables(child) for child in node.args)) if node.args else own


def _has_positive_power_of_sum(node: Node) -> bool:
    if node.kind == "pow" and node.args[0].kind == "add" and node.args[1].kind == "num":
        return True
    return any(_has_positive_power_of_sum(child) for child in node.args)


def _has_power_of_sum(node: Node) -> bool:
    if node.kind == "pow" and node.args[0].kind == "add":
        return True
    return any(_has_power_of_sum(child) for child in node.args)


def _loses_the_variable(a: sp.Expr, b: sp.Expr) -> bool:
    """3x + 2x and then 5: the number that goes in front of x, standing where 5x should be."""

    collapsed = sp.expand(a)
    coefficient, rest = collapsed.as_coeff_Mul()
    return bool(rest.free_symbols) and not b.free_symbols and not collapsed.is_Add and coefficient != 0 and b == coefficient


VARIABLE_CHANGED = "variable_changed"
TERM_LETTER_CHANGED = "term_letter_changed"


def _renamed_letter(a: sp.Expr, b: sp.Expr) -> tuple[str, str] | None:
    """`8a - 5a`, then `3x`: right but for its letter. (x, a) if putting a for x gives exactly the right value.

    Only a letter the answer has and the working doesn't, standing in for one the working has and the
    answer doesn't. If any other letter of the answer is wrong too, nothing is said about which.
    """

    old = {symbol.name: symbol for symbol in a.free_symbols}
    new = {symbol.name: symbol for symbol in b.free_symbols}
    added = [name for name in new if name not in old]
    missing = sorted(name for name in old if name not in new)
    if len(added) != 1 or not missing:
        return None
    for target in missing:
        if compare(b.subs(new[added[0]], old[target]), a) == "equal":
            return added[0], target
    return None


def _signed_terms(node: Node) -> list[tuple[int, Node, sp.Expr]]:
    if node.kind == "add":
        return [(sign, arg, sign * to_sympy(arg)) for sign, arg in zip(node.signs, node.args, strict=True)]
    return [(1, node, to_sympy(node))]


def _term_text(sign: int, node: Node) -> str:
    return ("-" if sign < 0 else "") + format_node(node)


def _match_terms(left: Node, right: Node) -> tuple[list[tuple[int, Node, sp.Expr]], list[int], list[int]] | None:
    """The terms of `left`, and which of its and `right`'s terms have no exact copy in the other."""

    if left.kind != "add":
        return None
    try:
        mine, theirs = _signed_terms(left), _signed_terms(right)
    except MathParseError:
        return None
    unmatched = list(range(len(theirs)))
    left_over: list[int] = []
    for index, (_, _, term) in enumerate(mine):
        found = next((j for j in unmatched if sp.expand(term - theirs[j][2]) == 0), None)
        if found is None:
            left_over.append(index)
        else:
            unmatched.remove(found)
    return mine, left_over, unmatched


def _copied_term_changed(left: Node, right: Node) -> tuple[str, str, str, str] | None:
    """`8a + 3b - 4a - 7b`, then `= 8a - 4a + 3b - 7a`: the terms moved, and -7b came out as -7a.

    Only when every term but one is the same, as written, and that one keeps its number and changes
    its letter (`letter`), or keeps its letter and changes its sign (`sign`). Then the line is the line
    above rearranged, with that one change, and nothing else could explain it. Gives what changed, the
    term, what it became, and the line with it put back, which is Quack's own text.
    """

    if right.kind != "add" or len(left.args) != len(right.args):
        return None
    matched = _match_terms(left, right)
    if matched is None:
        return None
    mine, left_over, unmatched = matched
    if len(left_over) != 1 or len(unmatched) != 1:
        return None
    sign, node, was = mine[left_over[0]]
    their_sign, their_node = right.signs[unmatched[0]], right.args[unmatched[0]]
    now = their_sign * to_sympy(their_node)
    if len(was.free_symbols) != 1 or len(now.free_symbols) != 1:
        return None
    (letter,), (other,) = was.free_symbols, now.free_symbols
    if letter != other and sign == their_sign and sp.expand(was.subs(letter, other) - now) == 0:
        kind = "letter"
    elif letter == other and sp.expand(was + now) == 0:
        kind = "sign"
    else:
        return None
    put_back, signs = list(right.args), list(right.signs)
    put_back[unmatched[0]], signs[unmatched[0]] = node, sign
    fixed = replace(right, args=tuple(put_back), signs=tuple(signs))
    return kind, _term_text(sign, node), _term_text(their_sign, their_node), format_node(fixed)


def _copied_term_changed_letter(left: Node, right: Node) -> tuple[str, str, str] | None:
    changed = _copied_term_changed(left, right)
    return changed[1:] if changed is not None and changed[0] == "letter" else None


def _copied_term_flipped_sign(left: Node, right: Node) -> tuple[str, str, str] | None:
    changed = _copied_term_changed(left, right)
    return changed[1:] if changed is not None and changed[0] == "sign" else None


TERMS_LEFT_OUT = "terms_left_out"


def _terms_left_out(left: Node, right: Node) -> list[str]:
    """`8a + 3b - 4a - 7b`, then `= 8a - 4a`: each term written is one above, and the rest have gone.

    Nothing was combined or changed, so the step can only have lost terms. The verifier holds this
    back as still being written when it's the page's last line. See _copied_part_way.
    """

    matched = _match_terms(left, right)
    if matched is None:
        return []
    mine, left_over, unmatched = matched
    if unmatched or not left_over or len(left_over) == len(mine):
        return []
    return [_term_text(mine[index][0], mine[index][1]) for index in left_over]


def _differs_only_in_case(a: sp.Expr, b: sp.Expr) -> bool:
    """`8a - 5a`, `= 3A`: a capital is most likely the same letter, written larger, so it's not a mistake."""

    names = {symbol.name for symbol in a.free_symbols}
    swaps = {
        symbol: sp.Symbol(symbol.name.swapcase(), real=True)
        for symbol in b.free_symbols
        if symbol.name not in names and symbol.name.swapcase() in names
    }
    return bool(swaps) and compare(b.subs(swaps), a) == "equal"


def _has_arbitrary_constant(claimed: sp.Expr, names: set[str]) -> bool:
    """Is one of these letters added on as a constant, rather than multiplying something?"""

    return any(sp.diff(claimed, sp.Symbol(name, real=True)).is_number for name in names)


def _rounds_at_every_sample(a: sp.Expr, b: sp.Expr) -> bool:
    points = _sample_points(sorted(a.free_symbols | b.free_symbols, key=str))
    values = [(_evaluate_at(a, point), _evaluate_at(b, point)) for point in points]
    known = [(one, other) for one, other in values if one is not None and other is not None]
    return bool(known) and all(abs(one - other) <= ROUNDING_TOLERANCE * max(abs(one), abs(other)) for one, other in known)


def _kind_of_integration_error(integrand: sp.Expr, variable: sp.Symbol, claimed: sp.Expr, expected: sp.Expr) -> str:
    """Name it only if the mathematics shows it. Otherwise it's an unclassified mismatch."""

    if not claimed.has(variable):
        return "integral_without_variable"
    if compare(claimed, sp.diff(integrand, variable)) == "equal":
        return "derivative_instead_of_integral"
    try:
        wanted, answer = sp.Poly(expected, variable), sp.Poly(claimed, variable)
    except sp.PolynomialError:
        return "incorrect_antiderivative"
    return "integral_coefficient" if wanted.degree() == answer.degree() else "integral_exponent"


def _check_antiderivative(left: Node, right: Node, line: int, from_line: int | None, wants_general: bool) -> StepResult:
    """`∫ f dx = F`. It's right if F differentiates to f, and nothing else about F matters.

    Leaving off + C still gives a true antiderivative, so that's a note for when the question asked
    for the general one, and never a mistake. C is only a constant if nothing else claims the letter.
    """

    operation = "integration"
    variable = sp.Symbol(left.value, real=True)
    try:
        integrand, claimed = to_sympy(left.args[0]), to_sympy(right)
    except MathParseError as exc:
        return StepResult("unsupported", operation, line, from_line, reason=exc.reason)
    previous, current = format_node(left), format_node(right)
    known = {str(symbol) for symbol in integrand.free_symbols} | {left.value}
    new_letters = {str(symbol) for symbol in claimed.free_symbols} - known
    if new_letters - ARBITRARY_CONSTANTS:
        # `∫ 2x dx = x^2 + y`: y might be anything, and might depend on x.
        return StepResult("uncertain", operation, line, from_line, reason="variables_changed")
    if _uses_numeric_trig(integrand) or _uses_numeric_trig(claimed):
        return StepResult("uncertain", operation, line, from_line, reason="trig_units_unclear")
    derivative = sp.diff(claimed, variable)
    verdict = compare(derivative, integrand)
    if verdict == "equal":
        complete = _has_arbitrary_constant(claimed, new_letters)
        assumption = "for positive values of what's inside ln" if claimed.has(sp.log) and not claimed.has(sp.Abs) else None
        return StepResult(
            "valid", operation, line, from_line, previous_step=previous, current_step=current,
            assumption=assumption, note=None if complete or not wants_general else "missing_constant",
        )
    if verdict == "domain_sensitive":
        return StepResult(
            "uncertain", operation, line, from_line, reason="domain_sensitive",
            evidence=f"{previous} and {current} agree only for some values",
        )
    if verdict == "unknown":
        return StepResult("uncertain", operation, line, from_line, reason="could_not_evaluate")
    if integrand.free_symbols and not integrand.has(variable) and claimed.free_symbols - {variable} - integrand.free_symbols:
        return StepResult("uncertain", operation, line, from_line, reason="variable_not_in_source")
    if integrand.free_symbols and not integrand.has(variable) and left.value not in {str(s) for s in claimed.free_symbols}:
        # ∫ u dx = u^2/2 + C: u may well depend on x, and calling that wrong would be a false answer.
        return StepResult("uncertain", operation, line, from_line, reason="variable_not_in_source")
    if has_decimal(right) and _rounds_at_every_sample(derivative, integrand):
        return StepResult("uncertain", operation, line, from_line, reason="possible_rounding")
    found = antiderivative(integrand, variable)
    if found is None:
        # It's wrong, but with nothing to say it should be, so nothing is said.
        return StepResult("unsupported", operation, line, from_line, reason="beyond_elementary_integration")
    expected = f"{format_math(found)} + C"
    return StepResult(
        "invalid", operation, line, from_line,
        error_category="integration",
        error_type=_kind_of_integration_error(integrand, variable, claimed, found),
        evidence=f"the derivative of {current} is {format_math(derivative)}, not {format_math(integrand)}",
        previous_step=previous,
        current_step=current,
        expected=expected,
        fingerprint=_fingerprint("integration", integrand, claimed, left.value),
        parsed_operation="integration",
    )


def _check_identity(
    left: Node,
    right: Node,
    line: int,
    from_line: int | None,
    source: tuple[Node, str] | None = None,
    asserted: bool = False,
    wants_general: bool = False,
    values: Values = (),
) -> StepResult:
    """Check a claim that two expressions are identically equal.

    `asserted`: the task on this line has said so. A letter disappearing could otherwise be a value
    being put in, but not when the question was to simplify.
    `values`: the question gave these letters values, so they're numbers here. See `_check_by_value`.
    """

    if values and source is None and _mentions_a_given(left, right, values):
        return _check_by_value(left, right, line, from_line, values)

    if left.kind == "integral" and len(left.args) == 1:
        if contains_kind(right, "integral"):
            return StepResult("uncertain", "integration", line, from_line, reason="integral_on_both_sides")
        return _check_antiderivative(left, right, line, from_line, wants_general)
    if right.kind == "integral" and len(right.args) == 1:
        return _check_antiderivative(right, left, line, from_line, wants_general)

    operation = (
        "integration" if contains_kind(left, "integral") or contains_kind(right, "integral")
        else "differentiation" if source or contains_kind(left, "deriv")
        else "expression_equivalence"
    )
    try:
        a, b = to_sympy(left), to_sympy(right)
        source_expr = (
            (to_sympy(source[0]), sp.Symbol(source[1], real=True)) if source is not None else None
        )
        if source_expr is None and left.kind == "deriv":
            source_expr = (to_sympy(left.args[0]), sp.Symbol(left.value, real=True))
    except MathParseError as exc:
        return StepResult("unsupported", operation, line, from_line, reason=exc.reason)

    if left.kind == "deriv" and _free_symbol_names(left.args[0]) and left.value not in _free_symbol_names(left.args[0]):
        # dy/dx of u^2: u may well depend on x, and calling that 0 would be a false answer.
        return StepResult("uncertain", operation, line, from_line, reason="variable_not_in_source")

    previous, current = format_node(left), format_node(right)
    if not a.free_symbols and not b.free_symbols and operation != "integration":
        operation = "arithmetic"
    verdict = compare(a, b)
    if verdict == "equal":
        cancels = _has_variable_denominator(left) or _has_variable_denominator(right)
        assumption = "where the denominator isn't zero" if cancels else None
        return StepResult(
            "valid", operation, line, from_line, previous_step=previous, current_step=current, assumption=assumption
        )
    if verdict == "domain_sensitive":
        return StepResult(
            "uncertain", operation, line, from_line,
            reason="domain_sensitive",
            evidence=f"{previous} and {current} agree only for some values",
        )
    if verdict == "unknown":
        return StepResult("uncertain", operation, line, from_line, reason="could_not_evaluate")
    if _differs_only_in_case(a, b):
        return StepResult("uncertain", operation, line, from_line, reason="letter_case")
    if not asserted and _variables_changed(left, right, a, b, source) and not _is_a_new_constant_on_a_derivative(left, a, b):
        # `2x + 3` then `7` is more likely a substitution than a mistake.
        return StepResult("uncertain", operation, line, from_line, reason="variables_changed")
    if a.has(sp.pi) != b.has(sp.pi) and (compare(a * 180 / sp.pi, b) == "equal" or compare(a, b * 180 / sp.pi) == "equal"):
        # pi/6 = 30 is an angle with its degree sign left off.
        return StepResult("uncertain", operation, line, from_line, reason="degrees_and_radians")
    if _depends_on_e_being_a_constant(left, right, source):
        return StepResult("uncertain", operation, line, from_line, reason="e_may_be_a_variable")
    if _is_possible_rounding(left, right, a, b):
        return StepResult("uncertain", operation, line, from_line, reason="possible_rounding")
    if _uses_numeric_trig(a) or _uses_numeric_trig(b):
        # sin(30) = 0.5 is right in degrees, and nothing written says which is meant.
        if _agrees_in_degrees(a, b):
            return StepResult("valid", operation, line, from_line, previous_step=previous, current_step=current)
        return StepResult("uncertain", operation, line, from_line, reason="trig_units_unclear")

    if _is_antiderivative_before_limits(left, b):
        return StepResult("uncertain", operation, line, from_line, reason="limits_still_to_come")

    category, error_type, expected = _diagnose_identity(left, right, a, b, source_expr)
    difference = sp.expand(a - b)
    evidence = f"expected {format_math(expected)}, got {current}; difference {format_math(difference)}"
    expected_text = format_math(expected)
    renamed = _renamed_letter(a, b) if error_type == VARIABLE_CHANGED else None
    copied = (
        _copied_term_changed_letter(left, right) if error_type == TERM_LETTER_CHANGED
        else _copied_term_flipped_sign(left, right) if error_type == "sign_error"
        else None
    )
    dropped = _terms_left_out(left, right) if error_type == TERMS_LEFT_OUT else []
    # Worded for the hints, which read it back. See hint_engine._copying_help.
    if renamed is not None:
        evidence = f"{current} has the letter {renamed[0]} where {previous} has {renamed[1]}"
    elif copied is not None:
        evidence = f"{copied[0]} became {copied[1]}"
        # The line as written, with that one term as it was: the rearranging itself was right.
        expected_text = copied[2]
    elif dropped:
        evidence = f"left out: {', '.join(dropped)}"
    compared = (
        _compare_terms(source_expr[0], source_expr[1], b)
        if source_expr is not None and error_type not in ("chain_rule_inner_missing", "product_rule_not_applied")
        else None
    )
    term_facts, extra_terms = compared or ((), ())
    return StepResult(
        "invalid",
        operation,
        line,
        from_line,
        error_category=category,
        error_type=error_type,
        evidence=evidence,
        previous_step=previous,
        current_step=current,
        expected=expected_text,
        fingerprint=_fingerprint(category, a, b, _written_form(source[0] if source else left)),
        parsed_operation=_parsed_operation(left, operation),
        term_facts=term_facts,
        extra_terms=extra_terms,
    )


Values = tuple[tuple[str, str], ...]


def _given(values: Values) -> dict[sp.Symbol, sp.Expr]:
    return {sp.Symbol(letter, real=True): to_sympy(parse_expression(value)) for letter, value in values}


def _mentions_a_given(left: Node, right: Node, values: Values) -> bool:
    if any(contains_kind(node, "deriv") or contains_kind(node, "integral") for node in (left, right)):
        return False
    letters = {letter for letter, _ in values}
    return bool(letters & (_written_variables(left) | _written_variables(right)))


def is_written_value(node: Node) -> bool:
    """What a student puts in for a letter: 4, -4, 1/2 or -1/2."""

    if node.kind == "neg":
        return is_written_value(node.args[0])
    if node.kind == "div":
        return all(child.kind == "num" for child in node.args)
    return node.kind == "num"


def _values_put_in(letter_form: Node, written: Node, letters: set[str]) -> list[tuple[str, Node]] | None:
    """What was put in for each letter, if `written` is `letter_form` with values in place of letters.

    `x^2 + 6x` and `(-4)^2 + 6(4)` give [(x, -4), (x, 4)]. A letter may also be left as it is. None if
    anything else differs, as then more has happened on the line than putting values in.
    """

    if letter_form.kind == "var" and letter_form.value in letters:
        if written == letter_form:
            return []
        return [(letter_form.value, written)] if is_written_value(written) else None
    if (
        letter_form.kind != written.kind
        or letter_form.value != written.value
        or letter_form.signs != written.signs
        or len(letter_form.args) != len(written.args)
    ):
        return None
    found: list[tuple[str, Node]] = []
    for mine, theirs in zip(letter_form.args, written.args, strict=True):
        inside = _values_put_in(mine, theirs, letters)
        if inside is None:
            return None
        found.extend(inside)
    return found


# A value put in with its sign lost, where the line's value is still right: (3)^2 for x^2 with x = -3.
SIGN_HIDDEN = "sign_lost_value_unchanged"


def _wrong_values(put_in: list[tuple[str, Node]], given: dict[sp.Symbol, sp.Expr]) -> list[tuple[str, Node]]:
    return [
        (letter, node) for letter, node in put_in
        if compare(to_sympy(node), given[sp.Symbol(letter, real=True)]) != "equal"
    ]


def _signs_hidden(left: Node, right: Node, letters: set[str], given: dict[sp.Symbol, sp.Expr]) -> list[tuple[str, Node]]:
    """The values put in that are the given's negative, when that's every value that's wrong.

    Only for a line that is the line above with values in place of letters, as then what was put in
    for each letter can be read off it. Anything else, like `9 - 9` or `3^2 - 9` under `(-3)^2 + 3(-3)`,
    is arithmetic, and `(-3)^2` and `3^2` are the same number.
    """

    put_in = _values_put_in(left, right, letters)
    wrong = _wrong_values(put_in or [], given)
    # With x = -2 and y = 2, `xy`, `= (2)(-2)` is the values written the other way round, which is right, and
    # `x + y`, `= 2 + (-2)` swapped them. Neither lost a sign, so a value another letter is given isn't one.
    if any(
        compare(to_sympy(node), value) == "equal"
        for letter, node in wrong
        for symbol, value in given.items()
        if symbol.name != letter
    ):
        return []
    if wrong and all(compare(to_sympy(node), -given[sp.Symbol(letter, real=True)]) == "equal" for letter, node in wrong):
        return wrong
    return []


def _first_term_put_in_wrongly(
    left: Node, right: Node, letters: set[str], given: dict[sp.Symbol, sp.Expr]
) -> tuple[Node, Node]:
    """The term with a value that isn't the question's, by what was put in rather than by its value."""

    if left.kind == "add" and right.kind == "add" and left.signs == right.signs and len(left.args) == len(right.args):
        for mine, theirs in zip(left.args, right.args, strict=True):
            if _wrong_values(_values_put_in(mine, theirs, letters) or [], given):
                return mine, theirs
    return left, right


def _puts_the_givens_in(ctx: _Context, wrong: Statement, stmt: Statement | None, values: Values) -> bool:
    """Is this line what the wrong one substituted, with the question's values put in, each as given?

    What puts right a sign hidden by a square: any true line below it is true whatever was put in.
    The letters it came from are on the wrong line itself, `x^2 + 3x = (3)^2 + 3(-3)`, or above it.
    """

    if stmt is None or not values or not stmt.parts:
        return False
    letters = {letter for letter, _ in values}
    # Anywhere on the line: `= (-3)^2 + 3(-3) = 0`, or `x^2 + 3x = (-3)^2 + 3(-3) = 0` written out again.
    sources = [*wrong.parts[:-1], *(ctx.prev.parts[-1:] if ctx.prev is not None else []), *stmt.parts[:-1]]
    try:
        given = _given(values)
        for target in stmt.parts:
            for source in sources:
                if source is target:
                    continue
                put_in = _values_put_in(source, target, letters)
                if put_in and not _wrong_values(put_in, given):
                    return True
    except MathParseError:
        return False
    return False


def _with_values_in(text: str, values: Values) -> str:
    """`x^2 + 6x` as it reads with each letter's value in brackets: `(-4)^2 + 6(-4)`. Quack's own text."""

    for letter, value in values:
        text = re.sub(rf"(?<![A-Za-z]){letter}(?![A-Za-z])", f"({value})", text)
    return text


def _first_term_that_differs(left: Node, right: Node, given: dict[sp.Symbol, sp.Expr]) -> tuple[Node, Node]:
    """The term of the working with the wrong value in it, or the whole line if it can't be split up."""

    if left.kind == "add" and right.kind == "add" and left.signs == right.signs and len(left.args) == len(right.args):
        for mine, theirs in zip(left.args, right.args, strict=True):
            if compare(to_sympy(mine).subs(given), to_sympy(theirs).subs(given)) != "equal":
                return mine, theirs
    return left, right


def _starts_like(letter_form: Node, written: Node, letters: set[str]) -> bool:
    """`5(3)` under `5x + 4`: the first terms with values put in, and the line not yet finished."""

    if letter_form.kind != "add" or len(letter_form.args) < 2:
        return False
    for count in range(1, len(letter_form.args)):
        if count == 1:
            head = letter_form.args[0] if letter_form.signs[0] > 0 else Node("neg", args=(letter_form.args[0],))
        else:
            head = Node("add", args=letter_form.args[:count], signs=letter_form.signs[:count])
        if _values_put_in(head, written, letters) is not None:
            return True
    return False


MAX_OCCURRENCES_TRIED = 8


def _a_sign_lost_gives(letter_form: Node, given: dict[sp.Symbol, sp.Expr], target: sp.Expr) -> bool:
    """Would the opposite of a value, put in for some of its letters, give exactly `target`?

    `3x - y` with y = -5 written out as `= 12 - 5`. The working changed shape on the way, so this goes by
    value: one of the ways a sign could be lost, and only those, gives what was written.
    """

    names = {str(symbol) for symbol in given}
    occurrences: list[sp.Symbol] = []

    def each(node: Node) -> Node:
        if node.kind == "var" and node.value in names:
            occurrences.append(sp.Symbol(node.value, real=True))
            return Node("var", value=f"{node.value}_{len(occurrences)}")
        return replace(node, args=tuple(each(child) for child in node.args))

    renamed = to_sympy(each(letter_form))
    if not occurrences or len(occurrences) > MAX_OCCURRENCES_TRIED:
        return False
    marks = [sp.Symbol(f"{symbol}_{index + 1}", real=True) for index, symbol in enumerate(occurrences)]
    for flips in range(1, 2 ** len(occurrences)):
        point = {}
        for index, (mark, symbol) in enumerate(zip(marks, occurrences, strict=True)):
            value = given[symbol]
            if flips >> index & 1:
                if value == 0:
                    break
                value = -value
            point[mark] = value
        else:
            if compare(renamed.subs(point), target) == "equal":
                return True
    return False


def _check_by_value(left: Node, right: Node, line: int, from_line: int | None, values: Values) -> StepResult:
    """A step under a question that gives its letters values: `x^2 + 6x`, `= (-4)^2 + 6(-4)` with x = -4.

    The letters stand for those numbers, so the two sides need the same value there, not to be the
    same expression. Putting the value in is the step being taken. A different value is a mistake, and
    when it comes from putting a value in, that's what it's called, with the term it's in.
    """

    given = _given(values)
    letters = {letter for letter, _ in values}
    try:
        a, b = to_sympy(left), to_sympy(right)
    except MathParseError as exc:
        return StepResult("unsupported", "substitution", line, from_line, reason=exc.reason)
    at_a, at_b = a.subs(given), b.subs(given)
    if at_a.free_symbols or at_b.free_symbols:
        # A letter the question didn't give a value. That's for the usual rules.
        return _check_identity(left, right, line, from_line)
    previous, current = format_node(left), format_node(right)
    verdict = compare(at_a, at_b)
    if verdict == "equal":
        hidden = _signs_hidden(left, right, letters, given)
        if hidden:
            # `x^2 + 3x`, `= (3)^2 + 3(-3)` with x = -3. The value is right, because squaring hides the
            # sign, and that's said. But 3 isn't what the question gave x, so the substitution wasn't done.
            letter = hidden[0][0]
            term, written_term = _first_term_put_in_wrongly(left, right, letters, given)
            return StepResult(
                "invalid", "substitution", line, from_line,
                error_category="substitution", error_type=SIGN_HIDDEN,
                evidence=(
                    f"with {letter} = {dict(values)[letter]}, {format_node(term)} became {format_node(written_term)}, "
                    f"not {_with_values_in(format_node(term), values)}"
                ),
                previous_step=previous, current_step=current, expected=_with_values_in(previous, values),
                fingerprint=_fingerprint("substitution", a, b, _written_form(left), _written_form(right)),
                parsed_operation="substitution",
            )
        return StepResult("valid", "substitution", line, from_line, previous_step=previous, current_step=current)
    if verdict != "different":
        return StepResult("uncertain", "substitution", line, from_line, reason="could_not_evaluate")
    if right.kind == "var" or left.kind == "var":
        # `x` on its own is a line being started, most likely the given, `x = -4`, half written.
        return StepResult("uncertain", "substitution", line, from_line, reason="letter_alone")
    if _starts_like(left, right, letters):
        # Half written: `5(3)` while `5(3) + 4` is on its way. Its value isn't the line's yet.
        return StepResult("uncertain", "substitution", line, from_line, reason="still_writing")
    put_in = _values_put_in(left, right, letters)
    wrong = [
        (letter, node) for letter, node in put_in or []
        if compare(to_sympy(node), given[sp.Symbol(letter, real=True)]) != "equal"
    ]
    if not wrong and _written_variables(right) & letters:
        # Still in letters, and not by putting a value in: `5x` while `5x + 4` is being written out.
        return StepResult("uncertain", "substitution", line, from_line, reason="letters_left_in")
    fingerprint = _fingerprint("substitution", a, b, _written_form(left))
    if wrong:
        letter, node = wrong[0]
        value = given[sp.Symbol(letter, real=True)]
        flipped = all(
            compare(to_sympy(written), -given[sp.Symbol(name, real=True)]) == "equal" for name, written in wrong
        )
        term, written_term = _first_term_that_differs(left, right, given)
        stated = dict(values)[letter]
        return StepResult(
            "invalid", "substitution", line, from_line,
            error_category="substitution",
            error_type="sign_lost_in_substitution" if flipped and value != 0 else "wrong_value_substituted",
            evidence=(
                f"with {letter} = {stated}, {format_node(term)} became {format_node(written_term)}, "
                f"not {_with_values_in(format_node(term), values)}"
            ),
            previous_step=previous, current_step=current, expected=_with_values_in(previous, values),
            fingerprint=fingerprint, parsed_operation="substitution",
        )
    if not (_written_variables(left) & letters):
        # The letters were already gone from the line above, so what's wrong is the arithmetic.
        return _check_identity(left, right, line, from_line)
    if _a_sign_lost_gives(left, given, at_b):
        term, written_term = _first_term_that_differs(left, right, given)
        letter = sorted(_written_variables(term) & letters or _written_variables(left) & letters)[0]
        return StepResult(
            "invalid", "substitution", line, from_line,
            error_category="substitution", error_type="sign_lost_in_substitution",
            evidence=(
                f"with {letter} = {dict(values)[letter]}, {format_node(term)} became {format_node(written_term)}, "
                f"not {_with_values_in(format_node(term), values)}"
            ),
            previous_step=previous, current_step=current, expected=_with_values_in(previous, values),
            fingerprint=fingerprint, parsed_operation="substitution",
        )
    stated = " and ".join(f"{letter} = {value}" for letter, value in values)
    return StepResult(
        "invalid", "substitution", line, from_line,
        error_category="substitution", error_type="incorrect_evaluation",
        evidence=f"with {stated}, {previous} is {format_math(at_a)}, not {format_math(at_b)}",
        previous_step=previous, current_step=current, expected=format_math(at_a),
        fingerprint=fingerprint, parsed_operation="substitution",
    )


GIVEN_PUT_RIGHT = "given_put_right"
# `x = 2, y = -5`, `x = 2 and y = -5`, or `x = 2 y = -5`, which would otherwise read as x = 2y = -5.
_GIVENS_APART = re.compile(r"\s*(?:,|;|&|\band\b)\s*|(?<=[\d)])\s+(?=[A-Za-z]\s*=)")


def _given_statements(text: str) -> list[Statement]:
    """Each value named on a line that names only values, `x = 3` or `x = 2, y = -5`, or nothing."""

    statements = []
    for part in (piece for piece in _GIVENS_APART.split(text.strip()) if piece):
        statement = _try_parse(part)
        if statement is None or statement.kind != "equality" or statement.continuation or len(statement.parts) != 2:
            return []
        if statement.parts[0].kind != "var" or not is_written_value(statement.parts[1]):
            return []
        statements.append(statement)
    return statements


def names_given_values(text: str, letters: set[str]) -> bool:
    """Is this line only values for these letters? Model text, parsed safely. See `_states_a_given`."""

    statements = _given_statements(text)
    return bool(statements) and all(statement.parts[0].value in letters for statement in statements)


def _states_a_given(stmt: Statement, line: LineInput) -> StepResult | None:
    """`x = 3` under `If x = 3, ...`: the question said so, and it isn't a step of the working.

    Saying a different value for that letter goes against the question, and that can be shown. Nothing
    is decided here about lines that do anything more than name one value.
    """

    if not line.values or stmt.kind != "equality" or stmt.continuation or len(stmt.parts) != 2:
        return None
    letter, value = stmt.parts
    given = dict(line.values)
    if letter.kind != "var" or letter.value not in given or not is_written_value(value):
        return None
    written = to_sympy(value)
    expected = to_sympy(parse_expression(given[letter.value]))
    stated = f"{letter.value} = {format_node(value)}"
    if compare(written, expected) == "equal":
        return StepResult("valid", "given", line.index, current_step=stated)
    return StepResult(
        "invalid", "substitution", line.index, line.index,
        error_category="substitution", error_type="given_value_changed",
        evidence=f"the question gives {letter.value} = {given[letter.value]}, not {format_node(value)}",
        previous_step=letter.value, current_step=format_node(value), expected=given[letter.value],
        fingerprint=_fingerprint("substitution", sp.Symbol(letter.value, real=True), written, "given"),
        parsed_operation="substitution",
    )


def _is_a_new_constant_on_a_derivative(left: Node, a: sp.Expr, b: sp.Expr) -> bool:
    """d/dx(x^3) = 2x + C. A derivative has one value, so C is no way out for it.

    `dy/dx = 6` with no C stays alone: that can be its value at a point, below `y = 9`.
    """

    if left.kind != "deriv":
        return False
    new = {str(symbol) for symbol in b.free_symbols - a.free_symbols} - _written_variables(left)
    return bool(new) and new <= ARBITRARY_CONSTANTS and not (a.free_symbols - b.free_symbols)


_OPERATION_BY_NODE = {"mul": "multiplication", "div": "division", "pow": "power"}


def _parsed_operation(left: Node, operation: str) -> str:
    """What the line does, from its own operators, so that no label can name the wrong one."""

    if operation in ("differentiation", "integration"):
        return operation
    if operation != "arithmetic":
        return "algebra"
    if left.kind == "add":
        if len(left.args) == 2 and all(_is_plain_number(part) for part in left.args):
            return "subtraction" if left.signs[1] < 0 else "addition"
        return "mixed"
    if left.kind in _OPERATION_BY_NODE and all(_is_plain_number(part) for part in left.args):
        return _OPERATION_BY_NODE[left.kind]
    return "mixed"


def _variables_changed(left: Node, right: Node, a: sp.Expr, b: sp.Expr, source: tuple[Node, str] | None) -> bool:
    """Has a variable appeared or gone in a way that suggests a new step, not a mistake?"""

    written = _written_variables(source[0] if source is not None else left)
    if contains_kind(left, "deriv") and source is None:
        written = _written_variables(left)
    claimed = {str(symbol) for symbol in b.free_symbols}
    if claimed - written:
        return True
    still_there = {str(symbol) for symbol in sp.expand(a).free_symbols}
    # The left side still really depends on it, yet the right side has dropped it.
    return bool(still_there - claimed)


def _e_as_variable(node: Node) -> Node:
    if node.kind == "const" and node.value == "e":
        return replace(node, kind="var")
    return replace(node, args=tuple(_e_as_variable(child) for child in node.args))


def _depends_on_e_being_a_constant(left: Node, right: Node, source: tuple[Node, str] | None) -> bool:
    """Is this only wrong if e is Euler's number? `2e` then `4` isn't if e is an unknown.

    `3e + 2e` then `6e` is wrong either way, and stays an error.
    """

    if source is not None or not (_mentions_e(left) or _mentions_e(right)):
        return False
    # e^x under an integral sign is the exponential function.
    if contains_kind(left, "integral") or contains_kind(right, "integral"):
        return False
    as_left, as_right = _e_as_variable(left), _e_as_variable(right)
    try:
        a, b = to_sympy(as_left), to_sympy(as_right)
    except MathParseError:
        return True
    return compare(a, b) != "different" or _variables_changed(as_left, as_right, a, b, None)


def _mentions_e(node: Node) -> bool:
    return (node.kind == "const" and node.value == "e") or any(_mentions_e(child) for child in node.args)


def _is_numeric_statement(stmt: Statement) -> bool:
    """No letters at all, not even an e that could be an unknown."""

    return not any(_written_variables(part) or _mentions_e(part) for part in stmt.parts)


def _has_nothing_to_work_out(prior: Statement) -> bool:
    """`d/dx(x^2) = 2x`, or `2(x+3) = 2x+6`, has no value to find or put in, so a sum worked out below
    it, `3 * 4 = 10`, is a sum. Below `y = 2x` or `x + 1 = 4`, it may be a value being tried."""

    if any(contains_kind(part, "deriv") or contains_kind(part, "integral") for part in prior.parts):
        return True
    return compare(to_sympy(prior.parts[0]), to_sympy(prior.parts[1])) == "equal"


def _could_follow_without_being_derived(prev: Statement, left: Node, right: Node) -> bool:
    """Can a numeric line after this equation be anything other than a false sum?

    Either the variable cancelled, as in `x + 1 = x + 2` then `1 = 2`, or the student is trying
    a value: `3(x + 1) = 12` then `3(2 + 1) = 12` is x = 2 put in, not a claim that 9 is 12.
    """

    try:
        lhs, rhs = to_sympy(prev.parts[0]), to_sympy(prev.parts[1])
        symbols = (lhs - rhs).free_symbols
        if len(symbols) != 1:
            return True
        return _is_trial_value(lhs, rhs, to_sympy(left), to_sympy(right), next(iter(symbols)))
    except MathParseError:
        return True


def _is_trial_value(lhs: sp.Expr, rhs: sp.Expr, left: sp.Expr, right: sp.Expr, variable: sp.Symbol) -> bool:
    # Either way round: x + 1 = 4 can be tried as 4 = 2 + 1.
    for first, second in ((left, right), (right, left)):
        candidates = _real_solutions(lhs, first, variable)
        if candidates is None or any(compare(rhs.subs(variable, value), second) == "equal" for value in candidates):
            return True
    return False


def _is_literal(node: Node) -> bool:
    if node.kind == "num":
        return True
    if node.kind == "neg":
        return _is_literal(node.args[0])
    if node.kind == "div":
        return all(child.kind == "num" for child in node.args)
    return False


def _is_solution_form(stmt: Statement) -> bool:
    return (
        stmt.kind == "equality"
        and len(stmt.parts) == 2
        and stmt.parts[0].kind == "var"
        and _is_literal(stmt.parts[1])
    )


def _is_equation(stmt: Statement | None) -> bool:
    return (
        stmt is not None
        and stmt.kind == "equality"
        and len(stmt.parts) == 2
        and stmt.precision is None
        and not any(contains_kind(part, "integral") for part in stmt.parts)
    )


def _real_solutions(
    lhs: sp.Expr, rhs: sp.Expr, variable: sp.Symbol
) -> list[sp.Expr] | None:
    """Exact real solutions of a rational equation, or None if not computable."""

    numerator, denominator = sp.fraction(sp.together(lhs - rhs))
    numerator, denominator = sp.expand(numerator), sp.expand(denominator)
    try:
        poly = sp.Poly(numerator, variable)
    except sp.PolynomialError:
        return None
    if not all(coeff.is_rational for coeff in poly.all_coeffs()) or poly.degree() > MAX_ROOT_DEGREE:
        return None
    if poly.is_zero:
        return None
    found = sp.roots(poly)
    if sum(found.values()) != poly.degree():
        return None
    real = []
    for root in found:
        if root.is_real is False:
            continue
        if denominator != 1 and denominator.subs(variable, root) == 0:
            continue
        real.append(root)
    return real


def _has_variable_denominator(node: Node) -> bool:
    """Checked on the written form, because SymPy cancels `x^2/x` before we can see it."""

    if node.kind == "div" and _free_symbol_names(node.args[1]):
        return True
    if node.kind == "pow" and node.args[1].kind == "neg" and _free_symbol_names(node.args[0]):
        return True
    return any(_has_variable_denominator(child) for child in node.args)


def _same_set(a: list[sp.Expr], b: list[sp.Expr]) -> tuple[bool, bool, bool]:
    def contains(group: list[sp.Expr], value: sp.Expr) -> bool:
        return any(sp.simplify(value - other) == 0 for other in group)

    a_in_b = all(contains(b, value) for value in a)
    b_in_a = all(contains(a, value) for value in b)
    return a_in_b and b_in_a, a_in_b, b_in_a


def _diagnose_equation(
    prev_lhs: sp.Expr, prev_rhs: sp.Expr, lhs: sp.Expr, rhs: sp.Expr
) -> str:
    before, after = sp.expand(prev_lhs - prev_rhs), sp.expand(lhs - rhs)
    # A term carried across `=` without changing sign shifts the equation by twice that term.
    shift = sp.expand(after - before)
    was_left, was_right = sp.Add.make_args(sp.expand(prev_lhs)), sp.Add.make_args(sp.expand(prev_rhs))
    now_left, now_right = sp.Add.make_args(sp.expand(lhs)), sp.Add.make_args(sp.expand(rhs))
    for terms, stayed, other_before, other_after in (
        (was_left, now_left, was_right, now_right),
        (was_right, now_right, was_left, now_left),
    ):
        # It must really have moved: gone from its side, and the other side different.
        moved = set(other_before) != set(other_after)
        if shift != 0 and moved and any(shift in (2 * term, -2 * term) and term not in stayed for term in terms):
            return "sign_when_moving_term"
    if prev_rhs.is_number and rhs.is_number and prev_lhs.is_Mul and lhs.is_Symbol:
        coefficient = prev_lhs.as_coeff_Mul()[0]
        if coefficient not in (0, 1) and rhs == prev_rhs * coefficient:
            return "inverse_of_division" if abs(coefficient) < 1 else "inverse_of_multiplication"
    return "solution_set_changed"


def _check_equation(
    prev: Statement, cur: Statement, line: int, from_line: int | None, continues: Continues
) -> StepResult:
    """Check that an equation step keeps the same solutions. Never compares as expressions."""

    operation = "equation_transformation"
    try:
        lhs_prev, rhs_prev = to_sympy(prev.parts[0]), to_sympy(prev.parts[1])
        if cur.kind == "solutions":
            lhs_cur, rhs_cur = sp.Symbol(cur.variable, real=True), sp.Integer(0)
            cur_values = [to_sympy(value) for value in cur.values]
        else:
            lhs_cur, rhs_cur = to_sympy(cur.parts[0]), to_sympy(cur.parts[1])
            cur_values = None
    except MathParseError as exc:
        return StepResult("unsupported", operation, line, from_line, reason=exc.reason)

    before = lhs_prev - rhs_prev
    previous = f"{format_node(prev.parts[0])} = {format_node(prev.parts[1])}"
    current = cur.text if cur_values is not None else f"{format_node(cur.parts[0])} = {format_node(cur.parts[1])}"

    if cur_values is None:
        after = lhs_cur - rhs_cur
        if after != 0 and not after.is_number:
            ratio = sp.cancel(sp.together(before / after))
            if ratio.is_number and ratio != 0:
                return StepResult("valid", operation, line, from_line, previous_step=previous, current_step=current)
        symbols = before.free_symbols | after.free_symbols
    else:
        after = None
        symbols = before.free_symbols | {sp.Symbol(cur.variable, real=True)}

    if not symbols and after is not None:
        return _check_cancelled_equation(before, after, previous, current, line, from_line, continues)
    if len(symbols) != 1:
        return StepResult("uncertain", operation, line, from_line, reason="multiple_variables")
    variable = next(iter(symbols))

    before_roots = _real_solutions(lhs_prev, rhs_prev, variable)
    if before_roots is None:
        return StepResult("unsupported", operation, line, from_line, reason="not_a_rational_equation")
    if cur_values is not None:
        after_roots = cur_values
    else:
        solved_roots = _real_solutions(lhs_cur, rhs_cur, variable)
        if solved_roots is None:
            return StepResult("unsupported", operation, line, from_line, reason="not_a_rational_equation")
        after_roots = solved_roots

    same, after_within_before, before_within_after = _same_set(after_roots, before_roots)
    if same:
        return StepResult("valid", operation, line, from_line, previous_step=previous, current_step=current)
    places = max(_decimal_places(part) for part in (*cur.parts, *cur.values))
    from_decimals = any(has_decimal(part) for part in prev.parts)
    rounded_input = any(_is_rounded_input(part) for part in prev.parts)
    if after_roots and all(
        any(
            root != written
            # `=` means equal. 3x = 10 then x = 3 is false, and so is 2x = 7 then x = 3 or x = 3.3.
            # A whole number is only a rounded answer if the working was in decimals to begin with.
            and (
                rounded_input
                or (places > 0 and not _can_be_written_out(root))
                or (from_decimals and (places > 0 or not _could_just_be_written(root)))
            )
            and _rounds_to(root, written, places)
            for root in before_roots
        )
        or any(
            # F = 23.7 * 52.1, then F = 1230.
            from_decimals and _rounds_to_tens(root, written)
            for root in before_roots
        )
        for written in after_roots
    ):
        return StepResult("uncertain", operation, line, from_line, reason="possible_rounding")
    if any(_has_variable_denominator(part) for part in (*prev.parts, *cur.parts)):
        # Clearing denominators legitimately produces roots the original excludes. The
        # student may be about to reject them.
        return StepResult("uncertain", operation, line, from_line, reason="denominator_domain")
    same_degree = cur_values is None and _same_degree(before, after, variable)
    if ((after_within_before and after_roots) or (before_within_after and before_roots)) and not same_degree:
        # Writing one root at a time, dividing by a variable, or squaring. We do not
        # know which, and "you lost a root" is too confident a claim to quack about. None of
        # those keep the degree, so an equal-degree step that isn't a multiple is simply wrong.
        return StepResult("uncertain", operation, line, from_line, reason="solution_subset")
    if not _is_related_step(prev, cur, before, after, variable):
        return StepResult("uncertain", operation, line, from_line, reason="transition_unclear")
    if continues != "yes":
        return StepResult("uncertain", operation, line, from_line, reason="transition_unclear")

    error_type = (
        _diagnose_equation(lhs_prev, rhs_prev, lhs_cur, rhs_cur)
        if cur_values is None
        else "solution_set_changed"
    )
    expected = ", ".join(sorted(f"{variable} = {format_math(root)}" for root in before_roots))
    return StepResult(
        "invalid",
        operation,
        line,
        from_line,
        error_category="equation_solving",
        error_type=error_type,
        evidence=f"solutions were {before_roots}, now {after_roots}",
        previous_step=previous,
        current_step=current,
        expected=expected or "no real solutions",
        fingerprint=_fingerprint(
            "equation_solving",
            _canonical_equation(before),
            _canonical_equation(after) if after is not None else sp.Tuple(*(cur_values or [])),
        ),
    )


def _canonical_equation(difference: sp.Expr) -> sp.Expr:
    """The same equation however it's written, so that 4 = 2x and 2x = 4 are one mistake."""

    try:
        return sp.Poly(sp.expand(difference)).monic().as_expr()
    except (sp.PolynomialError, sp.polys.polyerrors.GeneratorsNeeded):
        return difference


def _check_cancelled_equation(
    before: sp.Expr, after: sp.Expr, previous: str, current: str, line: int, from_line: int | None, continues: Continues
) -> StepResult:
    """The variable has gone from both lines, so each is true for every x, or for none."""

    operation = "equation_transformation"
    if before.is_zero == after.is_zero:
        # `x + 1 = x + 2` then `1 = 2` finds there are no solutions, which is correct working.
        # `1 = 3` reaches the same conclusion by a sum we can't follow, so that's left alone.
        if before.is_zero or abs(before) == abs(after):
            return StepResult("valid", operation, line, from_line, previous_step=previous, current_step=current)
        return StepResult("uncertain", operation, line, from_line, reason="transition_unclear")
    if continues != "yes":
        return StepResult("uncertain", operation, line, from_line, reason="transition_unclear")
    expected = "all real numbers" if before.is_zero else "no real solutions"
    return StepResult(
        "invalid",
        operation,
        line,
        from_line,
        error_category="equation_solving",
        error_type="solution_set_changed",
        evidence=f"the equation holds for {expected}, but {current} says otherwise",
        previous_step=previous,
        current_step=current,
        expected=expected,
        fingerprint=_fingerprint("equation_solving", before, after),
    )


def _same_degree(before: sp.Expr, after: sp.Expr | None, variable: sp.Symbol) -> bool:
    if after is None:
        return False
    try:
        first = sp.Poly(sp.fraction(sp.together(before))[0], variable).degree()
        second = sp.Poly(sp.fraction(sp.together(after))[0], variable).degree()
    except sp.PolynomialError:
        return False
    return first == second and first > 0


def _is_related_step(
    prev: Statement, cur: Statement, before: sp.Expr, after: sp.Expr | None, variable: sp.Symbol
) -> bool:
    """Does this equation look like the next step of that one, or a different exercise?

    The model's "continues" label is only a hint and it can be wrong. `2x = 4` then `3x = 9` are
    two exercises, whatever it says, and calling that a mistake would be a false alarm.
    """

    if after is None or _is_solution_form(cur):
        return True
    if _is_solution_form(prev) or _is_numeric_statement(prev):
        # The last problem ended with an answer, so another equation starts a new one.
        return False
    if _works_out_one_side(prev, cur):
        return True
    if not any(has_distribution(part) for part in (*prev.parts, *cur.parts)) and _same_shape(prev, cur):
        # `x + 1 = 3` then `x + 2 = 7`: nothing has been done to it, so it's another exercise.
        return False
    if after.is_number:
        # `2x = 4` then `3 = 5` claims to follow, and it can't, unless it's a value being tried.
        lhs, rhs = to_sympy(prev.parts[0]), to_sympy(prev.parts[1])
        return not _is_trial_value(lhs, rhs, to_sympy(cur.parts[0]), to_sympy(cur.parts[1]), variable)
    shares_a_term = any(not term.is_number for term in _equation_terms(prev) & _equation_terms(cur))
    # `2(x + 1) = 6` then `3(x + 2) = 15`: brackets in both say nothing about one following the other.
    has_brackets = [any(has_distribution(part) for part in stmt.parts) for stmt in (prev, cur)]
    if all(has_brackets) and not _same_brackets(prev, cur):
        return False
    if all(has_brackets) or shares_a_term:
        return True
    # `2(x + 1) = 6` then `2x + 1 = 6` kept a side. Then `x + 5 = 9` hasn't kept or divided anything.
    if any(has_brackets) and any(
        compare(to_sympy(was), to_sympy(now)) == "equal" for was, now in zip(prev.parts, cur.parts, strict=False)
    ):
        return True
    if _collects_terms(prev, cur, before, after, variable):
        return True
    # Dividing through: 2x + 4 = 10 then x + 2 = 6.
    try:
        ratio = sp.Poly(before, variable).LC() / sp.Poly(after, variable).LC()
    except sp.PolynomialError:
        return False
    # Multiplying through: x/2 + 1 = 4 then x + 1 = 8.
    has_fraction = any(contains_kind(part, "div") for part in prev.parts)
    if has_fraction and ratio != 0 and (1 / ratio).is_integer and abs(1 / ratio) > 1:
        return True
    # A side has to have been divided, or `4x + 2 = 10` then `2x + 3 = 9` is just another equation.
    return bool(ratio.is_integer) and abs(ratio) > 1 and _a_side_is_divided_by(prev, cur, ratio)


def _same_brackets(prev: Statement, cur: Statement) -> bool:
    """Whichever bracket a line has is still there on the next: `2(x + 1) = 6` then `2(x + 1) = 7`."""

    def brackets(node: Node) -> set[sp.Expr]:
        has_any = node.kind in ("mul", "pow")
        found = {sp.expand(to_sympy(child)) for child in node.args if child.kind == "add"} if has_any else set()
        return found.union(*(brackets(child) for child in node.args))

    def on_the_line(stmt: Statement) -> set[sp.Expr]:
        return set().union(*(brackets(part) for part in stmt.parts))

    return bool(on_the_line(prev) & on_the_line(cur))


def _a_side_is_divided_by(prev: Statement, cur: Statement, ratio: sp.Expr) -> bool:
    return any(
        compare(to_sympy(was) / ratio, to_sympy(now)) == "equal"
        for was, now in zip(prev.parts, cur.parts, strict=False)
    )


def _works_out_one_side(prev: Statement, cur: Statement) -> bool:
    """`a^2 = 100 - 36` then `a^2 = 74`: one side kept, and the sum on the other worked out."""

    for same, other in ((0, 1), (1, 0)):
        if prev.parts[same] == cur.parts[same] and _free_symbol_names(prev.parts[same]):
            # x = 5 + 3 then x = 5 - 3 is the other root.
            if prev.parts[same].kind == "var" and not _is_literal(cur.parts[other]):
                continue
            was, now = prev.parts[other], cur.parts[other]
            if not _free_symbol_names(was) and not _free_symbol_names(now) and not _is_literal(was):
                return True
    return False


def _collects_terms(prev: Statement, cur: Statement, before: sp.Expr, after: sp.Expr, variable: sp.Symbol) -> bool:
    """`5x - 2 = 3x + 8` then `2x = 6`, or `4x + 1 = 2x + 7` then `6x = 6`: the terms were gathered up."""

    lhs, rhs = to_sympy(prev.parts[0]), to_sympy(prev.parts[1])
    if _diagnose_equation(lhs, rhs, to_sympy(cur.parts[0]), to_sympy(cur.parts[1])) == "sign_when_moving_term":
        return True
    if len(_equation_terms(cur)) >= len(_equation_terms(prev)):
        return False
    try:
        return sp.Poly(before, variable).LC() == sp.Poly(after, variable).LC()
    except sp.PolynomialError:
        return False


def _same_shape(prev: Statement, cur: Statement) -> bool:
    def shape(part: Node) -> tuple[frozenset[sp.Expr], int]:
        terms = sp.Add.make_args(sp.expand(to_sympy(part)))
        return frozenset(term for term in terms if not term.is_number), sum(term.is_number for term in terms)

    return [shape(part) for part in prev.parts] == [shape(part) for part in cur.parts]


def _equation_terms(stmt: Statement) -> set[sp.Expr]:
    return {term for part in stmt.parts for term in sp.Add.make_args(sp.expand(to_sympy(part)))}


def _leading_terms_agree(a: sp.Expr, b: sp.Expr) -> bool:
    """Do two expressions look like two forms of one thing, rather than two different things?"""

    expanded_a, expanded_b = sp.expand(a), sp.expand(b)
    symbols = sorted(expanded_a.free_symbols | expanded_b.free_symbols, key=str)
    if not symbols:
        return True
    try:
        first, second = sp.Poly(expanded_a, *symbols), sp.Poly(expanded_b, *symbols)
    except sp.PolynomialError:
        return False
    if first.is_zero or second.is_zero:
        return False
    if first.LT() == second.LT():
        return True
    # x^2 * x^3 = x^6, or 3x + 4x = 8x: a single term on each side, so nothing is being solved.
    return len(first.terms()) == 1 and len(second.terms()) == 1


def _intent_of(ctx: _Context, stmt: Statement, line: LineInput) -> str:
    """The student or the question says so, then the page says so, then the student's setting says so.

    Never the model's `operation` label, which is a guess about the same line.
    """

    if line.intent != "auto":
        return line.intent
    if stmt.task:
        return stmt.task
    # `Simplify`, `3x + 2x = 5x`, `4x + x = 6x` is the same again. `2(x + 1) = 6` has no letter left to
    # keep, so it may just as well be the next exercise, and the heading doesn't say it isn't.
    if ctx.task and not (ctx.task_answered and not _free_symbol_names(stmt.parts[-1])):
        return ctx.task
    return line.page_intent


def _is_identity_claim(left: Node, right: Node, line: LineInput, prior: Statement | None, intent: str = "auto") -> bool:
    # "Expand 3(x + 5)", answered 3x + 16, can't be an exercise in solving for x. It's the one case
    # where the question is known, rather than guessed at from how the line looks.
    if line.posed:
        return True
    if intent != "auto":
        try:
            a, b = to_sympy(left), to_sympy(right)
        except MathParseError:
            a, b = None, None
        if intent == "solve" and a is not None and b is not None and (a.free_symbols or b.free_symbols):
            return False
        # `x = 3` under "Simplify" isn't a claim about x. There has to be something to simplify.
        has_work_to_do = a is not None and (
            _is_unsimplified(left, a) or _is_unsimplified(right, b) or _has_a_fraction_to_simplify(left, a)
        )
        if intent in INTENTS_THAT_CLAIM_AN_IDENTITY and has_work_to_do:
            return True
    return _looks_like_identity_claim(left, right, line, prior)


def _looks_like_identity_claim(left: Node, right: Node, line: LineInput, prior: Statement | None) -> bool:
    """Decide whether `A = B` claims A and B are the same, rather than posing an equation."""

    if contains_kind(left, "deriv") or contains_kind(right, "deriv"):
        return True
    # ∫ on a line can only be saying what the integral comes to.
    if contains_kind(left, "integral") or contains_kind(right, "integral"):
        return True
    try:
        a, b = to_sympy(left), to_sympy(right)
    except MathParseError:
        return False
    # `1 + 1 = 2` poses nothing to solve, so what comes after it isn't a step of solving it.
    prior_equation = prior is not None and _is_equation(prior) and not _is_numeric_statement(prior)
    if not a.free_symbols and not b.free_symbols:
        # After an equation, `1 = 2` is a result (no solutions) rather than a sum to check.
        if not prior_equation:
            return True
        if (prior is not None and _has_nothing_to_work_out(prior)
                and left.kind in ("add", "mul", "div", "pow") and _is_literal(right)):
            return True
        return prior is not None and line.continues == "no" and not _could_follow_without_being_derived(prior, left, right)
    if (prior_equation and line.continues != "no") or line.operation not in SIMPLIFY_OPERATIONS:
        return False
    if sp.expand(a).free_symbols != b.free_symbols:
        return False
    # A plain equation such as `x + 3 = 2x + 1` has nothing to simplify on either side, and a
    # wrong label on `3(x + 1) = 2x + 5` must not turn it into an accusation.
    if not (_is_unsimplified(left, a) or _is_unsimplified(right, b)):
        return False
    # (x + 1)^2 = x^2 + 5 is an exercise, solved by x = 2. Only the label says otherwise, and the
    # first line of working often carries one. A claim that fails for all but x = 0 isn't posed.
    symbols = a.free_symbols | b.free_symbols
    # (x + 3)^2 = (x - 3)^2 has brackets on both sides, so nothing has been multiplied out.
    if has_distribution(left) and has_distribution(right):
        return False
    # x^2 * x^3 = x^6 has a single term each side, so it's a claim about powers and not an exercise.
    one_term_each = len(sp.Add.make_args(sp.expand(a))) == 1 and len(sp.Add.make_args(sp.expand(b))) == 1
    if len(symbols) == 1 and not one_term_each:
        roots = _real_solutions(a, b, next(iter(symbols)))
        if roots and any(root != 0 for root in roots):
            return False
    return _leading_terms_agree(a, b)


def _has_power_of_product(node: Node) -> bool:
    own = node.kind == "pow" and node.args[0].kind in ("mul", "pow")
    return own or any(_has_power_of_product(child) for child in node.args)


def _has_a_fraction_to_simplify(node: Node, expr: sp.Expr) -> bool:
    """`(x^2 - 1)/(x - 1)`, or `1/x + 1/y`. Not `1/x`, or `x/2`, where there's nothing to do."""

    denominator_has_a_letter = bool(sp.fraction(sp.together(expr))[1].free_symbols)
    has_a_top_to_work_with = node.kind == "div" and bool(_free_symbol_names(node.args[0]))
    return denominator_has_a_letter and (node.kind == "add" or has_a_top_to_work_with)


def _is_unsimplified(node: Node, expr: sp.Expr) -> bool:
    if has_distribution(node) or _has_power_of_product(node):
        return True
    canonical = sum(1 for atom in sp.preorder_traversal(sp.expand(expr)) if not atom.args)
    return leaf_count(node) > canonical


def _adjacent(parts: tuple[Node, ...], line: int, values: Values = ()) -> list[StepResult]:
    results = []
    for a, b in zip(parts, parts[1:], strict=False):
        result = _check_identity(a, b, line, line, values=values)
        # 5 + 3 = 8 - 2 = 6 is a running total. False as written, but no slip in the working.
        if result.status == "invalid" and _continues_a_running_total(a, b):
            result = _abstain(result, "running_total")
        results.append(result)
    return results


def _continues_a_running_total(a: Node, b: Node) -> bool:
    # An indefinite integral has no one value, so there's no total for it to be running.
    if contains_kind(a, "integral") or contains_kind(b, "integral"):
        return False
    if b.kind not in ("add", "mul", "div", "pow") or to_sympy(a).free_symbols or to_sympy(b).free_symbols:
        return False
    return compare(to_sympy(a), to_sympy(b.args[0])) == "equal"


def _carries_a_total_down(prev: Statement | None, left: Node, right: Node, line: LineInput) -> bool:
    """`12 / 4 = 3`, then `+ 2 = 5`: the 3 was carried down. `+ 2 = 6` is wrong on either reading."""

    if prev is None or line.posed or line.continues == "no" or not line.math.lstrip().startswith(("+", "-", "−")):
        return False
    carried, added, total = to_sympy(prev.parts[-1]), to_sympy(left), to_sympy(right)
    if carried.free_symbols or added.free_symbols or total.free_symbols:
        return False
    return compare(carried + added, total) == "equal"


def _could_be_either(left: Node, right: Node, prior: Statement | None, line: LineInput) -> bool:
    """`3x + 2x = 5`: a true equation with x = 1, or like terms that lost their x. Only the student knows.

    That needs the number to be exactly what goes in front of the variable. `3x + 2x = 7` is an
    equation, whatever else it could be. And below another equation, it's the next step of that.
    """

    if _is_equation(prior) and line.continues != "no":
        return False
    try:
        a, b = to_sympy(left), to_sympy(right)
    except MathParseError:
        return False
    return _is_unsimplified(left, a) and _loses_the_variable(a, b)


def _evaluate_line(ctx: _Context, stmt: Statement, line: LineInput) -> list[StepResult]:
    from_line = ctx.prev_line.index if ctx.prev_line else None
    prev = ctx.prev
    intent = _intent_of(ctx, stmt, line)
    asserted = line.posed or intent in INTENTS_THAT_CLAIM_AN_IDENTITY
    wants_general = line.posed or intent == "integrate"

    if stmt.kind == "approximation" or stmt.precision is not None:
        if not stmt.continuation:
            return [_check_rounding(stmt.parts[0], stmt.parts[1], stmt.precision, line.index, line.index)]
        if prev is None or not prev.parts:
            return [StepResult("uncertain", "approximation", line.index, reason="dangling_equals")]
        # `3x = 10`, `≈ 3.3`: shorthand for x, not a claim about 10.
        if _is_equation(prev) and not _is_numeric_statement(prev):
            return [StepResult("uncertain", "approximation", line.index, from_line, reason="continues_something_earlier")]
        return [_check_rounding(prev.parts[-1], stmt.parts[0], stmt.precision, line.index, from_line)]

    if stmt.continuation:
        if prev is None or not prev.parts:
            return [StepResult("uncertain", "expression_equivalence", line.index, reason="dangling_equals")]
        # `3x + 2x`, `= 5`: an expression was being worked out, with no equation in sight. Unless x has a
        # value written above it, the 5 can only be what the like terms came to.
        losing_it = False
        if prev.kind == "expression" and intent != "solve" and not contains_kind(prev.parts[-1], "integral"):
            try:
                letters = _written_variables(prev.parts[-1])
                in_an_equation = any(letters & _written_variables(part) for eq in ctx.equations for part in eq.parts)
                could_have_a_value = ctx.unread_above or in_an_equation or bool(letters & ctx.valued)
                losing_it = (
                    not could_have_a_value
                    and _is_unsimplified(prev.parts[-1], to_sympy(prev.parts[-1]))
                    and _loses_the_variable(to_sympy(prev.parts[-1]), to_sympy(stmt.parts[0]))
                )
            except MathParseError:
                losing_it = False
        continued = _check_identity(
            prev.parts[-1], stmt.parts[0], line.index, from_line,
            asserted=asserted or losing_it, wants_general=wants_general, values=line.values,
        )
        # `A = lw`, `l = 3`, `= 12`: the = may belong to the formula, not to the value just given.
        # `2x = 10`, `= 5`: it's shorthand for x = 5, not a claim that 10 is 5.
        shorthand = not _is_numeric_statement(prev) and _is_equation(prev) and _is_literal(prev.parts[-1])
        if continued.status == "invalid" and (_is_solution_form(prev) or shorthand):
            continued = _abstain(continued, "continues_something_earlier")
        elif continued.status == "invalid" and _continues_a_running_total(prev.parts[-1], stmt.parts[0]):
            continued = _abstain(continued, "running_total")
        return [continued, *_adjacent(stmt.parts, line.index, line.values)]

    if stmt.kind == "definition":
        return _adjacent(stmt.parts, line.index, line.values)

    if stmt.kind == "derivative_of":
        source = ctx.definitions.get(stmt.name)
        # A definition from another exercise says nothing about this derivative.
        if source is None or line.continues == "no":
            return [StepResult("uncertain", "differentiation", line.index, reason="no_source_for_derivative")]
        variable = stmt.variable
        if not variable:
            symbols = _free_symbol_names(source)
            # y' for y = 2a: a is more likely a constant than what's being differentiated by.
            if len(symbols) != 1 or symbols[0] in LIKELY_CONSTANTS:
                return [StepResult("uncertain", "differentiation", line.index, reason="unclear_variable")]
            variable = symbols[0]
        deriv = Node("deriv", args=(source,), value=variable)
        claimed = _check_identity(deriv, stmt.parts[0], line.index, from_line, source=(source, variable))
        # y' = 3x^2 then y' = 6x is most likely a y'' with a stroke lost.
        if claimed.status == "invalid" and prev is not None and prev.kind == "derivative_of" and prev.name == stmt.name:
            claimed = _abstain(claimed, "may_be_a_higher_derivative")
        return [claimed, *_adjacent(stmt.parts, line.index)]

    if stmt.kind == "solutions":
        if ctx.anchor is None or line.continues == "no":
            return []
        return [_check_against_equation(ctx, ctx.anchor, stmt, line, ctx.anchor_line)]

    if stmt.kind == "expression":
        if prev is None or line.continues == "no":
            return []
        if line.continues == "unknown" or _is_equation(prev) or not prev.parts:
            return [StepResult("uncertain", "expression_equivalence", line.index, from_line, reason="transition_unclear")]
        # `2(x+3)`, `2x+6`, `2(x-1)`: that step was done, and nobody un-simplifies on the way down.
        new_exercise = ctx.expression_run >= 2 and has_distribution(stmt.parts[0]) and not has_distribution(prev.parts[-1])
        return [_check_unmarked(prev.parts[-1], stmt.parts[0], line, from_line, prev.continuation or new_exercise)]

    if len(stmt.parts) >= 3:
        return _adjacent(stmt.parts, line.index, line.values)

    left, right = stmt.parts
    if _is_identity_claim(left, right, line, prev, intent):
        checked = _check_identity(
            left, right, line.index, line.index, asserted=asserted, wants_general=wants_general, values=line.values
        )
        # Said only by what's around the line, and not by anything on it or the student, about this line.
        by_the_line = line.posed or line.intent != "auto" or bool(stmt.task)
        by_context = not by_the_line and not _looks_like_identity_claim(left, right, line, prev)
        # 5 + 3 = 8 - 2, on the way to 6, however the line breaks.
        if checked.status == "invalid" and not line.posed and _continues_a_running_total(left, right):
            return [_abstain(checked, "running_total")]
        if checked.status == "invalid" and _carries_a_total_down(prev, left, right, line):
            return [_abstain(checked, "running_total")]
        return [replace(checked, intent_from_context=by_context)]
    if _has_bare_e(left, right):
        return [StepResult("uncertain", "notation", line.index, reason="e_may_be_a_variable")]
    if intent == "auto" and _could_be_either(left, right, prev, line):
        return [
            StepResult("uncertain", "task_intent", line.index, from_line, reason="ambiguous_intent", current_step=stmt.text)
        ]

    if line.continues == "no":
        return []

    if _is_solution_form(stmt):
        if ctx.anchor is None:
            return []
        # A further value below a solution has nothing written to follow from: x = 2 or x = 3,
        # then x = 0, may be a value being tried, or another exercise.
        if prev is not None and (prev.kind == "solutions" or _is_solution_form(prev)):
            checked = _check_against_equation(ctx, ctx.anchor, stmt, line, ctx.anchor_line)
            return [_abstain(checked, "value_after_a_solution") if checked.status == "invalid" else checked]
        return [_check_against_equation(ctx, ctx.anchor, stmt, line, ctx.anchor_line)]

    if _is_equation(prev):
        assert prev is not None
        if (
            prev.parts[0] == left
            and left.kind == "var"
            and not _mentions(prev.parts[1], left.value)
            and not _mentions(right, left.value)
            and line.operation in SIMPLIFY_OPERATIONS
            and line.continues == "yes"
            and _is_unsimplified(prev.parts[1], to_sympy(prev.parts[1]))
            and not _is_unsimplified(right, to_sympy(right))
        ):
            return [_check_identity(prev.parts[1], right, line.index, from_line)]
        return [_check_against_equation(ctx, prev, stmt, line, from_line)]

    return []


def _check_against_equation(
    ctx: _Context, equation: Statement, stmt: Statement, line: LineInput, from_line: int | None
) -> StepResult:
    result = _check_equation(equation, stmt, line.index, from_line, line.continues)
    if result.status == "invalid" and _rounds_the_line_above(ctx, stmt):
        return _abstain(result, "possible_rounding")
    # (x-2)(x-3) = 0, x - 2 = 0, x - 3 = 0: the last doesn't follow from the line above it. It's
    # the other branch of one further back.
    if result.status == "invalid" and _is_branch_of_earlier(ctx, equation, stmt):
        return _abstain(result, "another_branch")
    return result


def _rounds_the_line_above(ctx: _Context, stmt: Statement) -> bool:
    """`x = 24.6` then `x = 25`. It's the answer, to fewer figures."""

    prev = ctx.prev
    if prev is None or not _is_solution_form(prev) or not _is_solution_form(stmt) or prev.parts[0] != stmt.parts[0]:
        return False
    above, written = to_sympy(prev.parts[1]), to_sympy(stmt.parts[1])
    if _decimal_places(prev.parts[1]) == 0:
        return False
    return _rounds_to(above, written, _decimal_places(stmt.parts[1])) or _rounds_to_tens(above, written)


def _is_branch_of_earlier(ctx: _Context, compared_with: Statement, stmt: Statement) -> bool:
    try:
        for earlier in ctx.equations:
            if earlier is compared_with:
                continue
            lhs, rhs = to_sympy(earlier.parts[0]), to_sympy(earlier.parts[1])
            symbols = (lhs - rhs).free_symbols
            if len(symbols) != 1:
                continue
            variable = next(iter(symbols))
            roots = _real_solutions(lhs, rhs, variable)
            if stmt.kind == "solutions":
                written = [to_sympy(value) for value in stmt.values] if stmt.variable == str(variable) else None
            else:
                written = _real_solutions(to_sympy(stmt.parts[0]), to_sympy(stmt.parts[1]), variable)
            if roots and written and _same_set(written, roots)[1]:
                return True
    except MathParseError:
        return False
    return False


def _has_bare_e(left: Node, right: Node) -> bool:
    """`2e = 4` has no other variable, so the e is probably one rather than Euler's number."""

    def mentions_e(node: Node) -> bool:
        return (node.kind == "const" and node.value == "e") or any(mentions_e(child) for child in node.args)

    return (mentions_e(left) or mentions_e(right)) and not _written_variables(left) | _written_variables(right)


def _check_unmarked(
    left: Node, right: Node, line: LineInput, from_line: int | None, prev_finished: bool = False
) -> StepResult:
    """Two lines with no written `=` or derivative sign between them."""

    result = _check_identity(left, right, line.index, from_line, values=line.values)
    if result.status != "invalid":
        return result
    if contains_kind(left, "integral") or contains_kind(right, "integral"):
        # `∫ x dx`, then `x^3` with no `=`: that may be working towards it, or something else.
        return _abstain(result, "transition_unclear")
    symbols = _free_symbol_names(left)
    if len(symbols) == 1:
        derivative = Node("deriv", args=(left,), value=symbols[0])
        if _check_identity(derivative, right, line.index, from_line).status == "valid":
            return replace(result, status="valid", operation_type="differentiation",
                           error_category=None, error_type=None, evidence=None,
                           expected=None, fingerprint=None)
        if line.operation == "differentiate" and _could_be_a_derivative(left, right, symbols[0]):
            return _check_identity(derivative, right, line.index, from_line, source=(left, symbols[0]))
    # Nothing written says these lines are the same thing, and the label may be wrong. Only
    # challenge the step if they clearly look like two forms of one expression.
    # `x^2 - 1`, `= (x-1)(x+1)`, `x^2 - 4`: that exercise was finished, and this has no `=`.
    if prev_finished:
        return _abstain(result, "transition_unclear")
    a, b = to_sympy(left), to_sympy(right)
    # `2x` then `x^2`: neither has anything to simplify, so neither is a step from the other.
    if not (_is_unsimplified(left, a) or _is_unsimplified(right, b)) or not _leading_terms_agree(a, b):
        return _abstain(result, "transition_unclear")
    # `(x+1)^2` then `(x+2)^2`, or `2+3` then `4+5`: nothing has been worked out between them.
    if has_distribution(left) and has_distribution(right):
        return _abstain(result, "transition_unclear")
    if not a.free_symbols and not b.free_symbols and not _is_literal(right):
        return _abstain(result, "transition_unclear")
    return result


def _could_be_a_derivative(left: Node, right: Node, variable: str) -> bool:
    """The label alone doesn't make it so: `x + 1` then `x + 2` is no derivative, right or wrong."""

    symbol = sp.Symbol(variable, real=True)
    try:
        return sp.Poly(to_sympy(left), symbol).degree() == sp.Poly(to_sympy(right), symbol).degree() + 1
    except (sp.PolynomialError, MathParseError):
        return False


def _free_symbol_names(node: Node) -> list[str]:
    try:
        return sorted(str(symbol) for symbol in to_sympy(node).free_symbols)
    except MathParseError:
        return []


def _mentions(node: Node, variable: str) -> bool:
    return variable in _free_symbol_names(node)


def _advance(ctx: _Context, stmt: Statement, line: LineInput) -> _Context:
    definitions = dict(ctx.definitions)
    # y = 9 is a value of y at some point. It doesn't replace what y is: y = x^2 still holds.
    if stmt.kind == "definition" and _free_symbol_names(stmt.parts[0]):
        definitions[stmt.name] = _new_definition(definitions, stmt.name, stmt.parts[0])
    elif _is_equation(stmt) and stmt.parts[0].kind == "var" and _free_symbol_names(stmt.parts[1]):
        definitions[stmt.parts[0].value] = _new_definition(definitions, stmt.parts[0].value, stmt.parts[1])
    anchor, anchor_line, anchor_input = ctx.anchor, ctx.anchor_line, ctx.anchor_input
    if stmt.kind == "expression":
        # Whatever this is, it isn't a step of the equation, so that has stopped being worked on.
        anchor, anchor_line, anchor_input = None, None, None
    equations = ctx.equations
    if _is_equation(stmt) and not _is_numeric_statement(stmt) and not _is_solution_form(stmt):
        equations = (*equations, stmt)[-MAX_EQUATIONS_REMEMBERED:]
    # A numeric line, like a value being tried, isn't an equation that later lines solve.
    if _is_equation(stmt) and not _is_solution_form(stmt) and not _is_numeric_statement(stmt):
        anchor, anchor_line, anchor_input = stmt, line.index, line
    expression_run = ctx.expression_run + 1 if stmt.kind == "expression" else 0
    gives_a_value = _is_solution_form(stmt) or (_is_equation(stmt) and stmt.parts[0].kind == "var")
    valued = ctx.valued | {stmt.parts[0].value} if gives_a_value else ctx.valued
    return _Context(
        stmt, line, anchor, anchor_line, anchor_input, definitions, expression_run, equations, ctx.task or stmt.task, valued,
        ctx.unread_above, ctx.task_answered,
    )


def _new_definition(definitions: dict[str, Node | None], name: str, value: Node) -> Node | None:
    """y = x^2 and y = x + 2 on one page is a pair of curves, so dy/dx could be about either."""

    if name not in definitions:
        return value
    existing = definitions[name]
    if existing is None or compare(to_sympy(existing), to_sympy(value)) != "equal":
        return None
    return value


def _parse_alternatives(line: LineInput) -> list[Statement]:
    return [statement for text in plausible_alternatives(line.math, line.alternatives) if (statement := _try_parse(text))]


def is_a_sum(math: str) -> bool:
    """A claim made entirely of numbers, so that there's no working or letter to be read."""

    statement = _try_parse(math)
    return statement is not None and statement.kind in ("equality", "approximation") and _is_numeric_statement(statement)


def is_a_claim_on_its_own(math: str) -> bool:
    """`d/dx(...) = ...` or `∫ ... dx = ...`: what's asked and the answer, with nothing else needed."""

    statement = _try_parse(math)
    if statement is None or statement.kind != "equality" or statement.continuation or len(statement.parts) != 2:
        return False
    return statement.parts[0].kind in ("deriv", "integral")


def reads_the_same(math: str, other: str) -> bool:
    """Do these say the same thing, however it's spelled? 1 ÷ 5 = 0, 1/5 = 0, and 1/5 = O or D."""

    one, two = _try_parse(math), _try_parse(_with_round_letters_as_zeros(math, other))
    return (
        one is not None
        and two is not None
        and one.kind == two.kind
        and one.parts == two.parts
        and one.precision == two.precision
    )


def _with_round_letters_as_zeros(math: str, other: str) -> str:
    """D or O where the line being compared has a 0. Elsewhere they stay letters."""

    first, second = "".join(math.split()), "".join(other.split())
    if len(first) != len(second):
        return other
    pairs = zip(first, second, strict=True)
    swapped = "".join("0" if theirs in "OoDQ" and ours == "0" else theirs for ours, theirs in pairs)
    # With nothing swapped it's left as written, as the space in `sin x` is what makes it a function.
    return other if swapped == second else swapped


def plausible_alternatives(math: str, alternatives: tuple[str, ...] | list[str]) -> list[str]:
    """Other readings of a line that are worth doubting it for.

    Almost all of them. A round shape in a sum, offered as O or D as well as 0, used to clear a
    true mistake, because 1/5 = D is an equation with nothing to check. That's the same shape, read
    the same way, so it's no doubt at all. Any other letter is a real one: `s + 1 = 3` may be just
    what was written below `5 + 1 = 3`.
    """

    numeric = is_a_sum(math)
    kept = [text for text in alternatives if not (numeric and reads_the_same(math, text))]
    return kept[:MAX_ALTERNATIVES]


def _is_explained_by_alternative(ctx: _Context, line: LineInput) -> bool:
    """Would any other plausible reading of the two lines make this step not wrong?"""

    current_readings = _parse_alternatives(line)
    previous_readings = _parse_alternatives(ctx.prev_line) if ctx.prev_line else []

    def clears(context: _Context, stmt: Statement) -> bool:
        return not any(step.status == "invalid" for step in _evaluate_safely(context, stmt, line))

    for stmt in current_readings:
        if clears(ctx, stmt):
            return True
    original = _try_parse(line.math)
    for prev in previous_readings:
        anchor = prev if ctx.anchor is ctx.prev else ctx.anchor
        context = replace(ctx, prev=prev, anchor=anchor)
        for alternative in [original, *current_readings]:
            if alternative is not None and clears(context, alternative):
                return True
    # An equation step can be compared with a line further back than the one above.
    if ctx.anchor_input is not None and ctx.anchor_input is not ctx.prev_line:
        for anchor in _parse_alternatives(ctx.anchor_input):
            context = replace(ctx, anchor=anchor)
            for alternative in [original, *current_readings]:
                if alternative is not None and clears(context, alternative):
                    return True
    return False


def _try_parse(text: str) -> Statement | None:
    try:
        return parse_statement(text)
    except MathParseError:
        return None


def _status_for_parse_error(exc: MathParseError) -> StepStatus:
    return "unsupported" if isinstance(exc, Unsupported) else "uncertain"


def _starts_with_operator(line: LineInput) -> bool:
    # -x + 3 is a line in its own right, while - 3x, with the space, is carrying on.
    text = line.math.strip()
    return "=" not in text and (text.startswith("+") or text.startswith(("- ", "− ")))


def _evaluate_safely(ctx: _Context, stmt: Statement, line: LineInput) -> list[StepResult]:
    """One line we can't work out shouldn't cost the rest of the page its check."""

    try:
        return _evaluate_line(ctx, stmt, line)
    except (MathParseError, TypeError, ValueError, ArithmeticError, AttributeError):
        log.warning("line_could_not_be_evaluated", exc_info=True)
        return [StepResult("uncertain", "expression_equivalence", line.index, reason="could_not_evaluate")]


def _equals_the_left_side(wrong: Statement, stmt: Statement, line: int, values: Values = ()) -> bool:
    """`2 * 3 = 5`, `= 2 * 3`: whatever else this line is, it's true, so it can't be accused."""

    return (
        stmt.continuation
        and bool(stmt.parts)
        and wrong.kind == "equality"
        and len(wrong.parts) == 2
        and all(_is_a_fair_value(wrong.parts[0], part, stmt, line, values) for part in stmt.parts)
    )


def _is_a_fair_value(source: Node, part: Node, stmt: Statement, line: int, values: Values = ()) -> bool:
    """`1/3 = 0`, `= 0.33`: a student who rounds the right value has put the line above right."""

    if stmt.kind == "approximation" or stmt.precision is not None:
        return _check_rounding(source, part, stmt.precision, line, line).status == "valid"
    result = _check_identity(source, part, line, line, values=values)
    if result.status == "valid":
        return True
    # Rounded, and not merely close: pi = 3, then `= 3.2`, hasn't put anything right.
    return result.reason == "possible_rounding" and _rounds_to(to_sympy(source), to_sympy(part), _decimal_places(part))


def _is_plain_surd(node: Node) -> bool:
    """A number in its usual final form: 3pi, 2sqrt(2), (1 + sqrt(5))/2. Not sqrt(8) or 8^(1/2)."""

    if node.kind in ("num", "const"):
        return True
    if node.kind == "func" and node.value == "sqrt" and node.args[0].kind == "num" and not node.args[0].decimal:
        radicand = int(node.args[0].value)
        return radicand > 1 and sp.sqrt(radicand) == sp.Pow(radicand, sp.Rational(1, 2), evaluate=False)
    return node.kind in ("neg", "mul", "add", "div") and all(_is_plain_surd(child) for child in node.args)


def _terms_of(node: Node) -> Counter[str]:
    return Counter(map(repr, zip(node.signs, node.args, strict=False))) if node.kind == "add" else Counter([repr(node)])


def _is_worked_out(left: Node, answer: Node) -> bool:
    """Does this say what `left` comes to, rather than saying `left` again in other words?"""

    if answer == left or contains_kind(answer, "deriv"):
        return False
    value = to_sympy(answer)
    if not value.free_symbols:
        return _is_literal(answer) if value.is_Rational else _is_plain_surd(answer)
    # Is it the same terms in another order?
    return _terms_of(answer) != _terms_of(left) and not _is_unsimplified(answer, value)


def completes_answer(question: str, expected: str, line: str) -> bool:
    """Does this line finish a practice question: the right value, and worked out?

    Saying whether work is wrong stays with `verify_lines`. This is the other half, as a true line
    isn't always a finished one: 4x + 3x + 2 = 4x + 3x + 2 is true, and hasn't answered anything.
    """

    try:
        statement = parse_statement(line)
        if "=" in question:
            return _states_the_solution(question, statement)
        left = parse_expression(question)
        if statement.kind != "equality" and statement.kind != "expression":
            return False
        if left.kind == "integral" and len(left.args) == 1:
            return _is_a_general_antiderivative(left, statement.parts[-1])
        target = parse_expression(expected)
        answer = statement.parts[-1]
        value = to_sympy(answer)
        if compare(value, to_sympy(target)) != "equal":
            return False
    except MathParseError:
        return False
    # A fraction is the answer to the division it spells, so 3 / 4 finishes 3 ÷ 4.
    if not value.free_symbols and value.is_Rational:
        return _is_literal(answer)
    return _is_worked_out(left, answer)


def completes_in_form(question: str, expected: str, line: str, form: str) -> bool:
    """Does this line finish a question that asks for its answer in a form, not just a value?

    "lowest_terms": `6/9 = 2/3` finishes "Simplify 6/9", and `= 6/9` or `= 4/6`, though true, don't. Nor does
    `0.75` for "Write 0.75 as a fraction". A whole number counts. "decimal": `1/4 = 0.25` finishes "Write 1/4
    as a decimal", and `= 1/4` doesn't. Only for questions Quack set this way.
    """

    if not completes_answer(question, expected, line):
        return False
    try:
        answer = parse_statement(line).parts[-1]
    except MathParseError:
        return False
    if answer.kind == "neg":
        answer = answer.args[0]
    if form == "decimal":
        return answer.kind == "num"
    if form == "lowest_terms":
        if answer.kind == "num":
            return not answer.decimal
        if answer.kind != "div" or any(part.kind != "num" or part.decimal for part in answer.args):
            return False
        top, bottom = (int(part.value) for part in answer.args)
        return bottom > 1 and math.gcd(top, bottom) == 1
    return False


def completes_factorisation(question: str, line: str) -> bool:
    """Does this line finish "Factorise ...": the same value, with every common whole number taken out?

    `6x + 9 = 3(2x + 3)` finishes it. `= 6x + 9` and `= 1(6x + 9)` haven't taken anything out, and
    `1.5(4x + 6)` or `3(4x - 6)` leave a factor behind. Only for a question Quack set as factorising:
    `completes_answer` judges everything else.
    """

    try:
        statement = parse_statement(line)
        if statement.kind not in ("equality", "expression"):
            return False
        answer = statement.parts[-1]
        if compare(to_sympy(answer), to_sympy(parse_expression(question))) != "equal":
            return False
        sign = 1
        if answer.kind == "neg":
            sign, answer = -1, answer.args[0]
        if answer.kind != "mul" or len(answer.args) != 2:
            return False
        factor, bracket = answer.args if answer.args[1].kind == "add" else answer.args[::-1]
        # -6(3 - 2x) is read as (-6) times the bracket.
        if factor.kind == "neg":
            sign, factor = -sign, factor.args[0]
        if factor.kind != "num" or factor.decimal or bracket.kind != "add":
            return False
        taken_out = sign * to_sympy(factor)
        inside = sp.expand(to_sympy(bracket))
        if not taken_out.is_Integer or abs(taken_out) < 2 or not inside.free_symbols:
            return False
        coefficients = sp.Poly(inside, *sorted(inside.free_symbols, key=str)).coeffs()
    except (MathParseError, sp.PolynomialError):
        return False
    return all(value.is_Integer for value in coefficients) and sp.gcd(list(coefficients)) == 1


def _states_the_solution(equation: str, statement: Statement) -> bool:
    """`x = 2` answers `2x + 3 = 7`, and `2x = 4`, though true, doesn't yet."""

    posed = parse_statement(equation)
    if not _is_solution_form(statement):
        return False
    symbol = sp.Symbol(statement.parts[0].value, real=True)
    roots = _real_solutions(to_sympy(posed.parts[0]), to_sympy(posed.parts[1]), symbol)
    if not roots:
        return False
    return _same_set([to_sympy(statement.parts[1])], roots)[0]


def _is_a_general_antiderivative(integral: Node, answer: Node) -> bool:
    integrand, claimed = to_sympy(integral.args[0]), to_sympy(answer)
    known = {str(symbol) for symbol in integrand.free_symbols} | {integral.value}
    new_letters = {str(symbol) for symbol in claimed.free_symbols} - known
    if not new_letters <= ARBITRARY_CONSTANTS or not _has_arbitrary_constant(claimed, new_letters):
        return False
    return compare(sp.diff(claimed, sp.Symbol(integral.value, real=True)), integrand) == "equal"


def _answers_again(wrong: Statement, stmt: Statement, line: int, values: Values = ()) -> bool:
    """`2 * 3 = 5`, `= 6`: the right value, worked out, for the wrong line's left side.

    `= 2 * 3`, `= 4 + 4`, `= sqrt(36)` and `= (x+1)(x+1)` are true, but don't say what it comes to,
    so they can't count as having fixed it. `= 5/6`, `= 2sqrt(2)` and `= 3pi` do.
    """

    return _equals_the_left_side(wrong, stmt, line, values) and _is_worked_out(wrong.parts[0], stmt.parts[-1])


def _corrects(ctx: _Context, wrong: Statement, stmt: Statement, line: LineInput) -> bool:
    """Is `stmt` what the wrong line should have said, following from what came before it?

    `2(x+3)`, `= 2x+3`, `= 2x+6` is a student fixing their work. Carrying on from the wrong value
    would call the right answer a mistake, and quack at the correction. A line that is merely true
    is no correction: `3 + 4 = 7` below it has nothing to do with the mistake, which is still there.
    """

    if line.incomplete or not line.legible:
        return False
    # The whole step written out again: `2(x+3)`, `= 2x+3`, `2(x+3) = 2x+6`.
    if stmt.kind in ("equality", "approximation") and len(stmt.parts) == 2 and wrong.parts and not _is_solution_form(stmt):
        sources = [wrong.parts[0], *(ctx.prev.parts[-1:] if ctx.prev else [])]
        restated = stmt.parts[0] in sources
        if restated and _is_a_fair_value(stmt.parts[0], stmt.parts[1], stmt, line.index, line.values):
            return True
    # A line worked out in one go and answered again below: `2 * 3 = 5`, `= 6`. There's no line
    # above the wrong one to follow on from, so what it should have said comes from its own left side.
    if _answers_again(wrong, stmt, line.index, line.values):
        return True
    # However the model labels it, what counts is whether it follows. Unrelated lines don't.
    results = _evaluate_safely(ctx, stmt, replace(line, continues="yes"))
    if not results or any(result.status != "valid" for result in results):
        return False
    follows = any(result.from_line is not None and result.from_line != line.index for result in results)
    return follows or (bool(stmt.parts) and bool(wrong.parts) and stmt.parts[0] == wrong.parts[0])


def _copied_part_way(ctx: _Context, stmt: Statement, line: LineInput) -> bool:
    """Is this line some of the terms of what it follows, each copied exactly, and fewer of them?

    Only on a page Quack set, where every line is a claim it posed, so a pause while copying the
    question out reads as a false one. A line that combines anything, or writes any term differently,
    has done some working, and that's checked as usual. Equations are left alone.
    """

    if not line.posed or line.given or line.intent == "solve" or not stmt.parts:
        return False
    if stmt.kind not in ("equality", "expression"):
        return False
    sources = [*stmt.parts[:-1], *(ctx.prev.parts[-1:] if ctx.prev is not None and ctx.prev.parts else [])]
    try:
        written = [term for _, _, term in _signed_terms(stmt.parts[-1])]
        for source in sources:
            if source.kind != "add" or len(written) >= len(source.args):
                continue
            remaining = [term for _, _, term in _signed_terms(source)]
            for term in written:
                match = next((index for index, other in enumerate(remaining) if sp.expand(term - other) == 0), None)
                if match is None:
                    break
                remaining.pop(match)
            else:
                return True
    except MathParseError:
        return False
    return False


def _is_corrected_by_next_line(ctx: _Context, wrong: Statement, following: list[LineInput]) -> bool:
    if not following:
        return False
    statement = _try_parse(following[0].math)
    return statement is not None and _corrects(ctx, wrong, statement, following[0])


def _starts_new_problem(prev: Statement | None, stmt: Statement, line: LineInput) -> bool:
    """Is this line a complete claim of its own, rather than a step on from the line above?

    A page holds many problems, and `3 * 4 = 10` above `1 / 2 = 2` is two mistakes, not one. This
    decides from the mathematics on the line, so a label can't join them up, and it only separates
    what can safely stand alone. `1 = 2` below `x + 1 = x + 2` isn't a sum of its own, so a numeric
    line after an equation stays with it, and so does anything restating the line above, which is
    what a correction does.
    """

    if prev is None or not prev.parts or stmt.continuation:
        return False
    if stmt.kind == "approximation":
        # 1/3 = 0, then 1/3 ≈ 0.33, is that line put right. See _corrects.
        return stmt.parts[0] != prev.parts[0]
    if stmt.kind != "equality" or len(stmt.parts) != 2:
        return False
    left, right = stmt.parts
    # An integral has no one value to compare, so it's the same question or another one.
    if left.kind == "integral" and len(left.args) == 1:
        return not prev.parts or left != prev.parts[0]
    try:
        if contains_kind(right, "deriv") or _is_solution_form(stmt):
            return False
        is_claim = (
            (_is_numeric_statement(stmt) and (_is_numeric_statement(prev) or not _is_equation(prev)))
            or (_is_numeric_statement(stmt) and _is_identity_claim(left, right, line, prev))
            or (left.kind == "deriv" and bool(_free_symbol_names(left.args[0])))
            or left.kind == "integral"
            or (line.continues == "no" and _is_identity_claim(left, right, line, None))
        )
        if not is_claim:
            return False
        value = to_sympy(left)
        if compare(value, to_sympy(prev.parts[-1])) == "equal":
            # `3 * 4 = 10`, `5 + 5 = 11`. Five and five coming to that wrong answer is a coincidence
            # when the line is known to be its own. It's false either way, so saying so risks nothing.
            return line.continues == "no" and _is_numeric_statement(stmt) and _is_numeric_statement(prev)
        # `3 * 4 = 10`, `3 * 4 = 12` puts the line above right. `3 * 4 = 10`, `3 * 4 = 9` is just
        # another false claim, which nothing above it led to.
        # The line above was about an integral, which has no one value for this to be another of.
        if prev.parts[0].kind == "integral" and len(prev.parts[0].args) == 1:
            return True
        return compare(value, to_sympy(prev.parts[0])) != "equal" or compare(value, to_sympy(right)) != "equal"
    except (MathParseError, TypeError, ValueError, ArithmeticError, AttributeError):
        return False


def verify_lines(lines: list[LineInput], acknowledged: frozenset[str] = frozenset()) -> VerificationReport:
    """Check consecutive written lines, and report the first new error of each separate problem.

    `acknowledged` holds fingerprints of mistakes the student already knows about. Checking
    continues past those, so a mistake left on the page can't hide a later one. A later line
    that carries the earlier wrong value forward compares against what was written, so it
    isn't reported as a second mistake.
    """

    ctx = _Context()
    given = _try_parse(lines[0].given) if lines and lines[0].given else None
    if given is not None:
        ctx = _advance(ctx, given, LineInput(-1, lines[0].given))
    steps: list[StepResult] = []
    # Where things stood before the first mistake left on the page. Work carried on from the wrong
    # value can still be followed by the right answer, however many lines later.
    before_mistake: tuple[_Context, Statement, int] | None = None
    new_mistake_above = False
    problem_line = lines[0].index if lines else None
    # What each letter stands for in the working: the question's value, or one the student wrote instead.
    in_use = dict(lines[0].values) if lines else {}
    # A value written against the question, by letter, with where its step is.
    wrong_givens: dict[str, int] = {}
    for position, line in enumerate(lines):
        if line.incomplete:
            steps.append(StepResult("uncertain", "reading", line.index, reason="incomplete_line"))
            # The next line follows from something we couldn't read, so nothing carries over.
            ctx = _Context()
            continue
        if not line.legible and not line.alternatives and not line.math:
            steps.append(StepResult("uncertain", "reading", line.index, reason="illegible_line"))
            ctx = _Context()
            continue
        givens = _given_statements(line.math) if line.values else []
        if givens and all(_states_a_given(given, line) is not None for given in givens):
            # About the question, not a step of the working, which carries on as if it weren't there.
            for given in givens:
                stated = _states_a_given(given, line)
                assert stated is not None
                letter = given.parts[0].value
                if stated.status == "valid":
                    # Saying it right below saying it wrong puts that right, and the wrong one is then no
                    # more part of the working than the right one, so it goes from the report.
                    if letter in wrong_givens:
                        where = wrong_givens.pop(letter)
                        steps[where] = _abstain(steps[where], GIVEN_PUT_RIGHT)
                    in_use[letter] = dict(lines[0].values)[letter]
                    steps.append(stated)
                    continue
                if not line.legible or _restates_it_on_another_reading(line):
                    stated = _abstain(stated, "low_legibility" if not line.legible else "alternative_reading_is_valid")
                is_new = stated.status == "invalid" and stated.fingerprint not in acknowledged
                if is_new and new_mistake_above:
                    stated = _abstain(stated, "follows_a_new_mistake")
                if stated.status == "invalid":
                    wrong_givens[letter] = len(steps)
                    new_mistake_above = new_mistake_above or is_new
                # Working that goes on with the value written isn't a second slip, before or after this
                # one has been seen.
                in_use[letter] = format_node(given.parts[1])
                steps.append(replace(stated, problem_line=line.index) if stated.status == "invalid" else stated)
            continue
        if line.values and tuple(in_use.items()) != line.values:
            line = replace(line, values=tuple(in_use.items()))
        try:
            stmt = parse_statement(line.math)
        except MathParseError as exc:
            steps.append(
                StepResult(
                    _status_for_parse_error(exc),
                    # 1/2x was read perfectly well, and it's the notation that has two meanings.
                    "notation" if isinstance(exc, Ambiguous) else "reading",
                    line.index,
                    reason=exc.reason,
                )
            )
            # A line we could not read breaks the chain: the next line does not follow
            # from the line before this one.
            ctx = _Context(unread_above=True)
            continue

        if stmt.kind == "task":
            # `Simplify:` says what the next piece of working is doing.
            ctx = _Context(task=stmt.task)
            continue
        if stmt.task:
            ctx = _Context(task=stmt.task)
        elif line.continues == "no" and not stmt.continuation and ctx.task and ctx.prev is not None:
            # Whatever that task was about has come to an end.
            ctx = replace(ctx, task="")

        # A written `=` carries on from the line above, whatever the label says.
        if line.continues == "no" and not stmt.continuation:
            before_mistake = None
        # A claim complete in itself is its own problem, whatever is wrong above it.
        if _starts_new_problem(ctx.prev, stmt, line):
            before_mistake = None
            new_mistake_above = False
            problem_line = line.index
        elif line.continues == "no" and not stmt.continuation:
            problem_line = line.index
        # A line beginning with an operator finishes the one above: `= x^2 + 3x`, `+ 2x + 6`.
        # Neither is a whole step, and what comes next follows from the two of them together.
        if _starts_with_operator(line):
            steps.append(StepResult("uncertain", "expression_equivalence", line.index, reason="wrapped_line"))
            ctx = _Context()
            continue
        results = _evaluate_safely(ctx, stmt, line)
        invalid = [result for result in results if result.status == "invalid"]
        if (
            position == len(lines) - 1
            and invalid
            # Only the part being written. `5x + 2x + 3 = 8x + 3 = 8x` has a finished wrong part before it.
            and stmt.parts
            and all(result.current_step == format_node(stmt.parts[-1]) for result in invalid)
            and _copied_part_way(ctx, stmt, line)
        ):
            # The page's last line is some of the terms above, copied exactly, with the rest not there yet:
            # `8a + 3b - 4a - 7b`, then `= 8a - 4a`, as the next term is being written. Nothing has been worked
            # out on it to be wrong. Once the line is finished, or anything is written below it, it's checked.
            results = [_abstain(result, "still_writing") if result.status == "invalid" else result for result in results]
        if lines[position + 1 : position + 2] and _starts_with_operator(lines[position + 1]):
            results = [_abstain(result, "wrapped_line") if result.status == "invalid" else result for result in results]
        # This carries on from a right-hand side that may be wrong, but it's a true value for the
        # left side: `2(x+3) = 2x+3`, `= 2x+6`, or `sqrt(8) = 4`, `= sqrt(8)`. Never accuse it.
        is_true = ctx.prev is not None and _equals_the_left_side(ctx.prev, stmt, line.index, line.values)
        if is_true and any(r.status == "invalid" for r in results):
            # A sign hidden by a square is always a true value: that's what hides it. See _check_by_value.
            results = [
                _abstain(r, "answers_the_line_above") if r.status == "invalid" and r.error_type != SIGN_HIDDEN else r
                for r in results
            ]
        wrong = any(result.status == "invalid" for result in results)
        unsettled = any(result.status != "valid" for result in results)
        # `2 * 3 = 5`, `= 2 * 3`, `= 6`: the last follows from the one before, and also answers the mistake.
        may_correct = before_mistake is not None and (
            unsettled or _answers_again(before_mistake[1], stmt, line.index, line.values)
        )
        # Every true line below a sign hidden by a square follows from what it should have said, as the
        # value's the same. Only the values put in as given put it right.
        hidden_above = before_mistake is not None and any(
            step.status == "invalid" and step.error_type == SIGN_HIDDEN and step.line >= before_mistake[2] for step in steps
        )
        # And the substitution written out again as given puts it right wherever it comes, even after some
        # arithmetic, as `= (3)^2 + 3(-3)`, `= 9 - 9`, `= (-3)^2 + 3(-3)`: it's what the slip should have said.
        hidden_put_right = (
            before_mistake is not None
            and hidden_above
            and _puts_the_givens_in(before_mistake[0], before_mistake[1], stmt, line.values)
        )
        if (
            before_mistake is not None
            and (may_correct or hidden_put_right)
            and _corrects(before_mistake[0], before_mistake[1], stmt, line)
            and (not hidden_above or hidden_put_right)
        ):
            ctx, _, mistake_line = before_mistake
            steps = [
                _abstain(step, "corrected_further_down") if step.status == "invalid" and step.line >= mistake_line else step
                for step in steps
            ]
            before_mistake = None
            # It's just been shown right. An earlier sum on the page, which this now seems to carry
            # on from, can't make it wrong: `3 + 4 = 7`, `2 * 3 = 5`, `= 6`.
            again = _evaluate_safely(ctx, stmt, line)
            results = [_abstain(r, "corrected_further_down") if r.status == "invalid" else r for r in again]
            wrong = False
        corrected = wrong and _is_corrected_by_next_line(ctx, stmt, lines[position + 1 : position + 2])
        if corrected and any(r.status == "invalid" and r.error_type == SIGN_HIDDEN for r in results):
            following = lines[position + 1]
            corrected = _puts_the_givens_in(ctx, stmt, _try_parse(following.math), following.values or line.values)
        for result in results:
            if result.status != "invalid":
                steps.append(result)
                continue
            if corrected:
                steps.append(_abstain(result, "corrected_on_the_next_line"))
                continue
            previous_legible = ctx.prev_line.legible if ctx.prev_line else True
            compared_with_anchor = ctx.anchor_input is not None and result.from_line == ctx.anchor_line
            anchor_legible = ctx.anchor_input.legible if compared_with_anchor and ctx.anchor_input else True
            if not line.legible or not previous_legible or not anchor_legible:
                steps.append(_abstain(result, "low_legibility"))
            elif _is_explained_by_alternative(ctx, line):
                steps.append(_abstain(result, "alternative_reading_is_valid"))
            else:
                # This follows from a line the student hasn't yet been told is wrong. One slip gets
                # one alert. Once the first is known, or fixed, this one can come up in its own right.
                is_new = result.fingerprint not in acknowledged
                if is_new and new_mistake_above:
                    steps.append(_abstain(result, "follows_a_new_mistake"))
                    continue
                steps.append(replace(result, problem_line=problem_line))
                new_mistake_above = new_mistake_above or is_new
                before_mistake = before_mistake or (ctx, stmt, line.index)
        # Steps after a correction follow from the line that replaced this one.
        if not corrected:
            # `Simplify`, `3x + 2x = 5x`: that's it done, and what comes next may be a new exercise.
            is_a_whole_step = stmt.kind == "equality" and not stmt.continuation
            task_is_done = is_a_whole_step and results and all(r.status == "valid" for r in results)
            ctx = _advance(ctx, stmt, line)
            if task_is_done:
                ctx = replace(ctx, task_answered=True)
        else:
            # Unless what replaces it is only an answer, which follows from this line's left side.
            answer = _try_parse(lines[position + 1].math)
            if answer is not None and _answers_again(stmt, answer, line.index, line.values):
                ctx = _advance(ctx, replace(stmt, kind="expression", parts=stmt.parts[:1]), line)

    steps = [step for step in steps if step.reason != GIVEN_PUT_RIGHT]
    # A task or a setting said this was a claim, and the next line went on to solve it. The working
    # knows better, so it's gone through again as what it is, as what follows may have been held back.
    followed = {step.from_line for step in steps if step.status == "valid" and step.from_line != step.line}
    solved = {step.line for step in steps if step.status == "invalid" and step.intent_from_context and step.line in followed}
    if solved:
        as_solving = [replace(written, intent="solve") if written.index in solved else written for written in lines]
        return verify_lines(as_solving, acknowledged)
    return VerificationReport(_overall(_settle_intent(steps)), tuple(_settle_intent(steps)))


def _restates_it_on_another_reading(line: LineInput) -> bool:
    for alternative in _parse_alternatives(line):
        stated = _states_a_given(alternative, line)
        if stated is not None and stated.status == "valid":
            return True
    return False


def _settle_intent(steps: list[StepResult]) -> list[StepResult]:
    """`3x + 2x = 5`, then `5x = 5`: the next line has answered the question, so it isn't asked."""

    followed = {step.from_line for step in steps if step.status == "valid" and step.from_line != step.line}

    def settled(step: StepResult) -> StepResult:
        if step.line not in followed:
            return step
        if step.reason == "ambiguous_intent":
            return replace(step, status="valid", reason=None)
        return step

    return [settled(step) for step in steps]


def disposition_of(step: StepResult) -> Disposition:
    """What a step comes to, in terms a person would use. Only `verified_incorrect` is ever an error."""

    if step.status == "invalid":
        return "verified_incorrect"
    if step.status == "valid":
        return "valid_under_assumptions" if step.assumption else "verified_correct"
    if step.status == "unsupported":
        return "unsupported"
    if step.reason == "ambiguous_intent":
        return "ambiguous_intent"
    if step.reason in ("incomplete_line", "wrapped_line"):
        return "incomplete"
    if step.reason == "domain_sensitive":
        return "valid_under_assumptions"
    unreadable = ("low_legibility", "illegible_line", "alternative_reading_is_valid")
    if step.operation_type == "reading" or step.reason in unreadable:
        return "transcription_uncertain"
    return "needs_clarification"


def still_present(lines: list[LineInput], acknowledged: frozenset[str]) -> frozenset[str]:
    """Which known mistakes are still on the page, whatever the model says about continuity?

    Whether two lines continue, and what kind of step they are, are labels from the model, and
    they can flip between readings of the same page. Treating a flipped label as a fix would
    resolve the mistake, then quack again when the label flips back. So this asks: under any of
    the labels that could raise it, is it there? It must cover every way a mistake can be raised,
    which `test_label_flips_never_make_a_mistake_quack_twice` checks.
    """

    return frozenset(fingerprint for fingerprint, _ in where_still_present(lines, acknowledged))


def where_still_present(lines: list[LineInput], acknowledged: frozenset[str]) -> frozenset[tuple[str, int]]:
    """`still_present`, with the line each is on, as one mistake can be written out on two lines."""

    if not acknowledged:
        return frozenset()
    present: set[tuple[str, int]] = set()
    for operation in (None, "simplify", "differentiate"):
        for labelling in (_own_label, _everything_follows, _nothing_follows, _sums_stand_alone):
            relabelled = [replace(line, continues=labelling(line), operation=operation or line.operation) for line in lines]
            report = verify_lines(relabelled, _EVERYTHING)
            present.update(
                (step.fingerprint, step.line) for step in report.invalid_steps if step.fingerprint in acknowledged
            )
    return frozenset(present)


def _own_label(line: LineInput) -> Continues:
    return line.continues


def _everything_follows(_: LineInput) -> Continues:
    return "yes"


def _nothing_follows(_: LineInput) -> Continues:
    return "no"


def _sums_stand_alone(line: LineInput) -> Continues:
    # 1/2 + 1/3 = 2/5 below other working is only wrong as a sum in its own right. Following on from
    # an equation it would be a result, so it has to be asked about both ways.
    parsed = _try_parse(line.math)
    is_a_sum = parsed is not None and parsed.kind == "equality" and not parsed.continuation and _is_numeric_statement(parsed)
    return "no" if is_a_sum else "yes"


class _Everything(frozenset):
    """Treats every mistake as already known, so that checking runs to the end of the page."""

    def __contains__(self, item: object) -> bool:
        return True


_EVERYTHING = _Everything()


def _abstain(result: StepResult, reason: str) -> StepResult:
    return replace(result, status="uncertain", reason=reason, fingerprint=None)


def _overall(steps: list[StepResult]) -> StepStatus:
    statuses = {step.status for step in steps}
    if "invalid" in statuses:
        return "invalid"
    if "uncertain" in statuses:
        return "uncertain"
    if "unsupported" in statuses:
        return "unsupported"
    return "valid"
