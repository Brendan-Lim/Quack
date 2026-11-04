"""A constrained parser for student mathematics.

Model output is untrusted, so it never reaches `sympify` or `eval`. Text goes
through a hand-written tokenizer and recursive-descent parser into a small AST,
and only that AST is converted to SymPy.

Anything we cannot read with certainty raises rather than guesses:

- `Unsupported`: recognisable notation outside Quack's domain (limits, matrices).
- `Ambiguous`: more than one reasonable meaning (`1/2x`, `sinx`, `x2`).
- `Malformed`: not mathematics we can parse at all.
- `Incomplete`: visibly unfinished working (`2x +`, `x =`).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from typing import Literal

import sympy as sp

from quack.services.integration import CannotIntegrate, definite_value

MAX_TEXT_LENGTH = 200
MAX_TOKENS = 120
MAX_DEPTH = 24
MAX_NODES = 120
MAX_NUMBER_DIGITS = 12
MAX_EXPONENT = 12
# A power of a single term costs nothing, and x^4 * x^5 = x^20 is a mistake worth catching. MAX_EXPONENT
# is for sums, where each step up multiplies the work.
MAX_PLAIN_EXPONENT = 60
MAX_POWER_NESTING = 2
MAX_EXPANDED_TERMS = 3000
MAX_EXP_ARGUMENT = 100
# In an exponent with a variable in it, like 2^(3x), where sample values make the result enormous.
MAX_NUMBER_IN_VARIABLE_EXPONENT = 100
DIGITS = "0123456789"

FUNCTIONS = {
    "sin": sp.sin,
    "cos": sp.cos,
    "tan": sp.tan,
    "ln": sp.log,
    "exp": sp.exp,
    "sqrt": sp.sqrt,
    "abs": sp.Abs,
}
CONSTANTS = {"pi": sp.pi, "e": sp.E}
# Short words that would otherwise read as a product of letters.
PROSE_WORDS = {"or", "and", "so", "if", "let", "as", "is", "the", "to", "of", "in", "on", "at", "by", "we", "it"}
UNSUPPORTED_WORDS = {
    "int", "integral", "lim", "limit", "sum", "prod", "log", "arcsin", "arccos",
    "arctan", "sec", "csc", "cot", "sinh", "cosh", "tanh", "det", "mod", "max", "min",
}
# Read as letters these would multiply: 3cm * 4cm would come to 12c^2m^2.
UNIT_WORDS = {"cm", "mm", "km", "kg", "mg", "ml", "ft", "hr"}
# Single letters that, followed by "(", mean function notation rather than a product.
FUNCTION_LETTERS = {"f", "g", "h"}

UNSUPPORTED_CHARS = "∑∏∞≈≃≠<>≤≥!→⇒∂[]{}\\"

_NORMALISE = [
    ("×", "*"), ("⋅", "*"), ("·", "*"), ("÷", "/"), ("−", "-"), ("–", "-"), ("—", "-"),
    ("π", "pi"), ("√", "sqrt"), ("**", "^"),
    ("’", "'"), ("′", "'"), ("±", "+-"),
    # Written as one symbol, these have only one meaning, so the bracket says so.
    ("½", "(1/2)"), ("⅓", "(1/3)"), ("⅔", "(2/3)"), ("¼", "(1/4)"), ("¾", "(3/4)"),
    ("\\int", "∫"),
    # A degree sign says the angle is in degrees, which is how a number inside sin, cos or tan is already
    # read when it's checked (see `_agrees_in_degrees`), so it adds nothing to keep. Written or typed.
    ("^{\\circ}", ""), ("^\\circ", ""), ("°", ""), ("º", ""), ("^∘", ""), ("∘", ""),
]
_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
_SUPERSCRIPT_RUN = re.compile("[⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+")
_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉₋", "0123456789-")
_SUBSCRIPT_RUN = re.compile("[₀₁₂₃₄₅₆₇₈₉₋]+")
# ∫_{0}^{1}, the way a limit is often typed. Only straight after the sign.
_BRACED_LIMITS = re.compile(r"∫\s*(?:(_|\^)\{([^{}]*)\})\s*(?:(_|\^)\{([^{}]*)\})?")
_INNER_LEIBNIZ = re.compile(r"(?<![a-zA-Z])d[a-zA-Z]\s*/\s*d[a-zA-Z]")
_QUESTION_LABEL = re.compile(r"^\(\d+\)\s*[a-zA-Z(]")


class MathParseError(ValueError):
    """Base class. `reason` is safe to show in development tooling."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Unsupported(MathParseError):
    pass


class Ambiguous(MathParseError):
    pass


class Malformed(MathParseError):
    pass


class Incomplete(MathParseError):
    pass


NodeKind = Literal["num", "var", "const", "neg", "add", "mul", "div", "pow", "func", "deriv", "integral"]


@dataclass(frozen=True)
class Node:
    kind: NodeKind
    args: tuple[Node, ...] = ()
    value: str = ""
    # add: per-term sign, parallel to args.
    signs: tuple[int, ...] = ()
    # num: written with a decimal point, so the student may be rounding.
    decimal: bool = False
    # An integral's args are the integrand and, if it's definite, then the lower and upper limits.
    # Its `value` is the variable of integration.


