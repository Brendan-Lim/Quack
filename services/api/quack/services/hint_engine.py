"""Hints and explanations for a mistake the verifier has already established.

A hint has to arrive while the student is still looking at the duck. For every kind of mistake
the verifier names precisely, the first two hints are fixed templates, written from the verified
facts, so they cost nothing and take no time. For the rest, the model words a hint in the
background as soon as the alert goes up. Explanations are worded by the model on request.

The model only words the help. What is wrong, and what the right result is, come from the
verifier. Generated text is checked before it reaches the student, and a fixed template is
used if it fails twice or the model is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from collections import OrderedDict
from dataclasses import replace
from typing import Literal

import sympy as sp
from pydantic import BaseModel, ConfigDict

from quack import prompts
from quack.llm.router import ModelError, ModelRouter, Purpose
from quack.services.math_parser import MathParseError, Node, parse_statement, to_sympy
from quack.services.mistake_manager import TrackedMistake

log = logging.getLogger("quack.hints")

# How long a hint may keep the student waiting for the model before a general hint goes instead.
HINT_WAIT_S = 1.5
MAX_PREPARED = 500
MAX_WORDS = {1: 25, 2: 40, 3: 90}
MAX_EARLIER_HINTS = 5
MAX_QUESTION_CHARS = 300
FILLER = re.compile(
    r"great question|good question|let'?s dive|it'?s important to note|absolutely right|"
    r"fantastic|delve|as an ai|keep up|you'?ve got this",
    re.IGNORECASE,
)
CALLS_IT_CORRECT = re.compile(
    # "Check the step is right" tells them to look, which is fine.
    r"(?<!check )(?<!sure )(?<!whether )(?<!if )(?<!that )"
    r"\b(your|this|the)\s+(working|work|answer|step|solution|expansion|derivative|result)\s+"
    r"(is|looks|seems)\s+(correct|right|fine|good)\b|\byou got (it|this) right\b|"
    r"\bthat'?s (correct|right)\b|^correct\b",
    re.IGNORECASE,
)
# All the checker knows is what was written and what it should be. "You multiplied by 3" is a story
# about how it happened, and the student may have done nothing of the kind. A question isn't a claim.
CLAIMS_WHAT_THE_STUDENT_DID = re.compile(
    r"(?<!whether )(?<!if )(?<!did )(?<!how )(?<!what )(?<!which )(?<!when )(?<!as )(?<!check that )(?<!sure that )"
    r"\byou(?:'ve| have)?\s+(?:\w+ly\s+|just\s+|also\s+|must have\s+|might have\s+|may have\s+)?"
    r"(?:didn't|did not|made|added|subtracted|multiplied|divided|forgot|forgotten|applied|used|took|taken|treated|confused|mixed|"
    r"dropped|missed|moved|swapped|flipped|cancelled|canceled|squared|differentiated|expanded|distributed|thought|"
    r"meant|tried|assumed|ignored|left|wrote .{1,30} instead)\b",
    re.IGNORECASE,
)
_OPERATION_NOUNS = {
    "multiplication": re.compile(r"\b(?:division|dividing|divide|subtraction|addition)\b", re.IGNORECASE),
    "division": re.compile(r"\b(?:multiplication|times tables?|subtraction|addition)\b", re.IGNORECASE),
    "addition": re.compile(r"\b(?:multiplication|division|subtraction|times tables?)\b", re.IGNORECASE),
    "subtraction": re.compile(r"\b(?:multiplication|division|addition|times tables?)\b", re.IGNORECASE),
}


class GeneratedHelp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: int
    text: str
    references_step: str


_SUPERSCRIPT_RUN = re.compile("[⁰¹²³⁴-⁹]+")
NUMBER_WORDS = {
    "zero": "0", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
}


def _normalise(text: str) -> str:
    """Bring out the maths however it was written: x², $x^{2}$, full-width digits, "six"."""

    # Before NFKC, which would turn x² into x2 and lose the fact that it's a power.
    out = _SUPERSCRIPT_RUN.sub(lambda run: "^" + run.group(0), text)
    out = unicodedata.normalize("NFKC", out).replace("−", "-").replace("\\", "")
    out = re.sub(r"[${}]", "", out).lower()
    # "line 4" is a place on the page, and 6.0 is 6.
    out = re.sub(r"\b(?:line|step|question|part)\s+\d+", " ", out)
    out = re.sub(r"(?<![\d.])(\d+)\.0+(?![\d])", r"\1", out)
    for word, digit in NUMBER_WORDS.items():
        # "two functions" counts things. It doesn't give a value.
        out = re.sub(rf"\b{word}\b(?!\s+(?!is\b|was\b|plus\b|minus\b|times\b)[a-z]+s\b)", digit, out)
    # "one" is usually a pronoun ("which one changed?"), so only count it where a value goes.
    out = re.sub(r"(\b(?:be|is|equals|gives|get|makes|minus|negative|plus|it'?s)|=)\s+one\b", r"\1 1", out)
    return re.sub(r"\b(?:minus|negative)\s+(?=\d)", "-", out)


def _squash(text: str) -> str:
    return re.sub(r"[\s*]", "", _normalise(text))


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", _normalise(text)))


def _terms(text: str) -> set[str]:
    return {term for term in re.split(r"[+\-=,]", _squash(text)) if term}


def _reveals_a_term(text: str, mistake: TrackedMistake) -> str | None:
    """A term of the answer that the student didn't write, such as the 2x in x^2 + 2x + 1."""

    written = _terms(mistake.previous_step) | _terms(mistake.current_step)
    squashed = _squash(text)
    for term in _terms(mistake.expected) - written:
        if len(term) >= 2 and term in squashed:
            return term
    return None


