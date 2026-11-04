"""Practice questions from fixed templates, with answers worked out in integers and never by a model.

Each template is checked by the verifier before it's shown: the question with its answer has to be
valid. Answering runs through the normal pipeline, with the question placed in front of what the
student wrote, so a wrong answer is a statement the verifier can already judge: `d/dx(x^3) = 2x^2`.
"""

from __future__ import annotations

import random
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction

from quack.services.handwriting import RESTATED_QUESTION, HandwritingReading, ReadLine
from quack.services.math_parser import MathParseError, normalise, parse_expression, parse_statement
from quack.services.math_verifier import LineInput, TaskIntent, names_given_values
from quack.services.verifier_pool import VerificationFailed, shared_pool

MAX_TRIES = 8
SUPPORTED = (
    "addition_subtraction",
    "multiplication",
    "division",
    "combining_like_terms",
    "expanding_brackets",
    "solving_equations",
    "power_rule",
    "sum_and_constant_rules",
    "integration",
)
_SUPERSCRIPTS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
_SUBSCRIPTS = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
# What a question's verification method says the student is doing. An equation isn't one line's claim.
EQUATION = "equation"
INTENT_BY_METHOD: dict[str, TaskIntent] = {EQUATION: "solve", "integration": "integrate"}


@dataclass(frozen=True)
class Question:
    topic: str
    difficulty: str
    # In the notation the parser reads, and as a student would write it.
    math: str
    display: str
    expected: str
    method: str


def _pretty(math: str) -> str:
    out, position = "", 0
    while position < len(math):
        if math[position] in "^_":
            end = position + 1
            while end < len(math) and math[end].isdigit():
                end += 1
            out += math[position + 1 : end].translate(_SUPERSCRIPTS if math[position] == "^" else _SUBSCRIPTS)
            position = end
        else:
            out += "×" if math[position] == "*" else math[position]
            position += 1
    return out


def _polynomial(terms: dict[int, int]) -> str:
    """Written highest power first: {2: 3, 1: -1, 0: 2} is 3x^2 - x + 2."""

    text = ""
    for power in sorted(terms, reverse=True):
        coefficient = terms[power]
        if coefficient == 0:
            continue
        size = abs(coefficient)
        variable = "" if power == 0 else "x" if power == 1 else f"x^{power}"
        body = f"{size}{variable}" if size != 1 or not variable else variable
        sign = "-" if coefficient < 0 else "+"
        text += (f"-{body}" if coefficient < 0 else body) if not text else f" {sign} {body}"
    return text or "0"


def _question(topic: str, difficulty: str, math: str, expected: str, method: str, display: str | None = None) -> Question:
    return Question(topic, difficulty, math, f"{display or _pretty(math)} = ?", expected, method)


def _adding(rng: random.Random, difficulty: str) -> Question:
    if difficulty == "basic":
        a, b = rng.randint(12, 89), rng.randint(5, 49)
        if rng.random() < 0.5:
            return _question("addition_subtraction", difficulty, f"{a} + {b}", str(a + b), "arithmetic")
        a, b = max(a, b), min(a, b)
        return _question("addition_subtraction", difficulty, f"{a} - {b}", str(a - b), "arithmetic")
    # Negative numbers, where most of the slips are.
    a, b = rng.randint(2, 19), rng.randint(2, 19)
    if rng.random() < 0.5:
        return _question("addition_subtraction", difficulty, f"-{a} + {b}", str(b - a), "arithmetic")
    return _question("addition_subtraction", difficulty, f"{a} - {a + b}", str(-b), "arithmetic")


def _multiplication(rng: random.Random, difficulty: str) -> Question:
    a = rng.randint(2, 9) if difficulty == "basic" else rng.randint(11, 19)
    b = rng.randint(2, 9)
    return _question("multiplication", difficulty, f"{a} * {b}", str(a * b), "arithmetic", f"{a} × {b}")


def _division(rng: random.Random, difficulty: str) -> Question:
    if difficulty == "basic":
        b = rng.randint(2, 9)
        if rng.random() < 0.5:
            quotient = rng.randint(2, 9)
            math = f"{b * quotient} ÷ {b}"
            return _question("division", difficulty, math, str(quotient), "arithmetic", math)
        a = rng.randint(1, b - 1) if b > 2 else 1
        value = Fraction(a, b)
        expected = f"{value.numerator}/{value.denominator}"
        return _question("division", difficulty, f"{a} ÷ {b}", expected, "arithmetic", f"{a} ÷ {b}")
    b = rng.choice([2, 3, 4, 5])
    d = b * rng.choice([2, 3])
    a, c = 1, rng.randint(1, d - 1)
    value = Fraction(a, b) + Fraction(c, d)
    expected = str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"
    return _question("division", difficulty, f"{a}/{b} + {c}/{d}", expected, "arithmetic")