@dataclass(frozen=True)
class Token:
    kind: Literal["num", "word", "op", "lparen", "rparen", "deriv", "integral", "differential", "bar"]
    text: str
    # There was a gap before it. Only `1/2 x` cares, where the gap says what the fraction stops at.
    spaced: bool = False


def normalise(text: str) -> str:
    out = text.strip()
    # 3/4 ÷ 2/3 means (3/4) ÷ (2/3), and as one row of divisions it would be 9/8's opposite. Across
    # an equals sign nothing competes: 1 ÷ 6 = 1/6 can only mean one thing.
    if any("÷" in side and "/" in side for side in out.split("=")):
        raise Ambiguous("÷ together with fraction bars")
    for old, new in _NORMALISE:
        out = out.replace(old, new)
    out = _SUPERSCRIPT_RUN.sub(lambda run: "^" + run.group(0).translate(_SUPERSCRIPTS), out)
    out = _SUBSCRIPT_RUN.sub(lambda run: "_" + run.group(0).translate(_SUBSCRIPTS), out)
    return _BRACED_LIMITS.sub(_unbrace_limits, out)


def _unbrace_limits(match: re.Match[str]) -> str:
    first = f"{match[1]}({match[2]})"
    second = f"{match[3]}({match[4]})" if match[3] else ""
    return f"∫{first}{second} "


def _word_tokens(word: str) -> list[Token]:
    """Split a run of letters into tokens, so that a power binds to its own letter only.

    `xy^2` is x times y squared, never (xy) squared.
    """

    lower = word.lower()
    if lower in UNSUPPORTED_WORDS:
        raise Unsupported(f"{word} is outside the supported domain")
    if lower in PROSE_WORDS:
        raise Malformed(f"{word} looks like prose")
    if word in UNIT_WORDS:
        raise Ambiguous(f"{word} looks like a unit")
    if lower in FUNCTIONS or word == "pi":
        return [Token("word", lower)]
    for name in FUNCTIONS:
        if lower.startswith(name):
            raise Ambiguous(f"{word} needs brackets after {name}")
    letters = re.findall(r"pi|[a-zA-Z]", word)
    if len(letters) > 3:
        raise Malformed(f"{word} looks like prose")
    for letter in letters:
        if letter in ("i", "I"):
            # Almost always the imaginary unit, and complex numbers are out of scope.
            raise Unsupported("the imaginary unit")
    return [Token("word", letter) for letter in letters]


_ENDS_IN_DIFFERENTIAL = re.compile(r"^(.*)d([a-zA-Z])$")