def _states_a_solution(text: str, expected: str) -> bool:
    """`x equals 2`, or `the answer is 2`. The value may also be on the page, like the 2 in 2x = 4."""

    clean = _normalise(text)
    for solution in _normalise(expected).split(","):
        name, separator, value = solution.partition("=")
        if not separator:
            continue
        subject = rf"(?:\b{re.escape(name.strip())}|\b(?:answer|solution|result|value))"
        verb = r"(?:is|equals|equal|=|:|becomes|must be|should be|would be|has to be)"
        said = rf"(?:{subject}\s*(?:\w+\s+)?{verb}|\bit\s+is|\bit'?s|\b(?:should|would|will|must)\s+get)\s*"
        # A full stop may end the sentence, but a digit after it would make a different number.
        for form in _value_forms(value.strip()):
            written = re.escape(form).replace(r"\ ", r"\s*")
            if re.search(said + written + r"(?![\w]|\.\d)", clean):
                return True
            if re.search(r"(?<![\w.])" + written + r"\s+is\s+the\s+(?:answer|solution|result)", clean):
                return True
    return False


def _value_forms(value: str) -> list[str]:
    """1/2 is the same give-away as 0.5."""

    forms = [value]
    match = re.fullmatch(r"(-?\d+)/(\d+)", value)
    if match and int(match[2]) in (2, 4, 5, 8, 10, 20, 25, 50, 100):
        forms.append(f"{int(match[1]) / int(match[2]):g}")
    return forms


def _names_another_operation(text: str, mistake: TrackedMistake) -> bool:
    """A hint about the multiplication on a line that's a division. Checking by the inverse is fine."""

    names = _OPERATION_NOUNS.get(mistake.parsed_operation)
    return names is not None and names.search(text) is not None


def find_problems(level: int, text: str, mistake: TrackedMistake) -> list[str]:
    """Deterministic checks on generated text. Empty means it may be shown."""

    problems: list[str] = []
    clean = text.strip()
    if not clean:
        return ["the text is empty"]
    if len(clean.split()) > MAX_WORDS[level]:
        problems.append(f"it is longer than {MAX_WORDS[level]} words")
    if FILLER.search(clean):
        problems.append("it contains filler phrasing")
    if "!" in clean:
        problems.append("it uses an exclamation mark")
    if CALLS_IT_CORRECT.search(clean):
        problems.append("it calls the wrong step correct")
    if CLAIMS_WHAT_THE_STUDENT_DID.search(clean):
        problems.append("it says what the student did, which nothing has established. Say what differs instead")
    if _names_another_operation(clean, mistake):
        problems.append(f"this step is {mistake.parsed_operation}, and it talks about a different operation")

    squashed = _squash(clean)
    expected = _squash(mistake.expected)
    if level < 3:
        # A very short answer like `x` also appears in ordinary hints, so it can't be tested.
        # Brackets don't matter here: cos x gives away cos(x).
        if len(expected) >= 3 and re.sub(r"[()]", "", expected) in re.sub(r"[()]", "", squashed):
            problems.append("it gives away the corrected result")
        # A value only in the right answer, like the 6 in 2x + 6, gives it away too.
        written = _numbers(mistake.previous_step) | _numbers(mistake.current_step)
        leaked = (_numbers(mistake.expected) - written) & _numbers(clean)
        if leaked:
            problems.append(f"it reveals {', '.join(sorted(leaked))} from the corrected result")
        term = _reveals_a_term(clean, mistake)
        if term:
            problems.append(f"it reveals {term} from the corrected result")
        if _states_a_solution(clean, mistake.expected):
            problems.append("it states the solution")
    elif expected and expected not in squashed:
        problems.append(f"it must state the correct result exactly as: {mistake.expected}")
    return problems


Template = tuple[str, str, str]