def _expanding(rng: random.Random, difficulty: str) -> Question:
    if difficulty == "basic":
        a, b = rng.randint(2, 9), rng.randint(1, 9)
        return _question("expanding_brackets", difficulty, f"{a}(x + {b})", _polynomial({1: a, 0: a * b}), "identity")
    a, b = rng.randint(1, 6), rng.randint(1, 6)
    return _question(
        "expanding_brackets", difficulty, f"(x + {a})(x + {b})", _polynomial({2: 1, 1: a + b, 0: a * b}), "identity"
    )


def _like_terms(rng: random.Random, difficulty: str) -> Question:
    a, b, c = rng.randint(2, 9), rng.randint(2, 9), rng.randint(1, 9)
    if difficulty == "basic":
        return _question("combining_like_terms", difficulty, f"{a}x + {b}x + {c}", _polynomial({1: a + b, 0: c}), "identity")
    d = rng.randint(1, 9)
    return _question(
        "combining_like_terms", difficulty, f"{a}x + {c} + {b}x + {d}", _polynomial({1: a + b, 0: c + d}), "identity"
    )


def _equation_question(difficulty: str, math: str, solution: int) -> Question:
    return Question("solving_equations", difficulty, math, f"Solve {math}", f"x = {solution}", EQUATION)


def _equations(rng: random.Random, difficulty: str) -> Question:
    a, solution, b = rng.randint(2, 9), rng.randint(1, 9), rng.randint(1, 9)
    if difficulty == "basic":
        return _equation_question(difficulty, f"{a}x + {b} = {a * solution + b}", solution)
    # With x on both sides.
    c = rng.randint(1, a - 1) if a > 2 else 1
    right = (a - c) * solution + b
    # Written as anyone would write it.
    other = "x" if c == 1 else f"{c}x"
    return _equation_question(difficulty, f"{a}x + {b} = {other} + {right}", solution)


def _integrals(rng: random.Random, difficulty: str) -> Question:
    """Chosen so that the answers come out whole: 3x^2 goes to x^3, and 6x to 3x^2."""

    power = rng.randint(1, 4)
    scale = rng.randint(1, 4)
    integrand = {power: scale * (power + 1)}
    if difficulty == "basic":
        math = f"∫ {_polynomial(integrand)} dx"
        return _question("integration", difficulty, math, f"{_polynomial({power + 1: scale})} + C", "integration")
    top = rng.randint(1, 3)
    math = f"∫_0^{top} {_polynomial(integrand)} dx"
    return _question("integration", difficulty, math, str(scale * top ** (power + 1)), "arithmetic")


def _derivative_prompt(terms: dict[int, int], order: list[int]) -> str:
    return f"d/dx({_polynomial({power: terms[power] for power in order})})"


def _derivative_of(terms: dict[int, int]) -> str:
    return _polynomial({power - 1: coefficient * power for power, coefficient in terms.items() if power > 0})


def _power_rule(rng: random.Random, difficulty: str) -> Question:
    power = rng.randint(2, 6)
    terms = {power: 1 if difficulty == "basic" else rng.randint(2, 9)}
    return _question("power_rule", difficulty, _derivative_prompt(terms, [power]), _derivative_of(terms), "differentiation")


def _sum_rules(rng: random.Random, difficulty: str) -> Question:
    terms = {2: rng.randint(2, 9), 1: rng.randint(2, 9)}
    if difficulty != "basic":
        terms = {3: rng.randint(2, 9), 1: rng.randint(2, 9), 0: rng.randint(1, 9)}
    return _question(
        "sum_and_constant_rules", difficulty, _derivative_prompt(terms, sorted(terms, reverse=True)),
        _derivative_of(terms), "differentiation",
    )


_TEMPLATES = {
    "addition_subtraction": _adding,
    "solving_equations": _equations,
    "integration": _integrals,
    "multiplication": _multiplication,
    "division": _division,
    "expanding_brackets": _expanding,
    "combining_like_terms": _like_terms,
    "power_rule": _power_rule,
    "sum_and_constant_rules": _sum_rules,
}


def generate_question(topic: str, difficulty: str, rng: random.Random) -> Question:
    return _TEMPLATES[topic](rng, difficulty)