def _tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    gaps: set[int] = set()
    # Inside an integral, `dx` closes it. Anywhere else it stays d times x.
    open_integrals = 0
    i = 0
    while i < len(text):
        if len(tokens) > MAX_TOKENS:
            raise Unsupported("expression too long")
        ch = text[i]
        if ch.isspace():
            gaps.add(len(tokens))
            i += 1
            continue
        if ch == "∫":
            tokens.append(Token("integral", ch))
            open_integrals += 1
            i += 1
            continue
        if ch == "|":
            tokens.append(Token("bar", ch))
            i += 1
            continue
        if ch in UNSUPPORTED_CHARS:
            raise Unsupported(f"unsupported symbol {ch!r}")
        if _INNER_LEIBNIZ.match(text, i):
            raise Unsupported("a derivative inside an expression, as in implicit differentiation")
        deriv = re.match(r"d\s*/\s*d([a-zA-Z])(?![a-zA-Z])", text[i:])
        if deriv:
            tokens.append(Token("deriv", deriv.group(1)))
            i += deriv.end()
            continue
        # Only ASCII digits: isdigit() also accepts characters the literal pattern below can't
        # match, which used to stop the tokenizer advancing, forever.
        if ch in DIGITS or (ch == "." and i + 1 < len(text) and text[i + 1] in DIGITS):
            match = re.match(r"[0-9]*\.?[0-9]*", text[i:])
            assert match is not None
            literal = match.group(0)
            if len(literal.replace(".", "")) > MAX_NUMBER_DIGITS:
                raise Unsupported("number too long")
            if literal.endswith("."):
                raise Ambiguous("number ends with a decimal point")
            tokens.append(Token("num", literal))
            i += len(literal)
            continue
        if ch.isalpha() and ch.isascii():
            match = re.match(r"[a-zA-Z]+", text[i:])
            assert match is not None
            differential = _ENDS_IN_DIFFERENTIAL.match(match.group(0)) if open_integrals else None
            if differential:
                if differential[1]:
                    tokens.extend(_word_tokens(differential[1]))
                tokens.append(Token("differential", differential[2]))
                open_integrals -= 1
            else:
                tokens.extend(_word_tokens(match.group(0)))
            i += len(match.group(0))
            continue
        if ch in "+-*/^" or (ch == "_" and open_integrals):
            tokens.append(Token("op", ch))
            i += 1
            continue
        if ch == "(":
            tokens.append(Token("lparen", ch))
            i += 1
            continue
        if ch == ")":
            tokens.append(Token("rparen", ch))
            i += 1
            continue
        if ch == "'":
            raise Unsupported("prime notation inside an expression")
        raise Malformed(f"unexpected character {ch!r}")
    if len(tokens) > MAX_TOKENS:
        raise Unsupported("expression too long")
    return [replace(token, spaced=True) if position in gaps else token for position, token in enumerate(tokens)]


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.depth = 0
        self.nodes = 0
        # How many |...| are open, which is what says whether a bar opens one or closes it.
        self.open_bars = 0

    def peek(self) -> Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self) -> Token:
        token = self.peek()
        if token is None:
            raise Incomplete("expression ends early")
        self.pos += 1
        return token

    def node(self, kind: NodeKind, **kwargs: object) -> Node:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise Unsupported("expression too complex")
        return Node(kind, **kwargs)  # type: ignore[arg-type]

    def enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise Unsupported("expression nested too deeply")

    def parse(self) -> Node:
        if not self.tokens:
            raise Incomplete("empty expression")
        result = self.expression()
        if self.peek() is not None:
            token = self.next()
            if token.kind == "rparen":
                raise Malformed("unbalanced closing bracket")
            raise Malformed(f"unexpected {token.text!r}")
        return result

    def expression(self) -> Node:
        self.enter()
        terms = [self.term()]
        signs = [1]
        while (token := self.peek()) is not None and token.kind == "op" and token.text in "+-":
            self.next()
            signs.append(1 if token.text == "+" else -1)
            terms.append(self.term())
        self.depth -= 1
        if len(terms) == 1:
            return terms[0]
        return self.node("add", args=tuple(terms), signs=tuple(signs))

    def term(self) -> Node:
        self.enter()
        factors = [self.unary()]
        result: Node | None = None
        just_divided = False
        while (token := self.peek()) is not None:
            if token.kind == "op" and token.text == "*":
                self.next()
                factors.append(self.unary())
                just_divided = False
            elif token.kind == "op" and token.text == "/":
                self.next()
                numerator = self._collapse(result, factors)
                denominator = self.unary()
                result = self.node("div", args=(numerator, denominator))
                factors = []
                just_divided = True
            elif self._starts_implicit_factor(token):
                # 1/2 x^2, with the gap, is the way a half of x^2 is written. 1/2x could be either.
                if just_divided and not (token.spaced and result is not None and _is_fraction_of_numbers(result)):
                    raise Ambiguous("implicit multiplication after a division, like 1/2x")
                just_divided = False
                if token.kind == "num":
                    raise Ambiguous("a number directly after a term, like x2")
                factors.append(self.power())
            else:
                break
        self.depth -= 1
        return self._collapse(result, factors)

    def _collapse(self, result: Node | None, factors: list[Node]) -> Node:
        items = ([result] if result is not None else []) + factors
        if len(items) == 1:
            return items[0]
        return self.node("mul", args=tuple(items))

    def _starts_implicit_factor(self, token: Token) -> bool:
        return token.kind in ("num", "word", "lparen", "deriv", "integral") or (token.kind == "bar" and not self.open_bars)

    def unary(self) -> Node:
        token = self.peek()
        if token is None:
            raise Incomplete("expression ends after an operator")
        if token.kind == "op" and token.text == "-":
            self.next()
            return self.node("neg", args=(self.unary(),))
        if token.kind == "op" and token.text == "+":
            self.next()
            return self.unary()
        return self.power()

    def power(self) -> Node:
        self.enter()
        base = self.primary()
        token = self.peek()
        if token is not None and token.kind == "op" and token.text == "^":
            self.next()
            exponent = self.exponent()
            self._refuse_unclear_exponent_end(base, exponent)
            base = self.node("pow", args=(base, exponent))
        self.depth -= 1
        return base

    def _refuse_unclear_exponent_end(self, base: Node, exponent: Node) -> None:
        """Where does this exponent stop? On paper its height says, and in text it often can't."""

        following = self.peek()
        if following is None or following.kind == "rparen":
            return
        numeric_base = base.kind == "num" or (base.kind == "const" and base.value == "e")
        # e^-2x has the same problem as e^2x.
        exponent = exponent.args[0] if exponent.kind == "neg" else exponent
        if exponent.kind == "num":
            if following.kind == "word" and numeric_base:
                raise Ambiguous("e^2x could be (e^2)x or e^(2x)")
            after = self.tokens[self.pos + 1] if self.pos + 1 < len(self.tokens) else None
            if following.text == "/" and after is not None and after.kind == "num":
                # x^2/2 and x^3/3 are fractions. These are usually powers: x^1/2, x^3/2, 8^2/3.
                odd_over_two = int(exponent.value) % 2 == 1 and after.text == "2" if exponent.value.isdigit() else False
                if numeric_base or exponent.value == "1" or odd_over_two or (exponent.value, after.text) == ("2", "3"):
                    raise Ambiguous("x^1/2 could be (x^1)/2 or x^(1/2)")
        if exponent.kind == "var" and base.kind == "var" and following.kind == "op" and following.text in "+-":
            raise Ambiguous("x^n-1 could be (x^n) - 1 or x^(n-1)")

    def exponent(self) -> Node:
        token = self.peek()
        if token is None:
            raise Incomplete("exponent missing")
        if token.kind == "op" and token.text == "-":
            self.next()
            return self.node("neg", args=(self.exponent(),))
        return self.power()

    def primary(self) -> Node:
        token = self.next()
        if token.kind == "num":
            return self.node("num", value=token.text, decimal="." in token.text)
        if token.kind == "lparen":
            inner = self.expression()
            closing = self.peek()
            if closing is None:
                raise Incomplete("bracket left open")
            if closing.kind != "rparen":
                raise Malformed("bracket not closed")
            self.next()
            return inner
        if token.kind == "deriv":
            following = self.peek()
            if following is None:
                raise Incomplete("nothing to differentiate")
            if following.kind != "lparen":
                raise Ambiguous("d/dx without brackets has an unclear scope")
            self.next()
            inner = self.expression()
            closing = self.peek()
            if closing is None:
                raise Incomplete("bracket left open")
            if closing.kind != "rparen":
                raise Malformed("bracket not closed")
            self.next()
            return self.node("deriv", args=(inner,), value=token.text)
        if token.kind == "word":
            return self.word(token.text)
        if token.kind == "integral":
            return self.integral()
        if token.kind == "bar":
            return self.absolute_value()
        if token.kind == "rparen":
            raise Malformed("unexpected closing bracket")
        raise Malformed(f"unexpected {token.text!r}")

    def absolute_value(self) -> Node:
        self.open_bars += 1
        inner = self.expression()
        closing = self.peek()
        if closing is None:
            raise Incomplete("absolute value left open")
        if closing.kind != "bar":
            raise Malformed("absolute value not closed")
        self.next()
        self.open_bars -= 1
        return self.node("func", args=(inner,), value="abs")

    def integral(self) -> Node:
        lower, upper = None, None
        for _ in range(2):
            token = self.peek()
            if token is None or token.kind != "op" or token.text not in "_^":
                break
            self.next()
            if token.text == "_":
                lower = self.limit()
            else:
                upper = self.limit()
        if (lower is None) != (upper is None):
            raise Ambiguous("an integral with only one limit")
        token = self.peek()
        if token is not None and token.kind == "differential":
            # ∫ dx is the integral of 1.
            integrand = self.node("num", value="1")
        else:
            # A bar still open outside isn't closed by anything in here.
            bars, self.open_bars = self.open_bars, 0
            integrand = self.expression()
            self.open_bars = bars
        closing = self.peek()
        if closing is None or closing.kind != "differential":
            # With nothing to say what it's with respect to, it can't be checked.
            raise Unsupported("an integral with no dx")
        self.next()
        limits = () if lower is None or upper is None else (lower, upper)
        return self.node("integral", args=(integrand, *limits), value=closing.text)

    def limit(self) -> Node:
        token = self.peek()
        if token is None:
            raise Incomplete("a limit is missing")
        if token.kind == "op" and token.text == "-":
            self.next()
            return self.node("neg", args=(self.limit(),))
        if token.kind not in ("num", "word", "lparen"):
            raise Malformed("not a limit")
        return self.primary()

    def word(self, word: str) -> Node:
        if word in FUNCTIONS:
            return self.function(word)
        if word in CONSTANTS:
            return self.node("const", value=word)
        following = self.peek()
        if following is not None and following.kind == "lparen" and word in FUNCTION_LETTERS:
            raise Unsupported("function notation inside an expression")
        return self.node("var", value=word)

    def function(self, name: str) -> Node:
        following = self.peek()
        if following is None:
            raise Incomplete(f"{name} has no argument")
        power: Node | None = None
        if following.kind == "op" and following.text == "^":
            # sin^2(x). Negative powers mean an inverse function here, so they are refused.
            self.next()
            exponent = self.next()
            if exponent.kind != "num":
                raise Unsupported(f"{name}^... other than a positive whole power")
            power = self.node("num", value=exponent.text)
            following = self.peek()
            if following is None:
                raise Incomplete(f"{name} has no argument")
        if following.kind == "bar" and not self.open_bars:
            # ln|x|
            self.next()
            inner = self.absolute_value()
        elif following.kind == "word" and power is None and self._is_a_lone_letter_argument():
            inner = self.word(self.next().text)
        elif following.kind == "num" and power is None and self._is_a_lone_number_argument():
            number = self.next()
            inner = self.node("num", value=number.text, decimal="." in number.text)
        elif following.kind != "lparen":
            raise Ambiguous(f"{name} without brackets has an unclear scope")
        else:
            self.next()
            inner = self.expression()
            closing = self.peek()
            if closing is None:
                raise Incomplete("bracket left open")
            if closing.kind != "rparen":
                raise Malformed("bracket not closed")
            self.next()
        applied = self.node("func", args=(inner,), value=name)
        if power is not None:
            return self.node("pow", args=(applied, power))
        return applied


    def _is_a_lone_letter_argument(self) -> bool:
        """`sin x`, `sin x + 1` and `sin x cos x` can only mean one thing. `sin x^2` and `sin xy` can't."""

        letter = self.peek()
        if letter is None or letter.text in FUNCTIONS:
            return False
        after = self.tokens[self.pos + 1] if self.pos + 1 < len(self.tokens) else None
        if after is None or after.kind in ("rparen", "differential", "bar"):
            return True
        if after.kind == "op":
            return after.text != "^"
        return after.kind == "word" and after.text in FUNCTIONS

    def _is_a_lone_number_argument(self) -> bool:
        """`sin 30` and `sin 30 + cos 60`, as written in a trigonometry lesson. `sin 30 / 2`, `sin 30^2` and
        `sin 30x` can be read two ways, so they still aren't."""

        after = self.tokens[self.pos + 1] if self.pos + 1 < len(self.tokens) else None
        if after is None or after.kind == "rparen":
            return True
        return after.kind == "op" and after.text in ("+", "-")