_TEMPLATES: dict[str, Template] = {
    "rounding_error": (
        "Check how this was rounded.",
        "Work out the value first, then look at the digit after the one you're rounding to.",
        "Rounded as this line asks, {previous} comes to {expected}, not {current}.",
    ),
    "incorrect_distribution": (
        "Check whether you multiplied both terms inside the bracket.",
        "Look at what happened to each term in the bracket. Did every one get multiplied?",
        "Every term inside the bracket gets multiplied by what is outside it. So {previous} becomes {expected}, not {current}.",
    ),
    "power_of_sum_expanded_termwise": (
        "Try writing the bracket out twice and multiplying it through.",
        "A power does not apply to each term separately. What happens to the middle term?",
        "Raising a sum to a power also produces middle terms. So {previous} becomes {expected}, not {current}.",
    ),
    "sign_error": (
        "Have another look at the signs in this step.",
        "One term has the wrong sign. Which one changed when it should not have, or the other way round?",
        "A sign went astray here. {previous} becomes {expected}, not {current}.",
    ),
    "combining_like_terms": (
        "Check how you added up the matching terms.",
        "Add only the coefficients of terms with the same power. What do you get for each one?",
        "Only the coefficients of like terms are added. {previous} becomes {expected}, not {current}.",
    ),
    "exponent_rule": (
        "Check the rule for multiplying powers of the same base.",
        "When you multiply powers of the same base, what happens to the exponents?",
        "Multiplying powers of the same base adds the exponents. So {previous} becomes {expected}, not {current}.",
    ),
    "fraction_addition": (
        "Check how you combined these fractions.",
        "Do the fractions share a denominator yet? Look at that before adding.",
        "Fractions need a common denominator before they combine. {previous} becomes {expected}, not {current}.",
    ),
    "dividing_powers": (
        "Check the rule for dividing powers of the same base.",
        "When you divide powers of the same base, what happens to the exponents?",
        "Dividing powers of the same base subtracts the exponents. So {previous} becomes {expected}, not {current}.",
    ),
    "power_of_a_power": (
        "Check the rule for raising a power to another power.",
        "When a power is raised to a power, what happens to the exponents?",
        "Raising a power to a power multiplies the exponents. So {previous} becomes {expected}, not {current}.",
    ),
    "power_rule_exponent": (
        "Check the power in each term of your derivative.",
        "The power rule also changes the exponent. What should the new exponent be?",
        "The power rule brings the exponent down and lowers it by one. The derivative is {expected}, not {current}.",
    ),
    "power_rule_coefficient": (
        "Check the number in front of each term of your derivative.",
        "The power rule also changes the coefficient. What does the old exponent do?",
        "The old exponent multiplies the coefficient. The derivative is {expected}, not {current}.",
    ),
    "derivative_sign": (
        "Check the signs in your derivative.",
        "Differentiate one term at a time, keeping each term's sign. Which one changes?",
        "Each term keeps its sign when it's differentiated. The derivative is {expected}, not {current}.",
    ),
    "multiple_derivative_errors": (
        "More than one term of your derivative needs another look.",
        "Differentiate one term at a time, and compare each with what you wrote.",
        "The derivative is {expected}, not {current}.",
    ),
    "constant_not_dropped": (
        "Check what happens to the constant term.",
        "What is the derivative of a constant?",
        "A constant differentiates to 0. The derivative is {expected}, not {current}.",
    ),
    "missing_term": (
        "Check that you differentiated every term.",
        "Go through the original one term at a time. Is one of them missing from your answer?",
        "Each term is differentiated separately and they all stay. The derivative is {expected}, not {current}.",
    ),
    "chain_rule_inner_missing": (
        "Is there a function inside another function here?",
        "The outside function has a derivative, and so does what is inside it. Where is the second one?",
        "The chain rule also multiplies by the derivative of the inside. The derivative is {expected}, not {current}.",
    ),
    "product_rule_not_applied": (
        "Check what rule applies when two functions are multiplied.",
        "The derivative of a product is not the product of the derivatives. What is the rule?",
        "The product rule is u'v + uv'. The derivative is {expected}, not {current}.",
    ),
    "sign_when_moving_term": (
        "Check what happens to a term when it moves across the equals sign.",
        "To move a term, you do the opposite operation to both sides. What should that be here?",
        "Moving a term across the equals sign changes its sign. This step should keep the solution {expected}.",
    ),
    "solution_set_changed": (
        "Check that this step keeps the equation balanced.",
        "Whatever you do to one side, you have to do to the other. Did that happen here?",
        "This step changes the solutions of the equation. It should keep {expected}.",
    ),
    "inverse_of_multiplication": (
        "Check which operation undoes the one on the variable.",
        "The variable is being multiplied. What undoes that?",
        "Undo a multiplication by dividing both sides. The solution is {expected}.",
    ),
    "inverse_of_division": (
        "Check which operation undoes the one on the variable.",
        "The variable is being divided. What undoes that?",
        "Undo a division by multiplying both sides. The solution is {expected}.",
    ),
    "lost_variable": (
        "Look at the terms on both sides. What happened to the variable?",
        "Like terms combine by adding the numbers in front. What stays the same about each term?",
        "The numbers in front add and the variable stays. So {previous} is {expected}, not {current}.",
    ),
    "integral_without_variable": (
        "Look at your answer. Does it still have the variable in it?",
        "An indefinite integral gives a function, not a single number. Try differentiating your answer. Do you get back what's inside the integral?",
        "Differentiating the answer has to give back what's inside the integral. {previous} is {expected}, not {current}.",
    ),
    "integral_coefficient": (
        "Check the number in front of your answer.",
        "Differentiate your answer. What would the number in front have to be to get exactly what's inside the integral?",
        "Differentiating the answer has to give back what's inside the integral. {previous} is {expected}, not {current}.",
    ),
    "integral_exponent": (
        "Check the power in your answer.",
        "When you integrate a power of the variable, what happens to the exponent?",
        "Integrating raises the power by one, and divides by the new power. {previous} is {expected}, not {current}.",
    ),
    "derivative_instead_of_integral": (
        "An integral undoes differentiation. Which way should the power go?",
        "Differentiate your answer. Do you get back what's inside the integral?",
        "{current} is what differentiating gives, and an integral goes the other way. {previous} is {expected}.",
    ),
    "incorrect_antiderivative": (
        "Try differentiating your answer. Does it give you what's inside the integral?",
        "Differentiate your answer one term at a time, and compare it with what's inside the integral. Where do they differ?",
        "Differentiating the answer has to give back what's inside the integral. {previous} is {expected}, not {current}.",
    ),
    "incorrect_definite_integral": (
        "Check how the limits were used.",
        "Find the antiderivative first, then take its value at the top limit minus its value at the bottom limit. Do you get {current}?",
        "Taking the antiderivative at the top limit minus the bottom limit, {previous} comes to {expected}, not {current}.",
    ),
    "constant_added_to_derivative": (
        "Check the derivative itself first. Where would a constant have come from?",
        "A derivative has one definite value, and the + C belongs to integrals. What does differentiating each term give?",
        "A derivative has no arbitrary constant. The derivative is {expected}, not {current}.",
    ),
    "quotient_rule_not_applied": (
        "Check what rule applies when one function is divided by another.",
        "The derivative of a quotient isn't the quotient of the derivatives. What is the rule?",
        "The quotient rule is (u'v - uv')/v^2. The derivative is {expected}, not {current}.",
    ),
    "derivative_of_sine": (
        "Which derivative rule applies to {inner}?",
        "Think about how the slope of the sine curve relates to cosine.",
        "The slope of the sine curve at any point is the value of cosine there. The derivative of {inner} is {expected}, not {current}.",
    ),
    "derivative_of_cosine": (
        "Which derivative rule applies to {inner}?",
        "Is the cosine curve rising or falling just after x = 0? What does that say about the sign?",
        "Cosine falls just after x = 0, where sine is positive, which is where the minus sign comes from. The derivative of {inner} is {expected}, not {current}.",
    ),
    "derivative_of_tangent": (
        "Which derivative rule applies to {inner}?",
        "Try writing it as sine over cosine, and using the quotient rule.",
        "The derivative of tan(x) is sec^2(x), which is the same as tan^2(x) + 1. The derivative of {inner} is {expected}, not {current}.",
    ),
    "derivative_of_exponential": (
        "Which derivative rule applies to {inner}?",
        "What's special about the slope of e^x, compared with its value?",
        "e^x is its own derivative: at every point its slope equals its value. The derivative of {inner} is {expected}, not {current}.",
    ),
    "derivative_of_logarithm": (
        "Which derivative rule applies to {inner}?",
        "How quickly does ln(x) grow as x gets larger? What simple function shrinks in the same way?",
        "The derivative of ln(x) is 1/x. The derivative of {inner} is {expected}, not {current}.",
    ),
    "incorrect_factorisation": (
        "Check your factors again.",
        "Try multiplying your factors back out. Do you get the line above?",
        "Multiplying the factors back out should give {previous}. The factorised form is {expected}, not {current}.",
    ),
    "incorrect_derivative": (
        "Check your derivative again.",
        "Differentiate one term at a time, and compare each with what you wrote.",
        "The derivative is {expected}, not {current}.",
    ),
    "incorrect_simplification": (
        "Check this step again.",
        "Try putting the same number in for the variable in both lines. Do they give the same value?",
        "These two lines aren't equal. {previous} simplifies to {expected}, not {current}.",
    ),
}

