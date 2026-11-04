"""Naming the misconception behind a verified mistake in a lesson, only where the maths shows it.

The verifier has already decided that the step is wrong, and this never changes that. It only asks
whether the wrong value is the one a known misconception produces, from the question Quack set and
the step that was read, using the safe parser and SymPy. Nothing here comes from a model's opinion
of the student.

Three answers are possible:

- `confirmed`: the verifier's own diagnosis names it, or the wrong value is exactly what the
  misconception gives. Only this changes which question comes next.
- `plausible`: the kind of mistake fits, and the value doesn't prove it. Recorded, not acted on.
- `unclassified`: a real mistake, with nothing more to say about why.

Uncertain readings never get here, as they're never mistakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import sympy as sp

from quack.services.math_parser import MathParseError, Node, parse_expression, parse_statement, to_sympy

Confidence = Literal["confirmed", "plausible", "unclassified"]
UNCLASSIFIED = "verified_error_unclassified"


@dataclass(frozen=True)
class Misconception:
    id: str
    label: str
    # Finishes "Last time, ..." and "It looks like ...". Says what happened to the maths, not to the student.
    happened: str
    # The idea that fixes it, for choosing what to say before the next question.
    idea: str


TAXONOMY: dict[str, Misconception] = {
    item.id: item
    for item in (
        Misconception("variable_dropped", "The letter went missing", "the letter went missing from the answer",
                      "Like terms keep their letter: add the numbers in front, then write the letter after."),
        Misconception("letters_multiplied", "Letters multiplied instead of added", "adding like terms turned into a power",
                      "Adding like terms counts them. The letter stays, with no new power."),
        Misconception("unlike_terms_combined", "Unlike terms combined", "unlike terms were put together",
                      "Only terms with exactly the same letters and powers combine. Numbers on their own stay apart."),
        Misconception("distribution_incomplete", "Not every term was multiplied",
                      "the number outside a bracket didn't reach every term",
                      "The number outside a bracket multiplies every term inside it."),
        Misconception("common_factor_incomplete", "The factor wasn't taken from every term",
                      "a common factor was taken out of only some of the terms",
                      "A common factor divides every term, so check each term left in the bracket."),
        Misconception("letter_changed", "A letter changed", "a letter changed into a different one",
                      "Each term keeps its own letter. Working it out changes the numbers, never the letters."),
        Misconception("sign_error", "A sign changed", "a plus or minus sign changed along the way",
                      "Each term keeps the sign in front of it."),
        Misconception("incorrect_inverse_operation", "The wrong inverse operation", "a step undid the wrong operation",
                      "Undo adding by subtracting, and multiplying by dividing, on both sides."),
        Misconception("substitution_as_digits", "Value written next to the number",
                      "the value was written next to the number instead of multiplied",
                      "A number next to a letter means multiply. Put the value in brackets first."),
        Misconception(UNCLASSIFIED, "A mistake to look at again", "there was a slip", "Take it one line at a time."),
    )
}

# What the verifier's own diagnosis establishes. It's symbolic already: `lost_variable` means the
# answer is the coefficients' total with the letter gone, and `sign_when_moving_term` that the
# equation shifted by twice a moved term.
_CONFIRMED_BY_VERIFIER = {
    "lost_variable": "variable_dropped",
    "sign_when_moving_term": "incorrect_inverse_operation",
    "inverse_of_multiplication": "incorrect_inverse_operation",
    "inverse_of_division": "incorrect_inverse_operation",
    # Each value put in that differs from the question's is its negative.
    "sign_lost_in_substitution": "sign_error",
    # The same, where squaring hid it: the value's right and the substitution isn't.
    "sign_lost_value_unchanged": "sign_error",
    # The answer is right with its letter put back, or one copied term is the same but for its letter.
    "variable_changed": "letter_changed",
    "term_letter_changed": "letter_changed",
}
# The kind of mistake suggests one, without proving it.
_PLAUSIBLE_BY_CATEGORY = {
    "algebra_expansion": "distribution_incomplete",
    "algebra_factorisation": "common_factor_incomplete",
    "equation_solving": "incorrect_inverse_operation",
}


@dataclass(frozen=True)
class Classification:
    misconception: str
    confidence: Confidence
    # How it was established, for the debug trace and the report: "verifier:lost_variable".
    evidence: str


def _unclassified(evidence: str = "no_rule_matched") -> Classification:
    return Classification(UNCLASSIFIED, "unclassified", evidence)


def _value(text: str) -> sp.Expr | None:
    try:
        return sp.expand(to_sympy(parse_expression(text)))
    except (MathParseError, TypeError, ValueError):
        return None


def _same(a: sp.Expr | None, b: sp.Expr | None) -> bool:
    return a is not None and b is not None and sp.expand(a - b) == 0


def _written_terms(node: Node) -> list[sp.Expr]:
    """Each written term with its sign, before anything is combined: 7x - 3x + 2x is 7x, -3x, 2x."""

    if node.kind == "add":
        return [sign * to_sympy(arg) for sign, arg in zip(node.signs, node.args, strict=True)]
    return [to_sympy(node)]


def _monomials(expr: sp.Expr) -> int:
    return len(sp.Add.make_args(sp.expand(expr)))


def _at_ones(expr: sp.Expr) -> sp.Expr:
    return expr.subs({symbol: 1 for symbol in expr.free_symbols})


def _drops_the_variable(before: sp.Expr, after: sp.Expr) -> bool:
    """3x + 2x = 5: the coefficients' total, with every letter gone."""

    return bool(before.free_symbols) and not after.free_symbols and _same(_at_ones(before), after)