def _is_fraction_of_numbers(node: Node) -> bool:
    return node.kind == "div" and all(part.kind == "num" for part in node.args)


def parse_expression(text: str) -> Node:
    cleaned = normalise(text)
    if len(cleaned) > MAX_TEXT_LENGTH:
        raise Unsupported("expression too long")
    return _Parser(_tokenize(cleaned)).parse()


def _power_nesting(node: Node) -> int:
    inner = max((_power_nesting(child) for child in node.args), default=0)
    return inner + 1 if node.kind == "pow" or (node.kind == "func" and node.value == "exp") else inner


def _count_terms(node: Node) -> int:
    """How many terms this could have once multiplied out. Doesn't build anything."""

    if node.kind == "add":
        counts = [_count_terms(child) for child in node.args]
        # Adding fractions over a common denominator multiplies the denominators together.
        if any(contains_kind(child, "div") for child in node.args):
            return math.prod(counts)
        return sum(counts)
    if node.kind == "mul":
        return math.prod(_count_terms(child) for child in node.args)
    if node.kind == "div":
        return _count_terms(node.args[0]) * _count_terms(node.args[1])
    if node.kind == "neg":
        return _count_terms(node.args[0])
    if node.kind == "pow":
        base = _count_terms(node.args[0])
        # The exponent may be a sum not yet worked out, like (a + b)^(3*4).
        power = _convert(node.args[1])
        if base > 1 and power.is_Integer and power > 0:
            return math.comb(int(power) + base - 1, base - 1) if power <= MAX_EXPONENT else MAX_EXPANDED_TERMS + 1
        return base
    if node.kind in ("func", "deriv", "integral"):
        return _count_terms(node.args[0])
    return 1