# "One term at a time" means nothing to someone looking at one term. These replace a template's hints then.
_FOR_ONE_TERM: dict[str, tuple[str | None, str | None]] = {
    "incorrect_derivative": ("Which derivative rule applies to {inner}?", "Differentiate {inner} on its own, and compare the result with your {current}."),
    "derivative_sign": (None, "Differentiate {inner} on its own. Does its sign match your {current}?"),
    "constant_added_to_derivative": (None, "A derivative has one definite value, and the + C belongs to integrals. What does differentiating {inner} give?"),
    "incorrect_antiderivative": (None, "Differentiate your answer, and compare it with what's inside the integral. Where do they differ?"),
}
_DERIVATIVE_OF = re.compile(r"^d/d[a-z]\((.*)\)$")
_INTEGRAL_OF = re.compile(r"^∫(?:_\S+\^\S+)?\s*(.*?)\s*d[a-z]$")


def _inside(previous: str) -> str:
    """What's being differentiated or integrated, as `previous` shows it."""

    match = _DERIVATIVE_OF.match(previous) or _INTEGRAL_OF.match(previous)
    return match[1] if match else previous


def _is_one_term(expression: str) -> bool:
    depth = 0
    for position, character in enumerate(expression):
        depth += (character == "(") - (character == ")")
        if depth == 0 and character in "+-" and position > 0 and expression[position - 1] not in "^*/(":
            return False
    return True


# Wording for a sum on numbers alone, built from the verified step. The second hint gives a way
# to check the work, using only numbers the student wrote.
_ARITHMETIC_NAMES = {"*": "multiplication", "/": "division", "+": "addition", "-": "subtraction"}
_TIMES = {2: "twice", 3: "three times", 4: "four times", 5: "five times"}
_SIMPLE_SUM = re.compile(r"\s*(\d{1,4})\s*([*/+-])\s*(\d{1,4})\s*")
# `6(-6) + 8`: one product, then a number added or taken away. What the slip was can't be known, but
# which part to work out first can be said without giving anything away.
_PRODUCT_THEN_SUM = re.compile(r"\s*(\d{1,4})\s*\(\s*(-?\d{1,4})\s*\)\s*([+-])\s*(\d{1,4})\s*")
_MIXED_SUM = re.compile(r"[\d\s.()]*\d[\d\s.()]*(?:[*/+-][\d\s.()]+){2,}")
_NUMBER = re.compile(r"\s*-?\d+(?:\.\d+)?(?:\s*/\s*\d+)?\s*")


def _arithmetic_hint(level: int, mistake: TrackedMistake) -> str | None:
    if mistake.error_type != "arithmetic_error" or not _NUMBER.fullmatch(mistake.current_step):
        return None
    # Each must also pass the spoiler checks, which can object to a wording that happens to
    # contain the answer. The last of these always passes.
    for text in _arithmetic_wordings(level, mistake.previous_step, mistake.current_step.strip()):
        if not find_problems(level, text, mistake):
            return text
    return None


