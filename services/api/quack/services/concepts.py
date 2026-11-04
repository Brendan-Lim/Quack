"""What idea makes a step work, for a student who asks to have it explained.

A check says whether a step holds. This says why that kind of step works, and it's what "Explain this
step" is for. It's all fixed wording chosen from the verifier's own record of the step, or from the
parsed maths that was circled, with no model call and no claim about the student. Nothing here
decides whether anything is right: `reveal` is only true for a step the verifier has found right, and
then it can use the student's own answer as the example. Otherwise it gives the rule, with no result.

The rule is chosen from the structure of what was circled: an integral is explained as an integral.
When that structure can't be made out, there's no explanation, rather than one about something else.
On 2026-11-02 an integral read as two lines got "like terms", because any letter used to mean that.

An explanation is a title and a few short numbered steps, each one idea, for a small panel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sympy as sp

from quack.services.math_parser import MathParseError, Node, parse_expression, parse_statement, to_sympy
from quack.services.math_verifier import StepResult

_DERIVATIVE = re.compile(r"^d/d(?P<variable>[a-z])\((?P<inner>.*)\)$")
_INDEFINITE = re.compile(r"^∫\s*(?P<inner>.*?)\s*d(?P<variable>[a-z])$")
_DEFINITE = re.compile(r"^∫_")
_SIMPLE_SUM = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*([*/+-])\s*(-?\d+(?:\.\d+)?)\s*$")

_CHANGE = "A derivative tells you how fast a function is changing."
_UNDOES = "Integrating undoes differentiating."
_ADD_C = "Add + C: any constant disappears when you differentiate."
_CHECK_BACK = "Quick check: differentiating your answer should give back what's inside the integral."


@dataclass(frozen=True)
class Explanation:
    """What Explain shows. `concept` names the rule, so tests and traces can say which was chosen."""

    concept: str
    title: str
    steps: tuple[str, ...]
    takeaway: str | None = None

    @property
    def text(self) -> str:
        """The same, as one paragraph, for anything that can't show a list."""

        return " ".join((*self.steps, *((self.takeaway,) if self.takeaway else ())))


_STANDARD_DERIVATIVES: dict[type, Explanation] = {
    sp.sin: Explanation(
        "derivative_of_sine",
        "The sine rule",
        (
            _CHANGE,
            "The slope of the sine curve at any point is the value of cos(x) there.",
            "So the derivative of sin(x) is cos(x).",
        ),
    ),
    sp.cos: Explanation(
        "derivative_of_cosine",
        "The cosine rule",
        (
            _CHANGE,
            "Cosine's rate of change is -sin(x).",
            "The minus sign is there because cos(x) is falling wherever sin(x) is positive.",
        ),
    ),
    sp.tan: Explanation(
        "derivative_of_tangent",
        "The tangent rule",
        ("The derivative of tan(x) is sec^2(x).", "That's the same as tan^2(x) + 1."),
    ),
    sp.exp: Explanation(
        "derivative_of_exponential",
        "The exponential rule",
        ("e^x is its own derivative.", "At every point, its slope equals its value."),
    ),
    sp.log: Explanation(
        "derivative_of_log",
        "The log rule",
        ("The derivative of ln(x) is 1/x.", "The larger x gets, the more slowly ln(x) grows."),
    ),
}
_STANDARD_INTEGRALS: dict[type, tuple[str, str, str]] = {
    sp.sin: ("integral_of_sine", "Integrating sine", "Differentiating -cos(x) gives sin(x), so ∫ sin(x) dx is -cos(x) + C."),
    sp.cos: (
        "integral_of_cosine",
        "Integrating cosine",
        "Differentiating sin(x) gives cos(x), so ∫ cos(x) dx is sin(x) + C.",
    ),
    sp.exp: ("integral_of_exponential", "Integrating e^x", "e^x is its own derivative, so ∫ e^x dx is e^x + C."),
}