def _letters(node: Node) -> set[str]:
    own = {node.value} if node.kind == "var" else set()
    return own.union(*(_letters(child) for child in node.args)) if node.args else own


def _has_variable(node: Node) -> bool:
    # The x in ∫_0^1 x dx has been integrated away, and the value is a number.
    if node.kind == "integral" and len(node.args) == 3:
        return bool(_letters(node.args[0]) - {node.value}) or any(_has_variable(child) for child in node.args[1:])
    return node.kind == "var" or any(_has_variable(child) for child in node.args)


def _largest_number(node: Node) -> float:
    own = float(node.value) if node.kind == "num" else 0.0
    return max([own, *(_largest_number(child) for child in node.args)])


def _has_oversized_variable_exponent(node: Node) -> bool:
    exponent = None
    if node.kind == "pow":
        exponent = node.args[1]
    elif node.kind == "func" and node.value == "exp":
        exponent = node.args[0]
    if exponent is not None and _has_variable(exponent) and _largest_number(exponent) > MAX_NUMBER_IN_VARIABLE_EXPONENT:
        return True
    return any(_has_oversized_variable_exponent(child) for child in node.args)


def to_sympy(node: Node) -> sp.Expr:
    if _power_nesting(node) > MAX_POWER_NESTING:
        raise Unsupported("powers nested too deeply")
    if _has_oversized_variable_exponent(node):
        raise Unsupported("exponent too large")
    if _count_terms(node) > MAX_EXPANDED_TERMS:
        raise Unsupported("expression too large to expand")
    try:
        return _convert(node)
    except TypeError as exc:
        # SymPy couldn't say which of two values was the larger, and only refusing is left.
        raise Unsupported("a comparison SymPy can't decide") from exc


def _finite(expr: sp.Expr) -> sp.Expr:
    if expr.has(sp.nan, sp.zoo, sp.oo, -sp.oo):
        raise Unsupported("undefined value")
    return expr


def _convert(node: Node) -> sp.Expr:
    if node.kind == "num":
        return sp.Rational(node.value)
    if node.kind == "var":
        return sp.Symbol(node.value, real=True)
    if node.kind == "const":
        return CONSTANTS[node.value]
    if node.kind == "neg":
        return -_convert(node.args[0])
    if node.kind == "add":
        total: sp.Expr = sp.Integer(0)
        for sign, term in zip(node.signs, node.args, strict=True):
            total = total + sign * _convert(term)
        return total
    if node.kind == "mul":
        product: sp.Expr = sp.Integer(1)
        for factor in node.args:
            product = product * _convert(factor)
        return product
    if node.kind == "div":
        denominator = _convert(node.args[1])
        if denominator.is_zero:
            raise Unsupported("division by zero")
        return _convert(node.args[0]) / denominator
    if node.kind == "pow":
        exponent = _convert(node.args[1])
        if exponent.is_number and abs(exponent) > MAX_PLAIN_EXPONENT:
            raise Unsupported("exponent too large")
        base = _convert(node.args[0])
        if base.is_number and base.is_zero and exponent.is_number and exponent.is_nonpositive:
            raise Unsupported("0 raised to a non-positive power")
        return _finite(base**_finite(exponent))
    if node.kind == "func":
        argument = _convert(node.args[0])
        if node.value == "exp" and argument.is_number and abs(argument) > MAX_EXP_ARGUMENT:
            raise Unsupported("exponent too large")
        return _finite(FUNCTIONS[node.value](argument))
    if node.kind == "deriv":
        return sp.diff(_convert(node.args[0]), sp.Symbol(node.value, real=True))
    if node.kind == "integral":
        if len(node.args) == 1:
            # A family of functions, so it isn't one value. See _check_antiderivative in the verifier.
            raise Unsupported("an indefinite integral as part of an expression")
        integrand, lower, upper = (_convert(child) for child in node.args)
        try:
            return definite_value(integrand, sp.Symbol(node.value, real=True), lower, upper)
        except CannotIntegrate as exc:
            raise Unsupported(str(exc)) from exc
    raise Malformed(f"unknown node {node.kind}")