def _multiplies_letters(before: sp.Expr, after: sp.Expr) -> bool:
    """x + x + x = x³, or 3x + 2x = 5x²: a sum of like terms of power one, turned into a higher power."""

    if len(before.free_symbols) != 1 or before.free_symbols != after.free_symbols:
        return False
    (letter,) = before.free_symbols
    if sp.Poly(before, letter).degree() != 1 or sp.expand(before).as_coeff_add()[0] != 0:
        return False
    if _monomials(after) != 1 or sp.Poly(after, letter).degree() < 2:
        return False
    return _same(_at_ones(after), _at_ones(before)) or _same(_at_ones(after), sp.Integer(1))


def _combines_unlike(before: sp.Expr, after: sp.Expr) -> bool:
    """4x + 3 = 7x, 2a + 3b = 5ab: fewer kinds of term, and the same total of the numbers in front."""

    return (
        bool(after.free_symbols)
        and _monomials(after) < _monomials(before)
        and _same(_at_ones(after), _at_ones(before))
    )


def _flips_one_sign(node: Node, after: sp.Expr) -> bool:
    """The answer is the question with one term's sign changed, before or after multiplying out.

    7x - 3x + 2x = 2x flips the + 2x. -2(x - 4) = -2x - 8 flips the + 8 of -2x + 8.
    """

    written = _written_terms(node)
    candidates = [written, [term for part in written for term in sp.Add.make_args(sp.expand(part))]]
    for terms in candidates:
        if len(terms) < 2 and not any(term.is_number for term in terms):
            continue
        for index in range(len(terms)):
            flipped = sum(terms[:index], sp.Integer(0)) - terms[index] + sum(terms[index + 1 :], sp.Integer(0))
            if _same(flipped, after):
                return True
    return False


def _brackets(node: Node, sign: int = 1) -> list[tuple[sp.Expr, list[sp.Expr]]]:
    """Every number-or-letter times a sum, as (multiplier with the sign in front of it, the sum's terms).

    In 3(x + 4) - 2(x - 4) the second bracket's multiplier is -2, not 2.
    """

    found: list[tuple[sp.Expr, list[sp.Expr]]] = []
    if node.kind == "add":
        for child_sign, child in zip(node.signs, node.args, strict=True):
            found.extend(_brackets(child, sign * child_sign))
        return found
    if node.kind == "neg":
        if node.args[0].kind == "add":
            found.append((sp.Integer(-sign), _written_terms(node.args[0])))
            return found
        return _brackets(node.args[0], -sign)
    if node.kind == "mul" and len(node.args) == 2:
        for outside, inside in (node.args, node.args[::-1]):
            if inside.kind == "add" and outside.kind != "add":
                found.append((sign * to_sympy(outside), _written_terms(inside)))
    for child in node.args:
        if node.kind == "mul" and child.kind == "add":
            continue
        found.extend(_brackets(child))
    return found


def _misses_a_term_in_a_bracket(node: Node, after: sp.Expr) -> bool:
    """3(x + 2) = 3x + 2: one term inside wasn't multiplied by what's outside.

    The term is either copied as it was, or with only the sign in front of the bracket applied:
    -2(x - 4) as -2x - 4 or as -2x + 4. Those are the only two values it can give.
    """

    right = sp.expand(to_sympy(node))
    for multiplier, terms in _brackets(node):
        signs = {sp.Integer(1), sp.sign(multiplier)} if multiplier.is_number else {sp.Integer(1)}
        for term in terms:
            for kept in signs:
                if multiplier != kept and _same(after, right - (multiplier - kept) * term):
                    return True
    return False