def _parsed(text: str, working: tuple[str, ...]) -> sp.Expr | None:
    """What's being differentiated or integrated. As the student's line has it if it can be, since
    the tidied form in a step's record isn't always something the parser takes back: `xsin(x)`."""

    for line in reversed(working):
        try:
            parts = parse_statement(line).parts
            if parts and parts[0].kind in ("deriv", "integral"):
                return to_sympy(parts[0].args[0])
        except (MathParseError, ValueError, TypeError):
            continue
    try:
        return to_sympy(parse_expression(text))
    except (MathParseError, ValueError, TypeError):
        return None


def _variable_in(expr: sp.Expr, name: str) -> sp.Symbol:
    """The parser's own symbol of that name, which may carry assumptions a new one wouldn't."""

    return next((symbol for symbol in expr.free_symbols if symbol.name == name), sp.Symbol(name))


def _is_power_of(expr: sp.Expr, variable: sp.Symbol) -> bool:
    coefficient, rest = expr.as_independent(variable, as_Add=False)
    base, exponent = rest.as_base_exp()
    return not coefficient.has(variable) and base == variable and not exponent.has(variable) and exponent != -1


def _function_of_variable(expr: sp.Expr, variable: sp.Symbol) -> tuple[type, sp.Expr] | None:
    """`3 sin(2x)` is (sin, 2x). Only one known function of the variable, with any constant in front."""

    _, rest = expr.as_independent(variable, as_Add=False)
    return (rest.func, rest.args[0]) if isinstance(rest, sp.Function) and len(rest.args) == 1 else None


def _becomes(inner: str, answer: str | None) -> str | None:
    return f"So {inner} becomes {answer}." if answer else None


def _derivative(inner: str, variable_name: str, answer: str | None, working: tuple[str, ...]) -> Explanation | None:
    expr = _parsed(inner, working)
    if expr is None:
        return None
    variable = _variable_in(expr, variable_name)
    example = _becomes(inner, answer)
    if isinstance(expr, sp.Add):
        steps = (
            "A sum is differentiated one term at a time.",
            "Each term follows its own rule.",
            "A constant term differentiates to 0.",
        )
        return Explanation("derivative_of_sum", "Differentiating a sum", steps, example)
    numerator, denominator = sp.fraction(expr)
    if denominator.has(variable) and numerator.has(variable):
        steps = (
            "This is one function divided by another.",
            "Use the quotient rule: (u'v - uv')/v^2.",
            "u is the top and v is the bottom.",
        )
        return Explanation("quotient_rule", "The quotient rule", steps, example)
    _, rest = expr.as_independent(variable, as_Add=False)
    if isinstance(rest, sp.Mul) and sum(factor.has(variable) for factor in rest.args) >= 2:
        steps = (
            "This is two functions multiplied together.",
            "Use the product rule: u'v + uv'.",
            "Differentiate each in turn, keeping the other as it is.",
        )
        return Explanation("product_rule", "The product rule", steps, example)
    function = _function_of_variable(expr, variable)
    if function is not None:
        kind, argument = function
        if argument != variable:
            steps = (
                "This is a function inside another function, so the chain rule applies.",
                "Differentiate the outside, leaving the inside alone.",
                "Then multiply by the derivative of the inside.",
            )
            return Explanation("chain_rule", "The chain rule", steps, example)
        if kind in _STANDARD_DERIVATIVES:
            return _STANDARD_DERIVATIVES[kind]
    if rest.is_Pow and not rest.base.is_Symbol and rest.base.has(variable) and not rest.exp.has(variable):
        steps = (
            "This is a power of a bracket, so the chain rule applies.",
            "Use the power rule on the outside, leaving the bracket alone.",
            "Then multiply by the derivative of what's inside.",
        )
        return Explanation("chain_rule", "The chain rule", steps, example)
    if _is_power_of(expr, variable) or expr == variable:
        steps = (
            "The power rule brings the exponent down in front.",
            "Then the exponent goes down by one.",
            f"That tells you how fast {inner} changes at each value of {variable_name}.",
        )
        return Explanation("power_rule", "The power rule", steps, example)
    return Explanation(
        "derivative", "What a derivative is", ("A derivative says how fast something changes at each point.",), example
    )