def contains_kind(node: Node, kind: NodeKind) -> bool:
    return node.kind == kind or any(contains_kind(child, kind) for child in node.args)


def has_decimal(node: Node) -> bool:
    return node.decimal or any(has_decimal(child) for child in node.args)


def leaf_count(node: Node) -> int:
    return 1 if not node.args else sum(leaf_count(child) for child in node.args)


def has_distribution(node: Node) -> bool:
    """True when the expression has a product over, or a power of, a sum."""

    if node.kind == "mul" and any(child.kind == "add" for child in node.args):
        return True
    if node.kind == "neg" and node.args[0].kind == "add":
        return True
    if node.kind == "pow" and node.args[0].kind == "add":
        return True
    return any(has_distribution(child) for child in node.args)


def format_math(expr: sp.Expr) -> str:
    """Render a SymPy expression the way a student would write it: `3x^2 + 2`."""

    text = str(expr).replace("**", "^").replace("log(", "ln(").replace("Abs(", "abs(")
    text = re.sub(r"abs\(([^()]*)\)", r"|\1|", text)
    text = re.sub(r"ln\(\|([^|]*)\|\)", r"ln|\1|", text)
    text = re.sub(r"\bE\b", "e", text)
    text = re.sub(r"exp\(([^()]*)\)", r"e^(\1)", text)
    text = re.sub(r"(\d)\*(?=[a-zA-Z(])", r"\1", text)
    text = re.sub(r"(?<=[a-zA-Z)])\*(?=[a-zA-Z(])", "", text)
    return text


def format_node(node: Node) -> str:
    """Render a parsed expression exactly as the student structured it.

    SymPy distributes `2(x + 3)` into `2x + 6` on construction, so showing a SymPy
    form of the student's working could reveal the answer.
    """

    return _render(node, 0)


_PRECEDENCE = {"add": 1, "neg": 2, "mul": 2, "div": 2, "pow": 3}


def _render(node: Node, parent: int) -> str:
    if node.kind == "num":
        return node.value
    if node.kind in ("var", "const"):
        return node.value
    if node.kind == "func":
        if node.value == "abs":
            return f"|{_render(node.args[0], 0)}|"
        return f"{node.value}({_render(node.args[0], 0)})"
    if node.kind == "deriv":
        return f"d/d{node.value}({_render(node.args[0], 0)})"
    if node.kind == "integral":
        limits = f"_{_render(node.args[1], 4)}^{_render(node.args[2], 4)}" if len(node.args) == 3 else ""
        integrand = _render(node.args[0], 0)
        text = f"∫{limits} {integrand} d{node.value}"
        return f"({text})" if parent else text
    own = _PRECEDENCE[node.kind]
    if node.kind == "add":
        text = _render(node.args[0], own) if node.signs[0] > 0 else "-" + _render(node.args[0], own)
        for sign, term in zip(node.signs[1:], node.args[1:], strict=True):
            # own + 1, so that x - (y - z) keeps its bracket.
            text += (" + " if sign > 0 else " - ") + _render(term, own + 1)
    elif node.kind == "neg":
        text = "-" + _render(node.args[0], own)
    elif node.kind == "mul":
        # (1/2)x^2 keeps its bracket, as 1/2x^2 could be read the other way.
        text = _render(node.args[0], own + 1 if node.args[0].kind == "div" else own)
        for factor in node.args[1:]:
            # A negative or a fraction needs its bracket, or 2(-3) would read as 2 - 3.
            rendered = _render(factor, own + 1)
            text += f" * {rendered}" if rendered[0] in DIGITS else rendered
    elif node.kind == "div":
        text = f"{_render(node.args[0], own)}/{_render(node.args[1], own + 1)}"
    else:
        text = f"{_render(node.args[0], own + 1)}^{_render(node.args[1], own + 1)}"
    return f"({text})" if own < parent else text


StatementKind = Literal["expression", "equality", "definition", "derivative_of", "solutions", "approximation", "task"]


@dataclass(frozen=True)
class Precision:
    """How a line says its answer is rounded. `places` is decimal places: -1 is to the nearest ten."""

    places: int | None = None
    figures: int | None = None


