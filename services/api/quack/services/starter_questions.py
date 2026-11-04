"""Question templates for the starter levels of the five newer paths: arithmetic, fractions and decimals,
geometry, trigonometry and calculus.

The same kind of template as the Algebra ones in `lesson_questions`, registered with them. Every answer is
worked out here from integers or `Fraction`, never floats and never a model, and the verifier must agree with it
before a question is shown (`lesson_questions.is_checkable`). These are starter levels, a few kinds of question
each, not a course in any of these subjects.

What the verifier can't check isn't asked. Geometry is numbers from given lengths, with no drawings to grade.
Trigonometry uses angles in degrees, said so in every question, and only the exact values that are whole numbers
or simple fractions (0, 1/2 and 1): a surd like √3/2 is read two ways when handwritten, so it's left out, and so
is tan 90°, which has no value. Word questions keep their sum in `math`, which is what the student's working is
checked against, and show the words.
"""

from __future__ import annotations

import random
from fractions import Fraction
from typing import Any

from quack.services.lesson_questions import (
    ARITHMETIC,
    DECIMAL,
    DIFFERENTIATION,
    INTEGRATION,
    LOWEST_TERMS,
    TEMPLATES,
    TOPIC_OF_SKILL,
    LessonQuestion,
    Template,
    _lesson,
    _pick,
)

TOPIC_OF_SKILL.update(
    {
        "arithmetic.add_subtract": "addition_subtraction",
        "arithmetic.multiply_divide": "multiplication",
        "arithmetic.order_of_operations": "arithmetic",
        "fractions.equivalent": "division",
        "fractions.operations": "division",
        "fractions.decimals": "division",
        "geometry.perimeter": "arithmetic",
        "geometry.area": "arithmetic",
        "trigonometry.ratios": "division",
        "trigonometry.exact_values": "arithmetic",
        "trigonometry.applying": "arithmetic",
        "calculus.power_rule": "power_rule",
        "calculus.integration": "integration",
    }
)

_TRIPLES = ((3, 4, 5), (6, 8, 10), (5, 12, 13), (8, 15, 17), (9, 12, 15), (12, 16, 20))


def _fraction(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _decimal(value: Fraction) -> str:
    """Exact, for denominators made of 2s and 5s only."""

    whole, rest = divmod(value.numerator, value.denominator)
    if rest == 0:
        return str(whole)
    digits = ""
    while rest and len(digits) < 6:
        rest *= 10
        digit, rest = divmod(rest, value.denominator)
        digits += str(digit)
    assert rest == 0, value
    return f"{whole}.{digits}"


def _number(value: Fraction) -> str:
    """A length or area: a whole number, or a decimal when it has to be."""

    return _decimal(value) if value.denominator != 1 else str(value.numerator)


# Arithmetic ----------------------------------------------------------------------------------------------------


def _add(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(12, 68))
    b = _pick(rng, fixed, "b", lambda: rng.randint(11, 29))
    return _lesson(
        "arithmetic.add",
        "arithmetic.add_subtract",
        "learn",
        "Work out",
        f"{a} + {b}",
        str(a + b),
        ARITHMETIC,
        {"a": a, "b": b},
        [],
    )


def _subtract(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(41, 95))
    b = _pick(rng, fixed, "b", lambda: rng.randint(12, a - 11))
    return _lesson(
        "arithmetic.subtract",
        "arithmetic.add_subtract",
        "apply",
        "Work out",
        f"{a} - {b}",
        str(a - b),
        ARITHMETIC,
        {"a": a, "b": b},
        [],
    )


def _negatives(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(6, 15))
    b = _pick(rng, fixed, "b", lambda: rng.randint(2, a - 1))
    return _lesson(
        "arithmetic.negatives",
        "arithmetic.add_subtract",
        "challenge",
        "Work out",
        f"{b} - {a}",
        str(b - a),
        ARITHMETIC,
        {"a": a, "b": b},
        [("sign_error", str(a - b))],
    )


def _multiply(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(3, 9))
    b = _pick(rng, fixed, "b", lambda: rng.randint(3, 9))
    return _lesson(
        "arithmetic.multiply",
        "arithmetic.multiply_divide",
        "learn",
        "Work out",
        f"{a} * {b}",
        str(a * b),
        ARITHMETIC,
        {"a": a, "b": b},
        [],
    )


def _divide(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(3, 9))
    b = _pick(rng, fixed, "b", lambda: rng.randint(3, 12))
    return _lesson(
        "arithmetic.divide",
        "arithmetic.multiply_divide",
        "apply",
        "Work out",
        f"{a * b} ÷ {a}",
        str(b),
        ARITHMETIC,
        {"a": a, "b": b},
        [],
        shown=f"{a * b} ÷ {a}",
    )


def _multiply_larger(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(12, 19))
    b = _pick(rng, fixed, "b", lambda: rng.randint(3, 8))
    return _lesson(
        "arithmetic.multiply_larger",
        "arithmetic.multiply_divide",
        "challenge",
        "Work out",
        f"{a} * {b}",
        str(a * b),
        ARITHMETIC,
        {"a": a, "b": b},
        [],
    )


def _multiply_first(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 9))
    b = _pick(rng, fixed, "b", lambda: rng.randint(2, 6))
    c = _pick(rng, fixed, "c", lambda: rng.randint(3, 7))
    # Left to right, (a + b) × c, is the slip this asks about.
    return _lesson(
        "arithmetic.multiply_first",
        "arithmetic.order_of_operations",
        "learn",
        "Work out",
        f"{a} + {b} * {c}",
        str(a + b * c),
        ARITHMETIC,
        {"a": a, "b": b, "c": c},
        [],
    )