def _arithmetic_wordings(level: int, previous: str, current: str) -> list[str]:
    sum_ = _SIMPLE_SUM.fullmatch(previous)
    fallback = ["Check that calculation again."] if level == 1 else ["Work it out again, one step at a time."]
    product = _PRODUCT_THEN_SUM.fullmatch(previous)
    if product is not None and level == 2:
        factor, inside, op, added = product[1], _minus(product[2]), product[3], product[4]
        then = f"add {added}" if op == "+" else f"take away {added}"
        sign = " A negative times a positive is negative." if inside.startswith("−") else ""
        return [f"Work out {factor} × ({inside}) first, then {then}.{sign} Do you still get {current}?", *fallback]
    if sum_ is None:
        # Powers, functions, or a value carried on from a line above have no order of operations to point to.
        if level == 1 or not _MIXED_SUM.fullmatch(previous):
            return fallback
        return [
            f"Work it out one operation at a time, multiplying and dividing before adding and subtracting. Do you still get {current}?",
            *fallback,
        ]
    a, op, b = int(sum_[1]), sum_[2], int(sum_[3])
    if level == 1:
        return [f"Check that {_ARITHMETIC_NAMES[op]} again.", *fallback]
    if op == "*":
        times, added = min(a, b), max(a, b)
        if times in (0, 1):
            # Saying what times 0 or 1 gives would be saying the answer.
            return [f"What happens when you multiply a number by {times}?", *fallback]
        if times in _TIMES:
            first = f"Try adding {added} {_TIMES[times]}. Does that give you {current}?"
        elif times < 10:
            first = f"Try 5 lots of {added}, then {times - 5} more. Does that give you {current}?"
        else:
            first = f"Try splitting {times} into tens and units, and multiplying each part by {added}. Do you still get {current}?"
        return [first, f"Try adding {added} again and again, counting as you go. Do you get {current}?", *fallback]
    if op == "/":
        return [f"Try multiplying your answer by {b}. Do you get {a}?", *fallback]
    if op == "+":
        return [f"Try taking {b} away from {current}. Do you get {a}?", *fallback]
    return [f"Try adding {b} to {current}. Do you get {a}?", *fallback]


_DEFAULT_TEMPLATE: Template = (
    "Have another look at this step.",
    "Compare this line carefully with the one above it. What changed?",
    "This step does not follow from the one before it. It should give {expected}, not {current}.",
)


_NO_SINGLE_SOLUTION = {
    "no real solutions": "This equation has no real solutions, and this step doesn't keep that.",
    "all real numbers": "This equation holds for all real numbers, and this step doesn't keep that.",
}


def _sides(text: str) -> tuple[Node, sp.Expr, sp.Expr] | None:
    """An equation's left side as written, and both sides' values, from math_parser's tree. Never from the text."""

    try:
        statement = parse_statement(text)
        if statement.kind != "equality" or len(statement.parts) != 2:
            return None
        return statement.parts[0], to_sympy(statement.parts[0]), to_sympy(statement.parts[1])
    except (MathParseError, ValueError, TypeError):
        return None


def _balance_help(level: int, mistake: TrackedMistake) -> str | None:
    """A step that added a number to one side and not the same to the other, named by its number.

    On the iPad on 2026-11-04, `5x − 7 = 23` then `5x = 29` got "Check that this step keeps the equation
    balanced", which says nothing about where. The left side gained exactly 7 and the right side didn't, so
    the help can ask about adding 7, then about 23 + 7, without saying what that is.
    """

    if level >= 3 or mistake.error_type != "solution_set_changed":
        return None
    before, after = _sides(mistake.previous_step), _sides(mistake.current_step)
    if before is None or after is None:
        return None
    written, before_left, before_right = before
    _, after_left, after_right = after
    left = sp.expand(after_left - before_left)
    right = sp.expand(after_right - before_right)
    # Only a whole number added or taken away on the left, which the right side didn't match.
    if not (left.is_Integer and left != 0) or sp.expand(right - left) == 0:
        return None
    # And only where the left was written as a sum with that number in it, which is what went. 2(x + 1) = 10 then
    # 2x + 1 = 10 changes the left by 1 too, but that's a slip in expanding, not a number moved.
    terms = [sign * to_sympy(arg) for arg, sign in zip(written.args, written.signs, strict=True)] if written.kind == "add" else []
    if not any(term.is_Integer and term == -left for term in terms):
        return None
    amount = abs(int(left))
    adding = bool(left > 0)
    if level == 1:
        text = f"What happens when you {'add' if adding else 'take away'} {amount} {'to' if adding else 'from'} both sides?"
    elif before_right.is_Integer:
        text = f"Check the right-hand side. What is {_minus(str(int(before_right)))} {'+' if adding else '−'} {amount}?"
    else:
        text = f"Whatever you {'add to' if adding else 'take away from'} one side, you do to the other. Check the right-hand side."
    return None if find_problems(level, text, mistake) else text


def has_prepared_hints(mistake: TrackedMistake) -> bool:
    """Is there a fixed hint written for exactly this kind of mistake, rather than the general one?"""

    return (
        mistake.error_type in _TEMPLATES
        or _arithmetic_hint(1, mistake) is not None
        or _substitution_help(1, mistake) is not None
        or _copying_help(1, mistake) is not None
    )


# The verifier's own wording of what it found, in `evidence`. See `_check_by_value` and `_states_a_given`.
_PUT_IN = re.compile(r"with (?P<letter>[a-z]) = (?P<value>\S+), (?P<term>.+) became (?P<written>.+), not (?P<right>.+)")
_WORKED_OUT = re.compile(r"with (?P<given>.+), (?P<expression>.+) is (?P<value>\S+), not (?P<written>\S+)")
_GIVEN_OTHERWISE = re.compile(r"the question gives (?P<letter>[a-z]) = (?P<value>\S+), not (?P<written>\S+)")