@dataclass(frozen=True)
class Statement:
    """One written line, split on `=` and recognised derivative notation."""

    kind: StatementKind
    text: str
    parts: tuple[Node, ...] = ()
    # The line began with "=" and so continues the previous line's value.
    continuation: bool = False
    # definition / derivative_of: the name defined or differentiated, like "f" or "y".
    name: str = ""
    variable: str = ""
    # solutions: x = 1 or x = 2.
    values: tuple[Node, ...] = field(default=())
    # A rounding the line itself asks for, as in `1/3 = 0.33 (2dp)`.
    precision: Precision | None = None
    # A task the line itself states, as in `Simplify: 3x + 2x`. See task_intent.py.
    task: str = ""


_DEFINITION = re.compile(r"^([fgh])\(([a-zA-Z])\)$")
_PRIME_FN = re.compile(r"^([fgh])'\(([a-zA-Z])\)$")
_PRIME_VAR = re.compile(r"^([a-zA-Z])'$")
_LEIBNIZ = re.compile(r"^d([a-zA-Z])\s*/\s*d([a-zA-Z])$")
_SECOND_ORDER = re.compile(r"(d\s*\^\s*\d|''|′′)")
_SOLUTION = re.compile(r"^([a-zA-Z])\s*=\s*(.+)$")


