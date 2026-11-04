"""The labelled cases. Each one is both a synthetic render and a prompt for real collection.

`lines` is ground truth, written by a person from the mathematics, never taken from a model.
`expected` is the behaviour we want from the whole pipeline:

- no_error: the working is right, so Quack stays silent.
- confirmed_error: there is a real mistake, at `error_line` (0-indexed among maths lines).
- abstain: Quack must not alert. uncertain and unsupported are both acceptable.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Case:
    id: str
    lines: tuple[str, ...]
    expected: str
    group: str
    error_line: int | None = None
    operation: str = "unknown"
    tags: tuple[str, ...] = ()
    style: str = "casual"
    # Overrides for drawing, one per written line, when a line is not simply its text.
    draw: tuple[dict, ...] | None = None
    # Lines drawn but not counted as maths, such as crossed-out working.
    note: str = field(default="")


def _valid(id: str, *lines: str, group: str, operation: str, **kw) -> Case:
    return Case(id, lines, "no_error", group, operation=operation, **kw)


def _invalid(id: str, *lines: str, group: str, operation: str, error_line: int | None = None, **kw) -> Case:
    return Case(id, lines, "confirmed_error", group, error_line=error_line if error_line is not None else len(lines) - 1, operation=operation, **kw)


def _sum(id: str, line: str, wrong: bool, drawn: str | None = None, **kw) -> Case:
    """One line of working on its own, as a student checks a sum. `drawn` is how it's written."""

    make = _invalid if wrong else _valid
    group = "standalone_invalid" if wrong else "standalone_valid"
    tags = ("standalone", *kw.pop("tags", ()))
    extra = {"draw": ({"text": drawn},)} if drawn else {}
    return make(id, line, group=group, operation=kw.pop("operation", "arithmetic"), tags=tags, **extra, **kw)