def _substitution_help(level: int, mistake: TrackedMistake) -> str | None:
    """Help for a slip in putting the question's values in, worded from what the verifier found.

    Only the explanation shows the line as it should read. The first hint only points, and the second
    names the term and the value, which the question has already given.
    """

    if mistake.category != "substitution":
        return None
    if mistake.error_type == "sign_lost_value_unchanged":
        # The value is still right, so nothing here may say it isn't. What's missing is the substitution.
        found = _PUT_IN.fullmatch(mistake.evidence)
        if found is None:
            return None
        letter, value = found["letter"], _minus(found["value"])
        term, written, right = found["term"], _minus(found["written"]), _minus(found["right"])
        if level == 1:
            return f"Remember, {letter} = {value}. Look at what you put in for {letter} in {term}."
        if level == 2:
            same = "Squaring gives the same value either way" if "^2" in term else "It gives the same value here"
            return f"Try putting {value} in the brackets: {right}. {same}, but the question gives {letter} = {value}, so that's what goes in."
        return (
            f"{written} and {right} have the same value, so your answer still works. But substituting means "
            f"putting in the value the question gives, {value}, in brackets, before working anything out. "
            f"So {mistake.previous_step} becomes {mistake.expected}."
        )
    if mistake.error_type in ("sign_lost_in_substitution", "wrong_value_substituted"):
        found = _PUT_IN.fullmatch(mistake.evidence)
        if found is None:
            return None
        letter, value, term = found["letter"], _minus(found["value"]), found["term"]
        if level == 1:
            return f"Look at the value the question gives for {letter}. Did you put that same value in every time {letter} appears?"
        if level == 2:
            if mistake.error_type == "sign_lost_in_substitution":
                needs = "" if term == letter else f", so {term} needs {value} in it"
                return f"{letter} = {value} here{needs}. Check the sign you put in for {term}."
            return f"{letter} = {value} here. What did you put in for {letter} in {term}?"
        return (
            f"Substituting means replacing every {letter} with the value given, {value}, in brackets. "
            f"So {mistake.previous_step} becomes {mistake.expected}, not {mistake.current_step}. "
            "Keep each sign as you work it out."
        )
    if mistake.error_type == "incorrect_evaluation":
        found = _WORKED_OUT.fullmatch(mistake.evidence)
        if found is None:
            return None
        given = _minus(found["given"])
        if level == 1:
            return f"Check this value again. Put in {given} one step at a time."
        if level == 2:
            return f"Write {mistake.previous_step} with the value in brackets first, then work it out. Do you still get {mistake.current_step}?"
        return f"With {given}, {mistake.previous_step} comes to {mistake.expected}, not {mistake.current_step}."
    if mistake.error_type == "given_value_changed":
        found = _GIVEN_OTHERWISE.fullmatch(mistake.evidence)
        if found is None:
            return None
        letter = found["letter"]
        if level == 1:
            return f"Look at the question again. What value does it give {letter}?"
        if level == 2:
            return f"Check this line against the question. Does it give {letter} the same value?"
        return f"The question gives {letter} = {_minus(found['value'])}, so that's the value to use, not {mistake.current_step}."
    return None


def _minus(text: str) -> str:
    return text.replace("-", "−")


# The verifier's wording for a letter or a copied term that changed. See `_check_identity`.
_RENAMED = re.compile(r"(?P<current>.+) has the letter (?P<now>[A-Za-z]) where (?P<previous>.+) has (?P<was>[A-Za-z])")
_BECAME = re.compile(r"(?P<term>[^,]+) became (?P<written>[^,]+)")
_LEFT_OUT = re.compile(r"left out: (?P<terms>.+)")


def _signed(term: str) -> str:
    return term if term.startswith("-") else f"+{term}"


def _copying_help(level: int, mistake: TrackedMistake) -> str | None:
    """Help for a letter, a sign or a term that changed on the way down, worded from what the verifier found.

    These say which term, because that's what the verifier established, and never the simplified answer
    before the explanation. Coefficient arithmetic gets the general like-terms hints instead.
    """

    evidence = mistake.evidence or ""
    if mistake.error_type == "variable_changed":
        found = _RENAMED.fullmatch(evidence)
        if found is None:
            return None
        was, now = found["was"], found["now"]
        previous, current = mistake.previous_step or "", mistake.current_step or ""
        # Not only like terms: `2(x + 3) = 2y + 6` is the same slip, so nothing here says "combining".
        # And `8a + 3b - 4a - 7b` isn't "in b", so with more than one letter the hint says which is missing.
        numbers = "numbers" if len(re.findall(r"\d+", current)) > 1 else "number"
        # Every letter but a function's name, so `8ab - 5ab` has two, and `sin(x)` one.
        letters = set(re.findall(r"[A-Za-z]", re.sub(r"(sqrt|sin|cos|tan|log|ln|exp)\(", "(", previous)))
        if len(letters) > 1:
            if level == 1:
                return f"You worked out the {numbers} correctly. Now look at the letters: which letters are in {previous}?"
            if level == 2:
                return f"There's no {now} in {previous}. The terms in {was} keep {was}."
            return (
                "Working it out changes the numbers, never the letters. "
                f"{previous} has no {now}, so it works out as {mistake.expected}, not {current}."
            )
        if level == 1:
            return f"You worked out the {numbers} correctly. Now look at the letter: which letter do the terms in {previous} have?"
        if level == 2:
            return f"Those terms are in {was}, so your answer keeps {was}. It doesn't change to {now}."
        return (
            "Working it out changes the numbers, never the letters. "
            f"{previous} is in {was}, so it works out as {mistake.expected}, not {current}."
        )
    if mistake.error_type == "term_letter_changed":
        found = _BECAME.fullmatch(evidence)
        if found is None:
            return None
        term, written = _minus(found["term"]), _minus(found["written"])
        if level == 1:
            return "Compare each term you wrote with the line above. Did every term keep its letter?"
        if level == 2:
            return f"The line above has {term}, and your line has {written}. Which letter should that term have?"
        return (
            "Moving terms around doesn't change them: each one keeps its own number and letter. "
            f"So {term} stays {term}, and this line should read {mistake.expected}, not {mistake.current_step}."
        )
    if mistake.error_type == "sign_error":
        found = _BECAME.fullmatch(evidence)
        if found is None:
            # A sign error found by value rather than term by term has the general hints.
            return None
        term, written = _minus(_signed(found["term"])), _minus(_signed(found["written"]))
        if level == 1:
            return "Compare each term you wrote with the line above. Did every term keep its sign?"
        if level == 2:
            return f"The line above has {term}, and your line has {written}. Check the sign in front of it."
        return (
            "When a term moves, the sign in front of it moves with it. "
            f"So {term} stays {term}, and this line should read {mistake.expected}, not {mistake.current_step}."
        )
    if mistake.error_type == "terms_left_out":
        found = _LEFT_OUT.fullmatch(evidence)
        if found is None:
            return None
        terms = [_minus(term) for term in found["terms"].split(", ")]
        if level == 1:
            return "Compare your line with the one above. Is every term still there?"
        if level == 2:
            return f"Count the terms in the line above, then in yours. Where has {terms[0]} gone?"
        missing = terms[0] if len(terms) == 1 else ", ".join(terms[:-1]) + " and " + terms[-1]
        return (
            "Every term carries on to the next line until it's combined with another. "
            f"Here {missing} went missing, so this line isn't equal to {mistake.previous_step}, which simplifies to {mistake.expected}."
        )
    return None