def _brackets_first(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(9, 16))
    b = _pick(rng, fixed, "b", lambda: rng.randint(2, a - 3))
    c = _pick(rng, fixed, "c", lambda: rng.randint(2, 5))
    return _lesson(
        "arithmetic.brackets_first",
        "arithmetic.order_of_operations",
        "apply",
        "Work out",
        f"({a} - {b}) * {c}",
        str((a - b) * c),
        ARITHMETIC,
        {"a": a, "b": b, "c": c},
        [],
    )


def _divide_and_subtract(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    d = _pick(rng, fixed, "d", lambda: rng.randint(2, 6))
    q = _pick(rng, fixed, "q", lambda: rng.randint(2, 6))
    a = _pick(rng, fixed, "a", lambda: rng.randint(q + 10, q + 25))
    b = _pick(rng, fixed, "b", lambda: rng.randint(2, 4))
    # a - d·q ÷ d × b: a - q·b. Never negative, and never a division that leaves a remainder.
    return _lesson(
        "arithmetic.divide_and_subtract",
        "arithmetic.order_of_operations",
        "challenge",
        "Work out",
        f"{a} - {d * q} ÷ {d} * {b}",
        str(a - q * b),
        ARITHMETIC,
        {"a": a, "d": d, "q": q, "b": b},
        [],
        shown=f"{a} − {d * q} ÷ {d} × {b}",
    )


# Fractions and decimals ----------------------------------------------------------------------------------------


def _proper(rng: random.Random, fixed: dict[str, Any] | None, largest: int) -> tuple[int, int]:
    while True:
        bottom = rng.randint(3, largest)
        top = rng.randint(1, bottom - 1)
        if Fraction(top, bottom).denominator == bottom:
            return top, bottom


def _simplify(template: str, tier: str, factors: tuple[int, int]):  # type: ignore[no-untyped-def]
    def build(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
        top, bottom = (fixed["p"], fixed["q"]) if fixed and "p" in fixed else _proper(rng, fixed, 7)
        k = _pick(rng, fixed, "k", lambda: rng.randint(*factors))
        math = f"{k * top}/{k * bottom}"
        return _lesson(
            template,
            "fractions.equivalent",
            tier,
            "Simplify",
            math,
            f"{top}/{bottom}",
            LOWEST_TERMS,  # type: ignore[arg-type]
            {"p": top, "q": bottom, "k": k},
            [],
        )

    return build


def _add_same_bottom(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    d = _pick(rng, fixed, "d", lambda: rng.randint(5, 11))
    a = _pick(rng, fixed, "a", lambda: rng.randint(1, d - 3))
    b = _pick(rng, fixed, "b", lambda: rng.randint(1, d - 1 - a))
    # Adding the bottoms too, a + b over 2d, is the slip.
    return _lesson(
        "fractions.add_same_bottom",
        "fractions.operations",
        "learn",
        "Work out",
        f"{a}/{d} + {b}/{d}",
        _fraction(Fraction(a + b, d)),
        ARITHMETIC,
        {"a": a, "b": b, "d": d},
        [],
    )


def _add_different_bottoms(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    b, d = _pick(rng, fixed, "bottoms", lambda: rng.choice([(2, 3), (2, 5), (3, 4), (2, 7), (3, 5), (4, 5)]))
    a = _pick(rng, fixed, "a", lambda: rng.choice([n for n in range(1, b) if Fraction(n, b).denominator == b]))
    c = _pick(rng, fixed, "c", lambda: rng.choice([n for n in range(1, d) if Fraction(n, d).denominator == d]))
    return _lesson(
        "fractions.add_different_bottoms",
        "fractions.operations",
        "apply",
        "Work out",
        f"{a}/{b} + {c}/{d}",
        _fraction(Fraction(a, b) + Fraction(c, d)),
        ARITHMETIC,
        {"a": a, "b": b, "c": c, "d": d},
        [],
    )


def _multiply_fractions(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a, b = _pick(rng, fixed, "first", lambda: _proper(rng, None, 6))
    c, d = _pick(rng, fixed, "second", lambda: _proper(rng, None, 6))
    return _lesson(
        "fractions.multiply",
        "fractions.operations",
        "apply",
        "Work out",
        f"{a}/{b} * {c}/{d}",
        _fraction(Fraction(a, b) * Fraction(c, d)),
        ARITHMETIC,
        {"a": a, "b": b, "c": c, "d": d},
        [],
    )


def _subtract_fractions(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    b, d = _pick(rng, fixed, "bottoms", lambda: rng.choice([(2, 6), (3, 6), (4, 8), (2, 10), (5, 10), (3, 12)]))
    a = _pick(rng, fixed, "a", lambda: 1)
    c = _pick(rng, fixed, "c", lambda: rng.randint(1, d * a // b - 1) if d * a // b > 1 else 1)
    value = Fraction(a, b) - Fraction(c, d)
    return _lesson(
        "fractions.subtract",
        "fractions.operations",
        "challenge",
        "Work out",
        f"{a}/{b} - {c}/{d}",
        _fraction(value),
        ARITHMETIC,
        {"a": a, "b": b, "c": c, "d": d},
        [],
    )


_DECIMAL_BOTTOMS = (2, 4, 5, 10, 20, 25)


def _to_decimal(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    while True:
        bottom = _pick(rng, fixed, "q", lambda: rng.choice(_DECIMAL_BOTTOMS))
        top = _pick(rng, fixed, "p", lambda bottom=bottom: rng.randint(1, bottom - 1))
        if Fraction(top, bottom).denominator == bottom:
            break
        fixed = None
    value = Fraction(top, bottom)
    return _lesson(
        "fractions.to_decimal",
        "fractions.decimals",
        "learn",
        "Write as a decimal:",
        f"{top}/{bottom}",
        _decimal(value),
        DECIMAL,
        {"p": top, "q": bottom},
        [],
    )


def _to_fraction(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    while True:
        bottom = _pick(rng, fixed, "q", lambda: rng.choice((4, 5, 20, 25)))
        top = _pick(rng, fixed, "p", lambda bottom=bottom: rng.randint(1, bottom - 1))
        if Fraction(top, bottom).denominator == bottom:
            break
        fixed = None
    value = Fraction(top, bottom)
    return _lesson(
        "fractions.to_fraction",
        "fractions.decimals",
        "apply",
        "Write as a fraction in its lowest terms:",
        _decimal(value),
        _fraction(value),
        LOWEST_TERMS,
        {"p": top, "q": bottom},
        [],
    )


def _add_decimals(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(11, 89))
    b = _pick(rng, fixed, "b", lambda: rng.randint(3, 9))
    first, second = Fraction(a, 100), Fraction(b, 10)
    # 0.35 + 0.6: lining up the last digits, not the points, gives 0.41.
    return _lesson(
        "fractions.add_decimals",
        "fractions.decimals",
        "challenge",
        "Work out",
        f"{_decimal(first)} + {_decimal(second)}",
        _decimal(first + second),
        DECIMAL,
        {"a": a, "b": b},
        [],
    )


# Geometry ------------------------------------------------------------------------------------------------------

_NUMBER_ONLY = "Write the number only: the units are"


def _rectangle_perimeter(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    length = _pick(rng, fixed, "l", lambda: rng.randint(5, 15))
    width = _pick(rng, fixed, "w", lambda: rng.randint(2, length - 1))
    shown = f"A rectangle is {length} cm long and {width} cm wide. Find its perimeter. ({_NUMBER_ONLY} cm.)"
    return _lesson(
        "geometry.rectangle_perimeter",
        "geometry.perimeter",
        "learn",
        "",
        f"2({length} + {width})",
        str(2 * (length + width)),
        ARITHMETIC,
        {"l": length, "w": width},
        [],
        shown=shown,
    )


def _decimal_perimeter(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    length = Fraction(_pick(rng, fixed, "l2", lambda: rng.randint(9, 25)), 2)
    width = _pick(rng, fixed, "w", lambda: rng.randint(2, 6))
    shown = f"A rectangle is {_number(length)} m long and {width} m wide. Find its perimeter. ({_NUMBER_ONLY} m.)"
    return _lesson(
        "geometry.decimal_perimeter",
        "geometry.perimeter",
        "apply",
        "",
        f"2({_number(length)} + {width})",
        _number(2 * (length + width)),
        ARITHMETIC,
        {"l2": int(length * 2), "w": width},
        [],
        shown=shown,
    )


def _square_from_area(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    side = _pick(rng, fixed, "s", lambda: rng.randint(3, 12))
    shown = f"A square has an area of {side * side} cm². Find its perimeter. ({_NUMBER_ONLY} cm.)"
    return _lesson(
        "geometry.square_from_area",
        "geometry.perimeter",
        "challenge",
        "",
        f"4 * sqrt({side * side})",
        str(4 * side),
        ARITHMETIC,
        {"s": side},
        [],
        shown=shown,
    )


def _rectangle_area(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    length = _pick(rng, fixed, "l", lambda: rng.randint(4, 13))
    width = _pick(rng, fixed, "w", lambda: rng.randint(2, 9))
    shown = f"A rectangle is {length} cm long and {width} cm wide. Find its area. ({_NUMBER_ONLY} cm².)"
    return _lesson(
        "geometry.rectangle_area",
        "geometry.area",
        "learn",
        "",
        f"{length} * {width}",
        str(length * width),
        ARITHMETIC,
        {"l": length, "w": width},
        [],
        shown=shown,
    )


def _triangle_area(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    base = _pick(rng, fixed, "b", lambda: rng.randint(3, 8) * 2)
    height = _pick(rng, fixed, "h", lambda: rng.randint(3, 11))
    shown = (
        f"A triangle has a base of {base} cm and a perpendicular height of {height} cm. Find its area. ({_NUMBER_ONLY} cm².)"
    )
    return _lesson(
        "geometry.triangle_area",
        "geometry.area",
        "apply",
        "",
        f"1/2 * {base} * {height}",
        str(base * height // 2),
        ARITHMETIC,
        {"b": base, "h": height},
        [],
        shown=shown,
    )


def _odd_triangle_area(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    base = _pick(rng, fixed, "b", lambda: rng.randint(2, 7) * 2 + 1)
    height = _pick(rng, fixed, "h", lambda: rng.randint(1, 5) * 2 + 1)
    shown = (
        f"A triangle has a base of {base} m and a perpendicular height of {height} m. Find its area. ({_NUMBER_ONLY} m².)"
    )
    return _lesson(
        "geometry.odd_triangle_area",
        "geometry.area",
        "challenge",
        "",
        f"1/2 * {base} * {height}",
        _number(Fraction(base * height, 2)),
        ARITHMETIC,
        {"b": base, "h": height},
        [],
        shown=shown,
    )


# Trigonometry --------------------------------------------------------------------------------------------------

_RATIO_SIDES = {"sin": ("opposite", "hypotenuse"), "cos": ("adjacent", "hypotenuse"), "tan": ("opposite", "adjacent")}


def _ratio(template: str, tier: str, ratios: tuple[str, ...]):  # type: ignore[no-untyped-def]
    def build(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
        opposite, adjacent, hypotenuse = _pick(rng, fixed, "triple", lambda: rng.choice(_TRIPLES))
        ratio = _pick(rng, fixed, "ratio", lambda: rng.choice(ratios))
        lengths = {"opposite": opposite, "adjacent": adjacent, "hypotenuse": hypotenuse}
        top, bottom = (lengths[side] for side in _RATIO_SIDES[ratio])
        shown = (
            f"A right-angled triangle has angle A. The side opposite A is {opposite}, the side adjacent to A is "
            f"{adjacent} and the hypotenuse is {hypotenuse}. Write {ratio} A as a fraction in its lowest terms."
        )
        return _lesson(
            template,
            "trigonometry.ratios",
            tier,
            "",
            f"{top}/{bottom}",  # type: ignore[arg-type]
            _fraction(Fraction(top, bottom)),
            LOWEST_TERMS,
            {"triple": (opposite, adjacent, hypotenuse), "ratio": ratio},
            [],
            shown=shown,
        )

    return build


# Only the values that are whole numbers or simple fractions, in degrees.
_EXACT = {
    ("sin", 0): Fraction(0),
    ("sin", 30): Fraction(1, 2),
    ("sin", 90): Fraction(1),
    ("cos", 0): Fraction(1),
    ("cos", 60): Fraction(1, 2),
    ("cos", 90): Fraction(0),
    ("tan", 0): Fraction(0),
    ("tan", 45): Fraction(1),
}


def _exact_value(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    ratio, angle = _pick(
        rng, fixed, "at", lambda: rng.choice([("sin", 30), ("cos", 60), ("sin", 90), ("cos", 0), ("tan", 45)])
    )
    angle = int(angle)
    k = _pick(rng, fixed, "k", lambda: rng.randint(2, 7))
    value = k * _EXACT[(ratio, angle)]
    return _lesson(
        "trigonometry.exact_value",
        "trigonometry.exact_values",
        "learn",
        "Work out",
        f"{k}{ratio}({angle})",
        _fraction(value),
        ARITHMETIC,
        {"at": (ratio, angle), "k": k},
        [],
        shown=f"{k} {ratio} {angle}°",
    )


def _exact_sum(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    first = _pick(rng, fixed, "first", lambda: rng.choice([("sin", 30), ("cos", 60), ("tan", 45), ("sin", 90)]))
    second = _pick(rng, fixed, "second", lambda: rng.choice([("sin", 30), ("cos", 60), ("cos", 0), ("tan", 45)]))
    k = _pick(rng, fixed, "k", lambda: rng.randint(2, 4))
    first, second = tuple(first), tuple(second)
    value = k * _EXACT[first] + _EXACT[second]
    math = f"{k}{first[0]}({first[1]}) + {second[0]}({second[1]})"
    shown = f"{k} {first[0]} {first[1]}° + {second[0]} {second[1]}°"
    return _lesson(
        "trigonometry.exact_sum",
        "trigonometry.exact_values",
        "apply",
        "Work out",
        math,
        _fraction(value),
        ARITHMETIC,
        {"first": first, "second": second, "k": k},
        [],
        shown=shown,
    )


def _side_from_angle(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    hypotenuse = _pick(rng, fixed, "h", lambda: rng.randint(2, 12) * 2)
    ratio, angle = _pick(rng, fixed, "at", lambda: rng.choice([("sin", 30), ("cos", 60)]))
    side = "opposite" if ratio == "sin" else "adjacent to"
    shown = (
        f"A right-angled triangle has a hypotenuse of {hypotenuse} cm and an angle of {angle}°. Find the side "
        f"{side} that angle. ({_NUMBER_ONLY} cm.)"
    )
    return _lesson(
        "trigonometry.side_from_angle",
        "trigonometry.applying",
        "challenge",
        "",
        f"{hypotenuse} * {ratio}({angle})",
        str(hypotenuse // 2),
        ARITHMETIC,
        {"h": hypotenuse, "at": (ratio, angle)},
        [],
        shown=shown,
    )


# Calculus ------------------------------------------------------------------------------------------------------


def _power(coefficient: int, power: int) -> str:
    lead = "" if coefficient == 1 else str(coefficient)
    if power == 0:
        return str(coefficient)
    return f"{lead}x" if power == 1 else f"{lead}x^{power}"


def _differentiate_power(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    n = _pick(rng, fixed, "n", lambda: rng.randint(2, 6))
    return _lesson(
        "calculus.differentiate_power",
        "calculus.power_rule",
        "learn",
        "Differentiate:",
        f"d/dx({_power(1, n)})",
        _power(n, n - 1),
        DIFFERENTIATION,
        {"n": n},
        [],
    )


def _differentiate_term(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 9))
    n = _pick(rng, fixed, "n", lambda: rng.randint(2, 5))
    return _lesson(
        "calculus.differentiate_term",
        "calculus.power_rule",
        "apply",
        "Differentiate:",
        f"d/dx({_power(a, n)})",
        _power(a * n, n - 1),
        DIFFERENTIATION,
        {"a": a, "n": n},
        [],
    )


def _differentiate_sum(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(2, 6))
    b = _pick(rng, fixed, "b", lambda: rng.randint(2, 9))
    c = _pick(rng, fixed, "c", lambda: rng.randint(1, 9))
    expression = f"{_power(a, 3)} + {_power(b, 1)} + {c}"
    return _lesson(
        "calculus.differentiate_sum",
        "calculus.power_rule",
        "challenge",
        "Differentiate:",
        f"d/dx({expression})",
        f"{_power(3 * a, 2)} + {b}",
        DIFFERENTIATION,
        {"a": a, "b": b, "c": c},
        [],
    )


def _integrate_power(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    n = _pick(rng, fixed, "n", lambda: rng.randint(1, 5))
    integrand = _power(n + 1, n)
    return _lesson(
        "calculus.integrate_power",
        "calculus.integration",
        "learn",
        "Integrate:",
        f"∫ {integrand} dx",
        f"{_power(1, n + 1)} + C",
        INTEGRATION,
        {"n": n},
        [],
    )


def _integrate_term(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    n = _pick(rng, fixed, "n", lambda: rng.randint(1, 3))
    k = _pick(rng, fixed, "k", lambda: rng.randint(2, 4))
    integrand = _power(k * (n + 1), n)
    return _lesson(
        "calculus.integrate_term",
        "calculus.integration",
        "apply",
        "Integrate:",
        f"∫ {integrand} dx",
        f"{_power(k, n + 1)} + C",
        INTEGRATION,
        {"n": n, "k": k},
        [],
    )


def _integrate_sum(rng: random.Random, fixed: dict[str, Any] | None) -> LessonQuestion:
    a = _pick(rng, fixed, "a", lambda: rng.randint(1, 3))
    b = _pick(rng, fixed, "b", lambda: rng.randint(1, 4))
    integrand = f"{_power(3 * a, 2)} + {_power(2 * b, 1)}"
    return _lesson(
        "calculus.integrate_sum",
        "calculus.integration",
        "challenge",
        "Integrate:",
        f"∫ ({integrand}) dx",
        f"{_power(a, 3)} + {_power(b, 2)} + C",
        INTEGRATION,
        {"a": a, "b": b},
        [],
    )


_ADD = ("Line the digits up by place value.", "Add the ones first, and carry a ten if you need to.")
_TAKE = ("Line the digits up by place value.", "Take away the ones first. If you can't, borrow a ten.")
_TIMES = ("Count up in steps of one of the numbers.", "Use a times table fact you know.")
_SHARE = ("What times the smaller number makes the bigger one?", "Think of the times table backwards.")
_ORDER = ("Brackets first, then × and ÷, then + and −.", "Write one step on each line.")
_SIMPLEST = ("What number divides the top and the bottom?", "Divide both by it. Can you divide again?")
_FRACTION_SUM = ("Are the bottoms the same?", "With the same bottom, add the tops and keep the bottom.")
_DECIMALS = ("Can the bottom become ten or a hundred?", "Multiply the top and the bottom by the same number.")
_LENGTHS = ("Perimeter is the distance all the way round.", "Add every side: a rectangle has two of each.")
_AREAS = ("Area is the space inside.", "Rectangle: length × width. Triangle: half of base × height.")
_RATIO = ("SOH CAH TOA: which two sides does this ratio use?", "Write it as a fraction, then simplify.")
_EXACT_HINTS = (
    "Picture the unit circle or the special triangles.",
    "Half of an equilateral triangle has the angles you need.",
)
_POWER = ("Bring the power down in front.", "Then take one off the power.")
_INTEGRATE = ("Add one to the power.", "Divide by the new power, and add + C.")

STARTER: dict[str, Template] = {
    template.id: template
    for template in (
        Template(
            "arithmetic.add", "arithmetic.add_subtract", "learn", ARITHMETIC, (), "a + b, a 12-68, b 11-29", _ADD, _add
        ),
        Template(
            "arithmetic.subtract",
            "arithmetic.add_subtract",
            "apply",
            ARITHMETIC,
            (),
            "a - b, a 41-95, a - b at least 11",
            _TAKE,
            _subtract,
        ),
        Template(
            "arithmetic.negatives",
            "arithmetic.add_subtract",
            "challenge",
            ARITHMETIC,
            ("sign_error",),
            "b - a with a > b, a 6-15",
            ("Is the answer above or below zero?", "Count down past zero."),
            _negatives,
        ),
        Template(
            "arithmetic.multiply", "arithmetic.multiply_divide", "learn", ARITHMETIC, (), "a × b, 3-9", _TIMES, _multiply
        ),
        Template(
            "arithmetic.divide",
            "arithmetic.multiply_divide",
            "apply",
            ARITHMETIC,
            (),
            "ab ÷ a, a 3-9, b 3-12",
            _SHARE,
            _divide,
        ),
        Template(
            "arithmetic.multiply_larger",
            "arithmetic.multiply_divide",
            "challenge",
            ARITHMETIC,
            (),
            "a × b, a 12-19, b 3-8",
            ("Split the bigger number into tens and ones.", "Multiply each part, then add."),
            _multiply_larger,
        ),
        Template(
            "arithmetic.multiply_first",
            "arithmetic.order_of_operations",
            "learn",
            ARITHMETIC,
            (),
            "a + b × c",
            _ORDER,
            _multiply_first,
        ),
        Template(
            "arithmetic.brackets_first",
            "arithmetic.order_of_operations",
            "apply",
            ARITHMETIC,
            (),
            "(a - b) × c, a > b",
            _ORDER,
            _brackets_first,
        ),
        Template(
            "arithmetic.divide_and_subtract",
            "arithmetic.order_of_operations",
            "challenge",
            ARITHMETIC,
            (),
            "a - dq ÷ d × b, never negative",
            _ORDER,
            _divide_and_subtract,
        ),
        Template(
            "fractions.simplify",
            "fractions.equivalent",
            "learn",
            LOWEST_TERMS,
            (),
            "kp/kq, q 3-7, k 2-3",
            _SIMPLEST,
            _simplify("fractions.simplify", "learn", (2, 3)),
        ),
        Template(
            "fractions.simplify_larger",
            "fractions.equivalent",
            "apply",
            LOWEST_TERMS,
            (),
            "kp/kq, q 3-7, k 4-9",
            _SIMPLEST,
            _simplify("fractions.simplify_larger", "apply", (4, 9)),
        ),
        Template(
            "fractions.simplify_challenge",
            "fractions.equivalent",
            "challenge",
            LOWEST_TERMS,
            (),
            "kp/kq, q 3-7, k 6-12",
            _SIMPLEST,
            _simplify("fractions.simplify_challenge", "challenge", (6, 12)),
        ),
        Template(
            "fractions.add_same_bottom",
            "fractions.operations",
            "learn",
            ARITHMETIC,
            (),
            "a/d + b/d, a + b < d",
            _FRACTION_SUM,
            _add_same_bottom,
        ),
        Template(
            "fractions.add_different_bottoms",
            "fractions.operations",
            "apply",
            ARITHMETIC,
            (),
            "a/b + c/d, small co-prime bottoms",
            ("Find a bottom both go into.", "Change each fraction, then add the tops."),
            _add_different_bottoms,
        ),
        Template(
            "fractions.multiply",
            "fractions.operations",
            "apply",
            ARITHMETIC,
            (),
            "a/b × c/d, proper, bottoms 3-6",
            ("Multiply the tops, and multiply the bottoms.", "Then simplify if you can."),
            _multiply_fractions,
        ),
        Template(
            "fractions.subtract",
            "fractions.operations",
            "challenge",
            ARITHMETIC,
            (),
            "1/b - c/d, d a multiple of b, positive",
            ("Make the bottoms the same first.", "Then take away the tops."),
            _subtract_fractions,
        ),
        Template(
            "fractions.to_decimal",
            "fractions.decimals",
            "learn",
            DECIMAL,
            (),
            "p/q, q in 2, 4, 5, 10, 20, 25",
            _DECIMALS,
            _to_decimal,
        ),
        Template(
            "fractions.to_fraction",
            "fractions.decimals",
            "apply",
            LOWEST_TERMS,
            (),
            "a decimal from p/q, q in 4, 5, 20, 25",
            ("Write it over ten or a hundred.", "Then simplify."),
            _to_fraction,
        ),
        Template(
            "fractions.add_decimals",
            "fractions.decimals",
            "challenge",
            DECIMAL,
            (),
            "0.ab + 0.c",
            ("Line up the decimal points.", "Fill a gap with a zero so both have the same number of places."),
            _add_decimals,
        ),
        Template(
            "geometry.rectangle_perimeter",
            "geometry.perimeter",
            "learn",
            ARITHMETIC,
            (),
            "2(l + w), l 5-15, w < l",
            _LENGTHS,
            _rectangle_perimeter,
        ),
        Template(
            "geometry.decimal_perimeter",
            "geometry.perimeter",
            "apply",
            ARITHMETIC,
            (),
            "2(l + w), l a half 4.5-12.5, w 2-6",
            _LENGTHS,
            _decimal_perimeter,
        ),
        Template(
            "geometry.square_from_area",
            "geometry.perimeter",
            "challenge",
            ARITHMETIC,
            (),
            "4 × √(s²), s 3-12",
            ("What number times itself makes the area?", "That's one side. A square has four."),
            _square_from_area,
        ),
        Template(
            "geometry.rectangle_area",
            "geometry.area",
            "learn",
            ARITHMETIC,
            (),
            "l × w, l 4-13, w 2-9",
            _AREAS,
            _rectangle_area,
        ),
        Template(
            "geometry.triangle_area",
            "geometry.area",
            "apply",
            ARITHMETIC,
            (),
            "½ × b × h, b even 6-16, h 3-11",
            _AREAS,
            _triangle_area,
        ),
        Template(
            "geometry.odd_triangle_area",
            "geometry.area",
            "challenge",
            ARITHMETIC,
            (),
            "½ × b × h, b and h odd",
            ("Multiply base by height first.", "Then halve it: the answer can be a decimal."),
            _odd_triangle_area,
        ),
        Template(
            "trigonometry.sin_ratio",
            "trigonometry.ratios",
            "learn",
            LOWEST_TERMS,
            (),
            "sin from a Pythagorean triple",
            _RATIO,
            _ratio("trigonometry.sin_ratio", "learn", ("sin",)),
        ),
        Template(
            "trigonometry.cos_tan_ratio",
            "trigonometry.ratios",
            "apply",
            LOWEST_TERMS,
            (),
            "cos or tan from a Pythagorean triple",
            _RATIO,
            _ratio("trigonometry.cos_tan_ratio", "apply", ("cos", "tan")),
        ),
        Template(
            "trigonometry.any_ratio",
            "trigonometry.ratios",
            "challenge",
            LOWEST_TERMS,
            (),
            "sin, cos or tan from a Pythagorean triple",
            _RATIO,
            _ratio("trigonometry.any_ratio", "challenge", ("sin", "cos", "tan")),
        ),
        Template(
            "trigonometry.exact_value",
            "trigonometry.exact_values",
            "learn",
            ARITHMETIC,
            (),
            "k × (sin 30, cos 60, sin 90, cos 0 or tan 45), k 2-7, in degrees",
            _EXACT_HINTS,
            _exact_value,
        ),
        Template(
            "trigonometry.exact_sum",
            "trigonometry.exact_values",
            "apply",
            ARITHMETIC,
            (),
            "k·f(a) + g(b), whole-number and half values only",
            _EXACT_HINTS,
            _exact_sum,
        ),
        Template(
            "trigonometry.side_from_angle",
            "trigonometry.applying",
            "challenge",
            ARITHMETIC,
            (),
            "h × sin 30 or h × cos 60, h even 4-24",
            ("Which ratio uses the side you want and the hypotenuse?", "Multiply the hypotenuse by that ratio."),
            _side_from_angle,
        ),
        Template(
            "calculus.differentiate_power",
            "calculus.power_rule",
            "learn",
            DIFFERENTIATION,
            (),
            "x^n, n 2-6",
            _POWER,
            _differentiate_power,
        ),
        Template(
            "calculus.differentiate_term",
            "calculus.power_rule",
            "apply",
            DIFFERENTIATION,
            (),
            "ax^n, a 2-9, n 2-5",
            _POWER,
            _differentiate_term,
        ),
        Template(
            "calculus.differentiate_sum",
            "calculus.power_rule",
            "challenge",
            DIFFERENTIATION,
            (),
            "ax^3 + bx + c",
            ("Differentiate each term on its own.", "A number on its own differentiates to zero."),
            _differentiate_sum,
        ),
        Template(
            "calculus.integrate_power",
            "calculus.integration",
            "learn",
            INTEGRATION,
            (),
            "(n+1)x^n, n 1-5",
            _INTEGRATE,
            _integrate_power,
        ),
        Template(
            "calculus.integrate_term",
            "calculus.integration",
            "apply",
            INTEGRATION,
            (),
            "k(n+1)x^n, n 1-3, k 2-4",
            _INTEGRATE,
            _integrate_term,
        ),
        Template(
            "calculus.integrate_sum",
            "calculus.integration",
            "challenge",
            INTEGRATION,
            (),
            "3a x^2 + 2b x",
            ("Integrate each term on its own.", "One + C at the end is enough."),
            _integrate_sum,
        ),
    )
}
TEMPLATES.update(STARTER)