def _factor_left_in(node: Node, after: sp.Expr, answer: Node | None) -> bool:
    """6x + 9 = 3(2x + 9): the factor taken out of one term and left in the other."""

    if answer is None or not _brackets(answer):
        return False
    before = sp.expand(to_sympy(node))
    for multiplier, _ in _brackets(answer):
        for term in sp.Add.make_args(before):
            if _same(after, before + (multiplier - 1) * term):
                return True
    return False


def _equation_sides(text: str) -> tuple[Node, Node] | None:
    try:
        statement = parse_statement(text)
    except MathParseError:
        return None
    if statement.kind != "equality" or len(statement.parts) != 2:
        return None
    return statement.parts[0], statement.parts[1]


def _equation_misconception(previous: str, current: str) -> Classification | None:
    """For a step between two equations. The verifier has named the moving and dividing slips already."""

    before, after = _equation_sides(previous), _equation_sides(current)
    if before is None or after is None:
        return None
    sides_after = [sp.expand(to_sympy(part)) for part in after]
    # One side multiplied out with a term missed, the other left as it was.
    for side in (0, 1):
        other = 1 - side
        if _same(sp.expand(to_sympy(before[other])), sides_after[other]) and _misses_a_term_in_a_bracket(
            before[side], sides_after[side]
        ):
            return Classification("distribution_incomplete", "confirmed", "rule:bracket_term_missed")
    # 3x = 12, then x = 9 or x = 15: the 3 taken away or added, when it was multiplying.
    left, right = (sp.expand(to_sympy(part)) for part in before)
    for written, value in ((left, right), (right, left)):
        coefficient, letter = written.as_coeff_Mul()
        if not letter.is_Symbol or coefficient in (0, 1) or not value.is_number:
            continue
        for answer_side, other_side in ((sides_after[0], sides_after[1]), (sides_after[1], sides_after[0])):
            if answer_side == letter and other_side in (value - coefficient, value + coefficient):
                return Classification("incorrect_inverse_operation", "confirmed", "rule:added_instead_of_divided")
    return None


def classify(
    skill: str,
    question: str,
    method: str,
    probes: tuple[tuple[str, str], ...] | list[tuple[str, str]],
    category: str,
    error_type: str,
    previous_step: str,
    current_step: str,
) -> Classification:
    """The misconception behind one verified mistake on a lesson question, or none.

    `previous_step` and `current_step` are what the verifier compared: for `3x + 2x = 5` those are
    `3x + 2x` and `5`, and for an equation the two equations either side of the step.
    """

    if error_type in _CONFIRMED_BY_VERIFIER:
        return Classification(_CONFIRMED_BY_VERIFIER[error_type], "confirmed", f"verifier:{error_type}")
    if method == "equation" or category == "equation_solving":
        found = _equation_misconception(previous_step, current_step)
        if found is not None:
            return found
        plausible = _PLAUSIBLE_BY_CATEGORY.get(category)
        return Classification(plausible, "plausible", f"category:{category}") if plausible else _unclassified()
    try:
        before_node = parse_expression(previous_step)
        before = sp.expand(to_sympy(before_node))
        answer_node = parse_expression(current_step)
        after = sp.expand(to_sympy(answer_node))
    except (MathParseError, TypeError, ValueError):
        return _unclassified("unreadable_step")
    # A wrong value Quack predicted for this very question.
    for misconception, wrong in probes:
        if _same(_value(wrong), after):
            return Classification(misconception, "confirmed", "probe")
    rules = (
        ("variable_dropped", lambda: _drops_the_variable(before, after)),
        ("letters_multiplied", lambda: _multiplies_letters(before, after)),
        ("distribution_incomplete", lambda: _misses_a_term_in_a_bracket(before_node, after)),
        ("common_factor_incomplete", lambda: _factor_left_in(before_node, after, answer_node)),
        ("unlike_terms_combined", lambda: _combines_unlike(before, after)),
        ("sign_error", lambda: error_type == "sign_error" or _flips_one_sign(before_node, after)),
    )
    for misconception, holds in rules:
        try:
            if holds():
                return Classification(misconception, "confirmed", f"rule:{misconception}")
        except (sp.PolynomialError, TypeError, ValueError):
            continue
    plausible = _PLAUSIBLE_BY_CATEGORY.get(category)
    if plausible:
        return Classification(plausible, "plausible", f"category:{category}")
    return _unclassified()