_TERM_HINTS: dict[str, tuple[str, str]] = {
    "coefficient": (
        "Look again at the derivative of {term}.",
        "Use the power rule on {term} by itself. Does the number in front match your {written}?",
    ),
    "exponent": (
        "Look again at the derivative of {term}.",
        "Use the power rule on {term} by itself. Does the power match your {written}?",
    ),
    "sign": (
        "Check the sign of the derivative of {term}.",
        "Differentiate {term} by itself. Does its sign match your {written}?",
    ),
    "missing": (
        "Go through the original one term at a time. Where is the derivative of {term}?",
        "Differentiate {term} by itself, then look for it in your answer.",
    ),
}


def _term_hint(level: int, mistake: TrackedMistake) -> str | None:
    """A first or second hint that points at the term that differs, and doesn't give it away."""

    wrong = [fact for fact in mistake.term_facts if fact.problem != "right"]
    if len(wrong) != 1 or wrong[0].problem not in _TERM_HINTS:
        return None
    fact = wrong[0]
    text = _TERM_HINTS[fact.problem][level - 1].format(term=fact.term, written=fact.written)
    return None if find_problems(level, text, mistake) else text


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def derivative_explanation(mistake: TrackedMistake) -> str | None:
    """Exactly what the comparison established, term by term, and nothing about how it happened.

    For `d/dx(x^2 + 2x) = 3x + 2`: "The derivative of x^2 is 2x, not 3x. The derivative of 2x is 2,
    so the correct result is 2x + 2."
    """

    if not mistake.term_facts or mistake.expected in (None, ""):
        return None
    sentences: list[str] = []
    right: list[str] = []
    for fact in mistake.term_facts:
        if fact.problem == "right":
            right.append(f"The derivative of {fact.term} is {fact.derivative}")
        elif fact.problem == "constant":
            sentences.append(f"{fact.term} is a constant, so its derivative is 0.")
        elif fact.problem == "missing":
            sentences.append(f"The derivative of {fact.term} is {fact.derivative}, and that term isn't in your answer.")
        else:
            sentences.append(f"The derivative of {fact.term} is {fact.derivative}, not {fact.written}.")
    for extra in mistake.extra_terms:
        sentences.append(f"None of the terms give the {extra} in your answer.")
    ending = f"so the correct result is {mistake.expected}."
    if right:
        worded = _join(right[:1] + [phrase.replace("The derivative", "the derivative") for phrase in right[1:]])
        sentences.append(f"{worded}, {ending}")
    else:
        sentences.append(ending[0].upper() + ending[1:])
    return " ".join(sentences)


def prepared_explanation(mistake: TrackedMistake) -> str | None:
    """An explanation that needs no model, because the verifier has established all of it."""

    substituted = _substitution_help(3, mistake)
    if substituted is not None:
        return substituted
    copied = _copying_help(3, mistake)
    if copied is not None:
        return copied
    # A named rule for one standard function, which is all there is to say about it.
    if mistake.error_type.startswith("derivative_of_") and mistake.error_type in _TEMPLATES:
        return _TEMPLATES[mistake.error_type][2].format(
            inner=_inside(mistake.previous_step), current=mistake.current_step, expected=mistake.expected
        )
    return derivative_explanation(mistake)


def template_text(level: int, mistake: TrackedMistake) -> str:
    substituted = _substitution_help(level, mistake)
    if substituted is not None:
        return substituted
    copied = _copying_help(level, mistake)
    if copied is not None:
        return copied
    if level < 3:
        arithmetic = _arithmetic_hint(level, mistake)
        if arithmetic is not None:
            return arithmetic
        balance = _balance_help(level, mistake)
        if balance is not None:
            return balance
        term = _term_hint(level, mistake)
        if term is not None:
            return term
    else:
        explained = prepared_explanation(mistake)
        if explained is not None:
            return explained
    template = _TEMPLATES.get(mistake.error_type, _DEFAULT_TEMPLATE)
    inner = _inside(mistake.previous_step)
    if level < 3 and mistake.error_type in _FOR_ONE_TERM and _is_one_term(inner):
        worded = _FOR_ONE_TERM[mistake.error_type][level - 1]
        if worded is not None:
            return worded.format(inner=inner, current=mistake.current_step)
    if level == 3 and mistake.expected in _NO_SINGLE_SOLUTION:
        return _NO_SINGLE_SOLUTION[mistake.expected]
    # When the right result is just the line before, "x - 3 becomes x - 3" would be nonsense.
    if level == 3 and _squash(mistake.expected) == _squash(mistake.previous_step):
        return f"These two lines aren't equal: {mistake.previous_step} and {mistake.current_step} have different values."
    return template[level - 1].format(
        previous=mistake.previous_step, current=mistake.current_step, expected=mistake.expected, inner=inner
    )