def _caret(expr: sp.Expr) -> str:
    return str(expr).replace("**", "^").replace("*", "")


def _integral_power(expr: sp.Expr, variable: sp.Symbol, inner: str, answer: str | None) -> Explanation:
    coefficient, rest = expr.as_independent(variable, as_Add=False)
    _, exponent = rest.as_base_exp()
    raised = exponent + 1
    if answer and coefficient == 1 and exponent.is_Integer and exponent > 0:
        power = _caret(variable**raised)
        steps = (
            f"Raise the power by one: {inner} is {inner}^1, so the new power is 2."
            if exponent == 1
            else f"Raise the power by one: {_caret(variable**exponent)} becomes {power}.",
            f"Divide by the new power: {power} becomes {power}/{raised}.",
            _ADD_C,
        )
        return Explanation(
            "integral_power_rule",
            "The power rule for integrals",
            steps,
            f"Quick check: the derivative of {answer} + C is {inner}.",
        )
    steps = ("Raise the power by one.", "Divide by the new power. Any number in front stays in front.", _ADD_C)
    return Explanation(
        "integral_power_rule",
        "The power rule for integrals",
        steps,
        f"Quick check: the derivative of {answer} + C is {inner}." if answer else _CHECK_BACK,
    )


def _integral(inner: str, variable_name: str, answer: str | None, working: tuple[str, ...]) -> Explanation:
    expr = _parsed(inner, working)
    if expr is None:
        return Explanation(
            "integration",
            "Integrating",
            (_UNDOES, "Look for something whose derivative is what's inside.", _ADD_C),
            _CHECK_BACK,
        )
    variable = _variable_in(expr, variable_name)
    if not expr.has(variable):
        steps = (_UNDOES, f"{inner}{variable_name} differentiates to the constant {inner}.", _ADD_C)
        return Explanation("integral_of_constant", "Integrating a constant", steps, _becomes(inner, answer))
    if isinstance(expr, sp.Add):
        steps = (
            _UNDOES,
            "A sum is integrated one term at a time, each with its own rule.",
            "Add + C once, for the whole answer.",
        )
        return Explanation("integral_of_sum", "Integrating a sum", steps, _becomes(inner, answer))
    function = _function_of_variable(expr, variable)
    if function is not None and function[1] == variable and function[0] in _STANDARD_INTEGRALS:
        concept, title, rule = _STANDARD_INTEGRALS[function[0]]
        return Explanation(concept, title, (_UNDOES, rule, _ADD_C))
    if _is_power_of(expr, variable):
        return _integral_power(expr, variable, inner, answer)
    steps = (_UNDOES, f"Look for something whose derivative is {inner}.", _ADD_C)
    return Explanation("integration", "Integrating", steps, _becomes(inner, answer))


def _without_constant(answer: str) -> str:
    return re.sub(r"\s*\+\s*[A-Z]\s*$", "", answer)


def _sum(previous: str, answer: str | None) -> Explanation | None:
    match = _SIMPLE_SUM.fullmatch(previous)
    if match is None:
        return None
    a, operator, b = match.groups()
    ending = f", which is {answer}" if answer else ""
    if operator == "*":
        return Explanation("multiplication", "Multiplying", (f"{a} × {b} means {a} lots of {b}{ending}.",))
    if operator == "/":
        result = f": it is {answer}" if answer else ""
        return Explanation("division", "Dividing", (f"{a} ÷ {b} asks what number multiplied by {b} gives {a}{result}.",))
    if operator == "+":
        return Explanation("addition", "Adding", (f"{a} + {b} means starting at {a} and counting on {b}{ending}.",))
    return Explanation("subtraction", "Subtracting", (f"{a} - {b} means starting at {a} and counting back {b}{ending}.",))