CASES: list[Case] = [
    # One line on its own. A small cross, a cross drawn like a large letter x, and a division sign.
    _sum("st_i_mul_2x3", "2*3=5", True),
    _sum("st_i_mul_3x4", "3*4=10", True),
    _sum("st_v_mul_2x3", "2*3=6", False),
    _sum("st_v_mul_3x4", "3*4=12", False),
    _sum("st_i_bigx_2x3", "2*3=5", True, drawn="2x3=5", tags=("cross_as_x",)),
    _sum("st_i_bigx_3x4", "3*4=10", True, drawn="3x4=10", tags=("cross_as_x",)),
    _sum("st_v_bigx_2x3", "2*3=6", False, drawn="2x3=6", tags=("cross_as_x",)),
    _sum("st_v_bigx_3x4", "3*4=12", False, drawn="3x4=12", tags=("cross_as_x",)),
    _sum("st_i_div", "6/2=2", True, drawn="6÷2=2"),
    _sum("st_v_div", "6/2=3", False, drawn="6÷2=3"),
    _sum("st_i_add", "7+5=11", True),
    _sum("st_v_add", "7+5=12", False),
    _sum("st_i_diff", "d/dx(x^3+2x)=3x+2", True, operation="differentiate", tags=("superscript",)),
    _sum("st_v_diff", "d/dx(x^3+2x)=3x^2+2", False, operation="differentiate", tags=("superscript",)),
    _sum("st_i_diff_power", "d/dx(x^3)=3x", True, operation="differentiate", tags=("superscript",)),
    _sum("st_i_dist", "2(x+3)=2x+3", True, operation="expand"),
    # The three demonstrations
    _invalid("demo_algebra", "2(x+3)", "=2x+3", group="algebra_invalid", operation="expand", style="neat", tags=("demo",)),
    _invalid("demo_diff", "d/dx(x^3+2x)", "=3x+2", group="diff_invalid", operation="differentiate", style="neat", tags=("demo", "superscript")),
    _valid("demo_correct", "3(x+2)", "=3x+6", group="algebra_valid", operation="expand", style="neat", tags=("demo",)),
    # Valid algebra
    _valid("alg_v_expand", "2(x+3)", "=2x+6", group="algebra_valid", operation="expand"),
    _valid("alg_v_two_brackets", "(x+1)(x+2)", "=x^2+3x+2", group="algebra_valid", operation="expand", tags=("superscript",)),
    _valid("alg_v_like_terms", "2x+3x+4", "=5x+4", group="algebra_valid", operation="combine_like_terms"),
    _valid("alg_v_negative", "-2(x-4)", "=-2x+8", group="algebra_valid", operation="expand"),
    _valid("alg_v_square", "(x+3)^2", "=x^2+6x+9", group="algebra_valid", operation="expand", tags=("superscript",)),
    _valid("alg_v_factor", "x^2+5x+6", "=(x+2)(x+3)", group="algebra_valid", operation="factor", tags=("superscript",)),
    _valid("alg_v_arith", "12+7", "=19", group="algebra_valid", operation="arithmetic"),
    _valid("alg_v_chain", "3(x+1)+2", "=3x+3+2", "=3x+5", group="algebra_valid", operation="expand"),
    _valid("alg_v_unconventional", "(x+1)(x+2)", "=x(x+3)+2", group="algebra_valid", operation="expand", tags=("unconventional",)),
    _valid("alg_v_equation", "2x+3=7", "2x=4", "x=2", group="algebra_valid", operation="solve_equation"),
    _valid("alg_v_quadratic", "x^2-5x+6=0", "(x-2)(x-3)=0", group="algebra_valid", operation="solve_equation", tags=("superscript",)),
    # Invalid algebra
    _invalid("alg_i_distribution", "4(x+5)", "=4x+5", group="algebra_invalid", operation="expand"),
    _invalid("alg_i_sign", "-(x-3)", "=-x-3", group="algebra_invalid", operation="expand"),
    _invalid("alg_i_square", "(x+2)^2", "=x^2+4", group="algebra_invalid", operation="expand", tags=("superscript",)),
    _invalid("alg_i_like_terms", "3x+4x", "=8x", group="algebra_invalid", operation="combine_like_terms"),
    _invalid("alg_i_arith", "15-8", "=9", group="algebra_invalid", operation="arithmetic"),
    _invalid("alg_i_two_brackets", "(x+1)(x+2)", "=x^2+2", group="algebra_invalid", operation="expand", tags=("superscript",)),
    _invalid("alg_i_factor", "x^2+5x+6", "=(x+1)(x+6)", group="algebra_invalid", operation="factor", tags=("superscript",)),
    _invalid("alg_i_equation_sign", "2x+3=7", "2x=10", group="algebra_invalid", operation="solve_equation"),
    _invalid("alg_i_equation_divide", "3x=12", "x=36", group="algebra_invalid", operation="solve_equation"),
    _invalid("alg_i_later_step", "2(x+3)+1", "=2x+6+1", "=2x+8", group="algebra_invalid", operation="expand"),
    # Valid differentiation
    _valid("diff_v_power", "d/dx(x^4)", "=4x^3", group="diff_valid", operation="differentiate", tags=("superscript",)),
    _valid("diff_v_sum", "d/dx(x^2+3x)", "=2x+3", group="diff_valid", operation="differentiate", tags=("superscript",)),
    _valid("diff_v_constant", "d/dx(5x^2+7)", "=10x", group="diff_valid", operation="differentiate", tags=("superscript",)),
    _valid("diff_v_poly", "d/dx(2x^3-x+1)", "=6x^2-1", group="diff_valid", operation="differentiate", tags=("superscript",)),
    _valid("diff_v_leibniz", "y=x^3", "dy/dx=3x^2", group="diff_valid", operation="differentiate", tags=("superscript",)),
    _valid("diff_v_function", "f(x)=x^2+x", "f'(x)=2x+1", group="diff_valid", operation="differentiate", tags=("superscript",)),
    _valid("diff_v_chain", "d/dx((2x+1)^3)", "=6(2x+1)^2", group="diff_valid", operation="differentiate", tags=("superscript", "nested")),
    _valid("diff_v_sin", "d/dx(sin(2x))", "=2cos(2x)", group="diff_valid", operation="differentiate", tags=("nested",)),
    # Invalid differentiation
    _invalid("diff_i_exponent", "d/dx(x^4)", "=4x^4", group="diff_invalid", operation="differentiate", tags=("superscript",)),
    _invalid("diff_i_coefficient", "d/dx(x^5)", "=x^4", group="diff_invalid", operation="differentiate", tags=("superscript",)),
    _invalid("diff_i_constant", "d/dx(x^2+9)", "=2x+9", group="diff_invalid", operation="differentiate", tags=("superscript",)),
    _invalid("diff_i_missing", "d/dx(x^3+4x)", "=3x^2", group="diff_invalid", operation="differentiate", tags=("superscript",)),
    _invalid("diff_i_leibniz", "y=x^3", "dy/dx=3x", group="diff_invalid", operation="differentiate", tags=("superscript",)),
    _invalid("diff_i_function", "f(x)=x^2+x", "f'(x)=2x", group="diff_invalid", operation="differentiate", tags=("superscript",)),
    _invalid("diff_i_chain", "d/dx((2x+1)^3)", "=3(2x+1)^2", group="diff_invalid", operation="differentiate", tags=("superscript", "nested")),
    _invalid("diff_i_sin", "d/dx(sin(2x))", "=cos(2x)", group="diff_invalid", operation="differentiate", tags=("nested",)),
    # Must abstain: outside the domain, or the transition is unclear
    Case("abs_limit", ("lim(x)",), "abstain", "abstain", tags=("unsupported",)),
    Case("abs_inequality", ("2x+1<7",), "abstain", "abstain", tags=("unsupported",)),
    Case("abs_incomplete", ("2(x+3)", "=2x+"), "abstain", "abstain", tags=("incomplete",)),
    Case("abs_substitution", ("2x+3", "=7"), "abstain", "abstain", tags=("unclear_transition",)),
    Case("abs_domain_sqrt", ("sqrt(x^2)", "=x"), "abstain", "abstain", tags=("domain",), draw=({"text": "√(x^2)"}, {"text": "=x"})),
    Case("abs_lost_root", ("x^2=x", "x=1"), "abstain", "abstain", tags=("domain",)),
    # Difficult renderings
    _valid("hard_messy_valid", "(x+1)(x+3)", "=x^2+4x+3", group="hard", operation="expand", style="messy", tags=("poor_handwriting", "superscript")),
    _invalid("hard_messy_invalid", "5(x+2)", "=5x+2", group="hard", operation="expand", style="messy", tags=("poor_handwriting",)),
    _valid("hard_overlap_valid", "3(x+4)+2x", "=5x+12", group="hard", operation="expand", style="overlap", tags=("overlapping",)),
    _invalid("hard_overlap_invalid", "3(x+4)", "=3x+4", group="hard", operation="expand", style="overlap", tags=("overlapping",)),
    _valid("hard_super_valid", "x^2*x^3", "=x^5", group="hard", operation="simplify", tags=("superscript",)),
    _invalid("hard_super_invalid", "x^2*x^3", "=x^6", group="hard", operation="simplify", tags=("superscript",)),
    _valid("hard_fraction_valid", "(x+1)/(x-1)", "=(2x+2)/(2x-2)", group="hard", operation="simplify", tags=("fraction",),
           draw=({"stack": ["x+1", "x-1"]}, {"stack": ["2x+2", "2x-2"], "prefix": "="})),
    _invalid("hard_fraction_invalid", "1/2+1/3", "=2/5", group="hard", operation="arithmetic", tags=("fraction",)),
    _valid("hard_crossed_out", "2(x+3)", "=2x+6", group="hard", operation="expand", tags=("crossed_out",),
           draw=({"text": "2(x+3)"}, {"text": "=2x+3", "strike": True}, {"text": "=2x+6"}), note="The wrong line is struck through and must be ignored."),
    Case("hard_two_errors", ("2(x+3)", "=2x+3", "=5x+3"), "confirmed_error", "hard", error_line=1, operation="expand", tags=("two_errors",)),
]