def _user_message(level: int, mistake: TrackedMistake, question: str | None, corrections: list[str]) -> str:
    facts: dict[str, object] = {
        "requested_level": level,
        "student_wrote": mistake.current_step,
        "previous_step": mistake.previous_step,
        "operation": mistake.parsed_operation or mistake.operation_type,
        "error_category": mistake.category,
        "error_type": mistake.error_type,
        "earlier_hints": mistake.hints_given[-MAX_EARLIER_HINTS:],
    }
    # A hint can't give away what the model was never told. Only an explanation gets the answer.
    if level == 3:
        facts["correct_result"] = mistake.expected
        facts["checker_evidence"] = mistake.evidence
        if mistake.term_facts:
            facts["established_by_term"] = [
                {"term": fact.term, "derivative": fact.derivative, "student_wrote": fact.written, "result": fact.problem}
                for fact in mistake.term_facts
            ]
    if question:
        facts["student_question"] = question[:MAX_QUESTION_CHARS]
    message = json.dumps(facts, ensure_ascii=False)
    if corrections:
        message += "\n\nYour previous attempt was rejected because " + "; ".join(corrections) + ". Try again."
    return message


async def generate_help(
    router: ModelRouter,
    level: Literal[1, 2, 3],
    mistake: TrackedMistake,
    *,
    question: str | None = None,
    session_id: str = "",
    run_id: str = "runtime",
    attempts: list[tuple[str, list[str]]] | None = None,
) -> tuple[str, Literal["model", "template"], ModelError | None]:
    """Return (text, source, error). Never raises for a model failure.

    `attempts`, if given, collects each raw model answer with its problems, for evaluation.
    """

    # With no question to answer, an explanation the verifier can state in full needs no wording.
    if level == 3 and question is None and prepared_explanation(mistake) is not None:
        return template_text(3, mistake), "template", None
    purpose = Purpose.EXPLAIN if level == 3 else Purpose.HINT
    system = prompts.load_prompt(prompts.EXPLAIN if level == 3 else prompts.HINT)
    corrections: list[str] = []
    for _ in range(2):
        messages = [
            {"role": "developer", "content": system},
            {"role": "user", "content": _user_message(level, mistake, question, corrections)},
        ]
        try:
            result = await router.complete_json(
                purpose, messages, GeneratedHelp, session_id=session_id, run_id=run_id
            )
        except ModelError as exc:
            return template_text(level, mistake), "template", exc
        corrections = find_problems(level, result.value.text, mistake)
        if attempts is not None:
            attempts.append((result.value.text, corrections))
        if not corrections:
            return result.value.text.strip(), "model", None
    return template_text(level, mistake), "template", None


HelpResult = tuple[str, Literal["model", "template"], ModelError | None]


class PreparedHelp:
    """Hints worded by the model before they're asked for, for mistakes with no fixed hint.

    Started when the alert goes up, so the wait for a hint is what's left of the model call, if
    anything. These are generated with no question to answer, and a second hint knows the first.
    """

    def __init__(self, router: ModelRouter) -> None:
        self._router = router
        self._tasks: OrderedDict[tuple[str, str, int], asyncio.Task[HelpResult]] = OrderedDict()

    def prepare(self, session_id: str, mistake: TrackedMistake) -> None:
        if has_prepared_hints(mistake):
            return
        first = self._start(session_id, mistake, 1, None)
        self._start(session_id, mistake, 2, first)

    def _start(
        self, session_id: str, mistake: TrackedMistake, level: Literal[1, 2], after: asyncio.Task[HelpResult] | None
    ) -> asyncio.Task[HelpResult]:
        key = (session_id, mistake.id, level)
        task = self._tasks.get(key)
        if task is None:
            task = asyncio.ensure_future(self._generate(session_id, mistake, level, after))
            task.add_done_callback(_log_failure)
            self._tasks[key] = task
            while len(self._tasks) > MAX_PREPARED:
                self._tasks.popitem(last=False)
        return task

    async def _generate(
        self, session_id: str, mistake: TrackedMistake, level: Literal[1, 2], after: asyncio.Task[HelpResult] | None
    ) -> HelpResult:
        earlier = list(mistake.hints_given)
        if after is not None:
            first = (await after)[0]
            if first not in earlier:
                earlier.append(first)
        return await generate_help(self._router, level, replace(mistake, hints_given=earlier), session_id=session_id)

    async def get(self, session_id: str, mistake: TrackedMistake, level: Literal[1, 2], wait_s: float = HINT_WAIT_S) -> HelpResult:
        """A hint now: the fixed one, or the model's if it arrives in time, or else the general one."""

        if has_prepared_hints(mistake):
            return template_text(level, mistake), "template", None
        first = self._start(session_id, mistake, 1, None)
        task = first if level == 1 else self._start(session_id, mistake, 2, first)
        try:
            return await asyncio.wait_for(asyncio.shield(task), wait_s)
        except TimeoutError:
            # Whatever is given here is what this level repeats, so the two can't disagree.
            return template_text(level, mistake), "template", None


def _log_failure(task: asyncio.Task[HelpResult]) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.warning("hint_preparation_failed", extra={"error_type": type(task.exception()).__name__})