async def is_checkable(question: Question, timeout_s: float) -> bool:
    """Would the verifier accept the answer, and call this question finished by it?"""

    if question.method == EQUATION:
        line = question.expected
        lines = [LineInput(0, line, continues="yes", intent="solve", given=question.math)]
    else:
        line = f"{question.math} = {question.expected}"
        lines = [LineInput(0, line, posed=True)]
    try:
        report, _ = await shared_pool.verify(lines, frozenset(), timeout_s)
    except VerificationFailed:
        return False
    if report.status != "valid":
        return False
    return await shared_pool.completes(question.math, question.expected, line, timeout_s)


async def new_question(topic: str, difficulty: str, avoid: set[str], timeout_s: float) -> Question | None:
    rng = random.Random(uuid.uuid4().int)
    for _ in range(MAX_TRIES):
        question = generate_question(topic, difficulty, rng)
        if question.math not in avoid and await is_checkable(question, timeout_s):
            return question
    return None


def _with_equation(text: str, equation: str) -> str:
    """An answer under an equation: a bare `2` says x = 2. Working stays as it was written."""

    stripped = text.strip()
    if "=" in stripped or not _is_plain_value(stripped):
        return stripped
    return f"{_variable_of(equation)} = {stripped}"


def _is_plain_value(text: str) -> bool:
    try:
        return parse_expression(text).kind in ("num", "neg", "div")
    except MathParseError:
        return False


def _variable_of(equation: str) -> str:
    return next(letter for letter in equation if letter.isalpha())


def _with_question(text: str, question: str, first: bool) -> str:
    stripped = text.strip()
    if stripped.startswith("="):
        return f"{question} {stripped}" if first else stripped
    if "=" in stripped:
        return stripped
    return f"{question} = {stripped}" if first else f"= {stripped}"




def _plain(text: str) -> str:
    try:
        out = normalise(text)
    except MathParseError:
        out = text
    return "".join(out.split()).casefold().replace("*", "").replace("·", "")


def _edits(one: str, other: str) -> int:
    """Levenshtein distance, over characters."""

    before = list(range(len(other) + 1))
    for i, a in enumerate(one, 1):
        now = [i]
        for j, b in enumerate(other, 1):
            now.append(min(before[j] + 1, now[j - 1] + 1, before[j - 1] + (a != b)))
        before = now
    return before[-1]


def _is_a_solution(text: str) -> bool:
    """`x = 10`: an answer to an equation, never a copy of one."""

    sides = text.split("=")
    return len(sides) == 2 and len(sides[0].strip()) == 1 and sides[0].strip().isalpha() and _is_plain_value(sides[1])


def restates_the_question(text: str, question: str) -> bool:
    """Is this line the question copied out, perhaps with a character or two read differently?

    On the iPad on 2026-11-04 (evening), a student's copy of `7(x + 6) - 4(x - 2)` was read by both readings as
    `7(x+8)-4(x-2)`, and checked as a step from the question: "the first bracket is 7(x + 6), not 7(x + 8)", which
    the student never wrote. Their copy of `7(x + 5) - 4(x - 2)` was read five different ways across checks, and
    held a right `3x + 43` below it for three and a half minutes. A copy of the question isn't working: it's
    neither right nor wrong, so it isn't checked, and the working under it is checked from the question Quack
    set. Nothing here says what the student wrote, and the answer is never compared with anything.

    Close enough is one character in six, at most three: `7(2x+5)-4(x-8)` is two from `7(x+5)-4(x-2)`; the
    first step, `7x+35-4x+8`, is six. Never a lone value or `x = 10`, which answer the question, and never a line
    whose brackets, equals signs or letters differ from the question's.
    """

    line, asked = _plain(text), _plain(question)
    if not line or not asked or line.startswith("="):
        return False
    if _is_plain_value(text) or _is_a_solution(text):
        return False
    # Working changes the shape: brackets opened, terms collected. `4x + 6 = x + 48` under `4(x + 6) = x + 48` is
    # two characters away, and a real mistake. A copy keeps every bracket, equals sign and letter.
    if any(line.count(mark) != asked.count(mark) for mark in "()="):
        return False
    if sorted(c for c in line if c.isalpha()) != sorted(c for c in asked if c.isalpha()):
        return False
    # And its signs, in order: `4(x + 6) - x = 48` moved a term, and is checked as the step it is.
    if [c for c in line if c in "+-/^="] != [c for c in asked if c in "+-/^="]:
        return False
    allowed = 1 if len(asked) <= 6 else 2 if len(asked) <= 10 else 3
    return abs(len(line) - len(asked)) <= allowed and _edits(line, asked) <= allowed


