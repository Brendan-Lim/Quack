import contextlib

import pytest
import sympy as sp

from quack.services.math_parser import (
    Ambiguous,
    Incomplete,
    Malformed,
    Unsupported,
    format_math,
    format_node,
    parse_expression,
    parse_statement,
    to_sympy,
)

x = sp.Symbol("x", real=True)
y = sp.Symbol("y", real=True)


def value(text: str) -> sp.Expr:
    return to_sympy(parse_expression(text))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2(x + 3)", 2 * (x + 3)),
        ("2x+6", 2 * x + 6),
        ("x^2", x**2),
        ("x²", x**2),
        ("3x^2 + 2", 3 * x**2 + 2),
        ("-x^2", -(x**2)),
        ("x^-2", x**-2),
        ("2^3^2", sp.Integer(512)),
        ("(x+1)(x+2)", (x + 1) * (x + 2)),
        ("xy", x * y),
        ("2x/3", 2 * x / 3),
        ("1/(2x)", 1 / (2 * x)),
        ("0.5x", x / 2),
        ("sin(x)", sp.sin(x)),
        ("sin^2(x)", sp.sin(x) ** 2),
        ("ln(x)", sp.log(x)),
        ("sqrt(x)", sp.sqrt(x)),
        ("√(x)", sp.sqrt(x)),
        ("e^x", sp.exp(x)),
        ("2π", 2 * sp.pi),
        ("3 × 4", sp.Integer(12)),
        ("6 ÷ 2", sp.Integer(3)),
        ("5 − 2", sp.Integer(3)),
        ("d/dx(x^3)", 3 * x**2),
    ],
)
def test_parses_to_expected_value(text: str, expected: sp.Expr) -> None:
    assert sp.simplify(value(text) - expected) == 0


@pytest.mark.parametrize(
    "text",
    ["1/2x", "1/2(x+1)", "sinx", "sin x^2", "sin 2x", "sin xy", "x2", "d/dx x^3", "2 3", "x = 1,000", "∫_0 x dx"],
)
def test_refuses_ambiguous_notation(text: str) -> None:
    with pytest.raises(Ambiguous):
        if "=" in text:
            parse_statement(text)
        else:
            parse_expression(text)


@pytest.mark.parametrize(
    "text",
    ["∫x", "∫ x dx", "∫_0^1 1/x dx", "lim(x)", "sum(x)", "log(x)", "x!", "x < 3", "x ≈ 3", "f(x) + 1", "x'", "[1,2]",
     "x^1000", "9^(9^9)", "1/0", "0^0", "99999999999999"],
)
def test_refuses_unsupported_notation(text: str) -> None:
    with pytest.raises(Unsupported):
        value(text)


@pytest.mark.parametrize("text", ["2x +", "(x + 1", "", "x ^"])
def test_detects_incomplete_writing(text: str) -> None:
    with pytest.raises(Incomplete):
        parse_expression(text)


@pytest.mark.parametrize("text", ["therefore", "x $ 2", "x + )", "(x + 1))", "2 * * 3"])
def test_rejects_malformed_text(text: str) -> None:
    with pytest.raises((Malformed, Incomplete, Ambiguous)):
        parse_expression(text)


@pytest.mark.parametrize(
    "attack",
    [
        "__import__('os').system('id')",
        "().__class__.__bases__",
        "exec(1)",
        "eval(x)",
        "lambda: 1",
        "open('/etc/passwd')",
        "x.real",
        "x;y",
        "Symbol('x')",
    ],
)
def test_never_executes_or_accepts_code(attack: str) -> None:
    with pytest.raises((Malformed, Unsupported, Ambiguous, Incomplete)):
        value(attack)


def test_every_unicode_character_returns_quickly() -> None:
    # A superscript digit once stopped the tokenizer advancing, forever.
    for code in list(range(0x20, 0x3000)) + [0xFF10, 0xFF11, 0x1D7CE, 0x1F986]:
        for text in (chr(code), f"x{chr(code)}", f"{chr(code)}1"):
            with contextlib.suppress(Malformed, Unsupported, Ambiguous, Incomplete):
                parse_expression(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("xy^2", x * y**2),
        ("y^2x", x * y**2),
        ("x^2y", x**2 * y),
        ("2xy^2", 2 * x * y**2),
        ("(xy)^2", x**2 * y**2),
        ("x⁵", x**5),
        ("x¹²", x**12),
        ("x⁻¹", 1 / x),
        ("2πr", 2 * sp.pi * sp.Symbol("r", real=True)),
        ("πr²", sp.pi * sp.Symbol("r", real=True) ** 2),
        ("xe^x", x * sp.exp(x)),
    ],
)
def test_a_power_binds_to_its_own_letter(text: str, expected: sp.Expr) -> None:
    assert sp.simplify(value(text) - expected) == 0