_ORDER = Explanation(
    "order_of_operations",
    "One operation at a time",
    ("Brackets and powers first.", "Then multiplying and dividing.", "Then adding and subtracting."),
)
_BALANCE = Explanation(
    "equation_transformation",
    "Keeping it balanced",
    (
        "Do the same thing to both sides.",
        "That keeps the equation balanced.",
        "So this line has the same solutions as the one before it.",
    ),
)
_ROUNDING = Explanation(
    "approximation",
    "Rounding",
    ("Look at the first digit you're dropping.", "Five or more rounds up.", "Anything less leaves it as it is."),
)
_EXPANDING = Explanation(
    "expanding_brackets",
    "Multiplying out a bracket",
    (
        "Multiply every term inside the bracket by what's outside.",
        "Don't stop before the last term.",
        "The result is the same value written another way.",
    ),
)
_LIKE_TERMS = Explanation(
    "like_terms",
    "Combining like terms",
    (
        "Like terms have the same letters and powers.",
        "Add the numbers in front of them.",
        "The letters and powers stay as they are.",
    ),
)
_DEFINITE_INTEGRAL = Explanation(
    "definite_integral",
    "Definite integrals",
    ("Find an antiderivative.", "Work it out at the top limit.", "Subtract its value at the bottom limit."),
)


def _terms(node: Node) -> list[Node]:
    if node.kind == "add":
        return [term for arg in node.args for term in _terms(arg)]
    return [node]


def _letters_and_powers(term: Node) -> sp.Expr | None:
    try:
        _, rest = to_sympy(term).as_coeff_Mul()
    except (MathParseError, ValueError, TypeError):
        return None
    return rest if rest.free_symbols else None


def _shape(previous: str) -> Explanation | None:
    """What a line with no derivative or integral in it is doing, from its parsed shape alone."""

    try:
        node = parse_expression(previous)
    except (MathParseError, ValueError, TypeError):
        return None
    if node.kind == "mul" and any(arg.kind == "add" for arg in node.args):
        return _EXPANDING
    if node.kind == "add":
        letters = [_letters_and_powers(term) for term in _terms(node)]
        found = [part for part in letters if part is not None]
        if len(found) != len(set(found)):
            return _LIKE_TERMS
    return None


def _claims(working: tuple[str, ...]) -> tuple[str, ...]:
    """The circled lines, with a line ending in `=` joined to the one under it, as that's its answer.

    A tall answer like x²/2 + C can be grouped as a line of its own, under `∫ x dx =`.
    """

    joined: list[str] = []
    for text in working:
        if joined and joined[-1].rstrip().endswith("="):
            joined[-1] = f"{joined[-1].rstrip()} {text.strip()}"
        else:
            joined.append(text)
    return tuple(joined)


def explain(step: StepResult | None, working: tuple[str, ...] = (), *, reveal: bool = False) -> Explanation | None:
    """The rule behind this step in a few short steps, or None if what it is can't be made out."""

    claims = _claims(working)
    previous = step.previous_step if step and step.previous_step else None
    answer = step.current_step if reveal and step else None
    if previous is None and claims:
        previous = claims[-1].split("=", 1)[0].strip()
    if not previous:
        return None
    if _DEFINITE.match(previous):
        return _DEFINITE_INTEGRAL
    derivative = _DERIVATIVE.match(previous)
    if derivative:
        return _derivative(derivative["inner"], derivative["variable"], answer, claims)
    integral = _INDEFINITE.match(previous)
    if integral:
        return _integral(integral["inner"], integral["variable"], _without_constant(answer) if answer else None, claims)
    operation = step.operation_type if step else ""
    if operation == "arithmetic" or _SIMPLE_SUM.fullmatch(previous):
        return _sum(previous, answer) or _ORDER
    if operation == "equation_transformation":
        return _BALANCE
    if operation == "approximation":
        return _ROUNDING
    return _shape(previous)