# Only at the end of a line that says something already, so that `y = 2dp` is still d times p.
_PRECISION_NOTE = re.compile(
    r"""[\s,;]+\(?\s*(?:(?:correct|rounded|round)\s+)?(?:to\s+)?(?:
        (?:the\s+)?nearest\s+(?P<place>ten|tens|10|hundred|100|thousand|1000|integer|whole\s+number|whole|unit|one|tenth|hundredth|thousandth)
        |(?P<count>\d+)\s*(?P<unit>d\.?\s*p\.?|decimal\s+places?|s\.?\s*f\.?|sig\.?\s*figs?\.?|significant\s+figures?)
    )\s*\)?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_NAMED_PLACES = {
    "ten": -1, "tens": -1, "10": -1, "hundred": -2, "100": -2, "thousand": -3, "1000": -3,
    "integer": 0, "whole number": 0, "whole": 0, "unit": 0, "one": 0,
    "tenth": 1, "hundredth": 2, "thousandth": 3,
}
_APPROXIMATE_SIGNS = ("≈", "≃")
_ZERO_IN_AN_ANSWER = re.compile(r"[Oo]")
_ANSWER_WITH_ZERO = re.compile(r"^[0-9Oo.]+$")
_HAS_OPERATOR = re.compile(r"[+\-*/^]")
MAX_STATED_PRECISION = 12
_ANY_LETTER = re.compile(r"[a-zA-Z]")


def _split_precision(text: str) -> tuple[str, Precision | None]:
    match = _PRECISION_NOTE.search(text)
    if match is None or match.start() == 0:
        return text, None
    before = text[: match.start()].strip()
    if not before or before[-1] in "=≈≃+-*/^(":
        return text, None
    if not (any(sign in before for sign in ("=", *_APPROXIMATE_SIGNS))):
        return text, None
    if match["place"]:
        return before, Precision(places=_NAMED_PLACES[" ".join(match["place"].lower().split())])
    unit = match["unit"].lower()
    if len(match["count"]) > 2 or int(match["count"]) > MAX_STATED_PRECISION:
        raise Unsupported("more figures than anyone rounds to")
    count = int(match["count"])
    return before, Precision(places=count) if unit.startswith("d") else Precision(figures=count)


def _read_as_numbers(text: str) -> str:
    """The O in the answer to a sum is a zero: `1/5 = O`, `3 * 4 = 1O`. Nobody solves a sum for O.

    Only there. On the left, `2O = 60` may well be an angle, and `5 = O` isn't a sum at all.
    """

    sign = next((sign for sign in ("=", *_APPROXIMATE_SIGNS) if sign in text), None)
    if sign is None or text.count(sign) != 1:
        return text
    left, answer = text.split(sign)
    if _ANY_LETTER.search(left) or not _HAS_OPERATOR.search(left) or not _ANSWER_WITH_ZERO.match(answer.strip()):
        return text
    return f"{left}{sign}{_ZERO_IN_AN_ANSWER.sub('0', answer)}"


def _parse_approximation(text: str, precision: Precision | None) -> Statement:
    for sign in _APPROXIMATE_SIGNS[1:]:
        text = text.replace(sign, _APPROXIMATE_SIGNS[0])
    pieces = [piece.strip() for piece in text.split(_APPROXIMATE_SIGNS[0])]
    if "=" in text or len(pieces) != 2:
        raise Unsupported("an approximation written with other relations on the line")
    if not pieces[1]:
        raise Incomplete("nothing after the approximation sign")
    parts = tuple(parse_expression(piece) for piece in pieces if piece)
    if any(_has_variable(part) for part in parts):
        raise Unsupported("an approximate value of a variable")
    return Statement("approximation", text, parts, continuation=not pieces[0], precision=precision)


_TASK_WORDS = {
    "simplify": "simplify", "expand": "expand", "factorise": "factorise", "factorize": "factorise", "factor": "factorise",
    "solve": "solve", "differentiate": "differentiate", "integrate": "integrate",
}
_TASK = re.compile(r"^(simplify|expand|factorise|factorize|factor|solve|differentiate|integrate)\b\s*:?\s*", re.IGNORECASE)


def written_task(text: str) -> str:
    """The task a line of words states, or nothing. `Simplify` and `Solve:` state one. `Solve it` doesn't."""

    match = _TASK.match(text.strip())
    return _TASK_WORDS[match[1].lower()] if match and not text.strip()[match.end() :] else ""


def _apply_task(statement: Statement, task: str) -> Statement:
    """`Differentiate x^3` says what `x^3` is being asked for, as d/dx( ) would."""

    if task in ("differentiate", "integrate") and statement.kind in ("expression", "equality") and statement.parts:
        letters = _letters(statement.parts[0])
        calculus = contains_kind(statement.parts[0], "deriv") or contains_kind(statement.parts[0], "integral")
        if len(letters) == 1 and not calculus:
            variable = next(iter(letters))
            kind: NodeKind = "deriv" if task == "differentiate" else "integral"
            wrapped = Node(kind, args=(statement.parts[0],), value=variable)
            return replace(statement, parts=(wrapped, *statement.parts[1:]), task=task)
    return replace(statement, task=task)


def parse_statement(text: str) -> Statement:
    cleaned = normalise(text)
    if not cleaned:
        raise Incomplete("empty line")
    if len(cleaned) > MAX_TEXT_LENGTH:
        raise Unsupported("line too long")
    task = _TASK.match(cleaned)
    if task:
        named = _TASK_WORDS[task[1].lower()]
        rest = cleaned[task.end() :]
        return _apply_task(parse_statement(rest), named) if rest else Statement("task", cleaned, task=named)
    cleaned, precision = _split_precision(cleaned)
    cleaned = _read_as_numbers(cleaned)
    if any(sign in cleaned for sign in _APPROXIMATE_SIGNS):
        return _parse_approximation(cleaned, precision)
    if _SECOND_ORDER.search(cleaned):
        raise Unsupported("higher-order derivatives")
    for ch in UNSUPPORTED_CHARS:
        if ch in cleaned:
            raise Unsupported(f"unsupported symbol {ch!r}")

    if _QUESTION_LABEL.match(cleaned):
        raise Ambiguous("a leading bracketed number may be a question label")

    solutions = _parse_solutions(cleaned)
    if solutions is not None:
        return solutions
    if "+-" in cleaned:
        # Two values at once. Reading it as a minus would turn `x - 3 = ±5` into a false alert.
        raise Unsupported("plus-or-minus in an expression")

    continuation = cleaned.startswith("=")
    if continuation:
        cleaned = cleaned[1:].strip()
        if not cleaned:
            raise Incomplete("nothing after the equals sign")
    if "=" not in cleaned:
        return Statement(
            "expression", cleaned, (parse_expression(cleaned),), continuation=continuation, precision=precision
        )

    pieces = [piece.strip() for piece in cleaned.split("=")]
    if any(not piece for piece in pieces):
        raise Incomplete("an equals sign has nothing on one side")

    head = pieces[0]
    if not continuation and len(pieces) >= 2:
        if match := _DEFINITION.match(head):
            parts = tuple(parse_expression(piece) for piece in pieces[1:])
            return Statement("definition", cleaned, parts, name=match[1], variable=match[2])
        if match := _PRIME_FN.match(head):
            parts = tuple(parse_expression(piece) for piece in pieces[1:])
            return Statement("derivative_of", cleaned, parts, name=match[1], variable=match[2])
        if match := _LEIBNIZ.match(head):
            parts = tuple(parse_expression(piece) for piece in pieces[1:])
            return Statement("derivative_of", cleaned, parts, name=match[1], variable=match[2])
        if match := _PRIME_VAR.match(head):
            parts = tuple(parse_expression(piece) for piece in pieces[1:])
            return Statement("derivative_of", cleaned, parts, name=match[1])

    parts = tuple(parse_expression(piece) for piece in pieces)
    if precision is not None and (any(_has_variable(part) for part in parts) or len(parts) > (1 if continuation else 2)):
        raise Ambiguous("a stated precision on a line with letters or several steps")
    return Statement("equality", cleaned, parts, continuation=continuation, precision=precision)


def _parse_solutions(text: str) -> Statement | None:
    """Recognise `x = 1 or x = 2`, `x = 1, x = 2`, `x = 1, 2`, and `x = +-2`."""

    if "=" not in text or text.startswith("="):
        return None
    if re.search(r"\d,\d{3}(?!\d)", text):
        raise Ambiguous("a comma that may be a thousands separator")
    branches = [b.strip() for b in re.split(r"\bor\b|,", text) if b.strip()]
    if len(branches) < 2 and "+-" not in text:
        return None

    variable = ""
    values: list[Node] = []
    for branch in branches:
        match = _SOLUTION.match(branch)
        if match:
            if variable and match[1] != variable:
                return None
            variable = match[1]
            literal = match[2].strip()
        elif variable:
            literal = branch
        else:
            return None
        if literal.startswith("+-"):
            value = parse_expression(literal[2:])
            values.extend([value, Node("neg", args=(value,))])
        elif "+-" in literal:
            raise Unsupported("plus-or-minus inside an expression")
        else:
            values.append(parse_expression(literal))
    if not variable or not values:
        return None
    if "+-" in text and not re.fullmatch(r"[a-zA-Z]", text.split("=", 1)[0].strip()):
        return None
    return Statement("solutions", text, variable=variable, values=tuple(values))