@pytest.mark.parametrize(
    "text",
    ["exp(exp(exp(99)))", "sin(exp(exp(99)))", "exp(exp(12^12))", "(a+b+c+d)^12(p+q+r+s)^12", "(1/(x+1)+1/(x+2)+1/(x+3)+1/(x+4)+1/(x+5)+1/(x+6)+1/(x+7)+1/(x+8))^12"],
)
def test_expressions_too_big_to_compute_are_refused_without_computing(text: str) -> None:
    with pytest.raises(Unsupported):
        value(text)


@pytest.mark.parametrize(
    "text",
    ["2^(99999x)", "x^(999999999999x)", "sin(2^(99999x))", "exp(99999x)", "(x+y+z+a+b+c+d)^(2*7)"],
)
def test_powers_that_hide_their_size_are_refused(text: str) -> None:
    with pytest.raises(Unsupported):
        value(text)


def test_rejects_oversized_input() -> None:
    with pytest.raises(Unsupported):
        parse_expression("x+" * 150 + "x")


def test_rejects_deep_nesting() -> None:
    with pytest.raises(Unsupported):
        parse_expression("(" * 40 + "x" + ")" * 40)


def test_rejects_power_towers() -> None:
    with pytest.raises(Unsupported):
        to_sympy(parse_expression("((x^2)^2)^2"))


def test_statement_kinds() -> None:
    assert parse_statement("2(x+3)").kind == "expression"
    assert parse_statement("2x+3 = 7").kind == "equality"
    assert parse_statement("f(x) = x^2").kind == "definition"
    assert parse_statement("f'(x) = 2x").kind == "derivative_of"
    assert parse_statement("dy/dx = 2x").kind == "derivative_of"
    assert parse_statement("y' = 2x").name == "y"
    assert parse_statement("x = 1 or x = 2").kind == "solutions"
    assert parse_statement("x = 1, 2").kind == "solutions"
    assert parse_statement("x = ±2").kind == "solutions"


def test_continuation_line() -> None:
    statement = parse_statement("= 2x + 6")
    assert statement.continuation
    assert statement.kind == "expression"


def test_second_derivatives_are_unsupported() -> None:
    with pytest.raises(Unsupported):
        parse_statement("d^2y/dx^2 = 2")


def test_trailing_equals_is_incomplete() -> None:
    with pytest.raises(Incomplete):
        parse_statement("2x + 3 =")


def test_format_math_reads_like_handwriting() -> None:
    assert format_math(3 * x**2 + 2) == "3x^2 + 2"


@pytest.mark.parametrize(
    "written",
    ["2(-3)", "3(-x)", "x(-1)", "2*3^2", "2*(1/2)", "2-(x+3)", "x-(y-z)", "(2x)(3x)", "-(x-3)", "(-x)^2", "x^-2", "1/(x+1)", "(x+1)/2", "2^(x+1)", "-x^2", "x/(y*z)", "(x/y)/z"],
)
def test_format_node_never_changes_the_value(written: str) -> None:
    node = parse_expression(written)
    assert sp.simplify(to_sympy(parse_expression(format_node(node))) - to_sympy(node)) == 0


@pytest.mark.parametrize(
    ("written", "rendered"),
    [
        ("2(x+3)", "2(x + 3)"),
        ("-(x-3)", "-(x - 3)"),
        ("(x+1)^2", "(x + 1)^2"),
        ("x^2 * x^3", "x^2x^3"),
        ("(x+1)/(x-1)", "(x + 1)/(x - 1)"),
        ("d/dx(x^3+2x)", "d/dx(x^3 + 2x)"),
    ],
)
def test_format_node_keeps_the_students_structure(written: str, rendered: str) -> None:
    node = parse_expression(written)
    assert format_node(node) == rendered
    assert sp.simplify(to_sympy(parse_expression(format_node(node))) - to_sympy(node)) == 0