_FUNCTION_NAMES = re.compile(r"sin|cos|tan|sqrt")
# `P =`, `Area =`, `sin A =`, `opp =`: a name, and only letters, before the first equals sign.
_LABEL = re.compile(r"^\s*[A-Za-z][A-Za-z ()]*=(?!=)")
# `26 cm`, `30cm²`, `4.5 m^2`: a unit straight after a number.
_UNIT = re.compile(r"(?<=[\d.)])\s*(?:cm|mm|km|m)(?:\^\{?[23]\}?|²|³)?(?![A-Za-z])")


def is_numeric_question(question: str) -> bool:
    """No letters to stand for anything: arithmetic, fractions, lengths and areas, trigonometry in numbers."""

    return not any(c.isalpha() for c in _FUNCTION_NAMES.sub("", question))


def _as_numbers(text: str) -> str:
    """In a question with no letters, a name before `=` is a label and `cm` after a number is a unit.

    On 2026-11-04, before this, `P = 26` for a perimeter of 26 was called wrong, and so was `sin A = 6/10` for
    a sine of 3/5: the letters were read as unknowns. Only for questions with no letters of their own, so it can't
    change a line of algebra or calculus. Text only: nothing here decides what's right.
    """

    return _UNIT.sub("", _LABEL.sub("", text, count=1)).strip()


def _as_restated(line: ReadLine) -> ReadLine:
    return line.model_copy(
        update={
            "kind": "prose", "math": "", "raw_text": RESTATED_QUESTION, "legibility": "clear", "alternatives": [],
            "continues_previous": "no",
        }
    )


def answer_lines(
    question: str, values: tuple[tuple[str, str], ...] = ()
) -> Callable[[HandwritingReading], HandwritingReading]:
    """What a practice page says, once it's read: the question, and then what was written under it.

    A bare `28` under 7 × 4 says 7 * 4 = 28, so the verifier sees a claim it knows how to check, and
    everything on the page is one piece of working. `values` are what the question gives its letters,
    `If x = 3, ...`. A line saying one of them, right or wrong, is about the question rather than a
    step of the working, so it's left as written and the working starts below it.
    """

    is_equation = _is_equation_text(question)
    letters = {letter for letter, _ in values}
    numeric = not letters and not is_equation and is_numeric_question(question)

    def fill(reading: HandwritingReading) -> HandwritingReading:
        lines: list[ReadLine] = []
        first = True
        for line in reading.lines:
            if line.kind not in ("math", "incomplete") or not line.math.strip():
                lines.append(line)
                continue
            if numeric:
                cleaned = [_as_numbers(text) for text in line.alternatives]
                line = line.model_copy(update={"math": _as_numbers(line.math), "alternatives": cleaned})
                if not line.math:
                    lines.append(line)
                    continue
            if letters and states_a_value(line.math, letters):
                lines.append(line.model_copy(update={"continues_previous": "no"}))
                continue
            if first and not letters and restates_the_question(line.math, question):
                # Before anything else is written: the working under it starts from the question itself. Not
                # with values to put in (`If x = -3, ...`): there the expression written out is what the values
                # go into, and the line a substitution written again is compared with.
                lines.append(_as_restated(line))
                continue
            if is_equation:
                # What's written follows from the equation, which `LineInput.given` puts above it.
                lines.append(
                    line.model_copy(
                        update={
                            "math": _with_equation(line.math, question),
                            "alternatives": [_with_equation(text, question) for text in line.alternatives],
                            "continues_previous": "yes",
                        }
                    )
                )
                first = False
                continue
            lines.append(
                line.model_copy(
                    update={
                        "math": _with_question(line.math, question, first),
                        "raw_text": line.raw_text,
                        "alternatives": [_with_question(text, question, first) for text in line.alternatives],
                        "continues_previous": "no" if first else "yes",
                    }
                )
            )
            first = False
        return HandwritingReading(lines=lines)

    return fill


def states_a_value(text: str, letters: set[str]) -> bool:
    """`x = 3`, or `x = 2, y = -5`: only values for these letters. Model text, so parsed safely."""

    return names_given_values(text, letters)


def _is_equation_text(question: str) -> bool:
    try:
        return "=" in question and parse_statement(question).kind == "equality"
    except MathParseError:
        return False
