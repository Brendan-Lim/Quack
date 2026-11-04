"""Elementary antiderivatives, worked out without asking a model and checked by differentiating.

Only a table of shapes gets as far as SymPy's rule-based integrator, as its general integrator can
take far longer than a check is allowed. Whatever comes back is differentiated, and if that doesn't
give the integrand it isn't used, so a bug in a rule can only ever make Quack abstain.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

import sympy as sp
from sympy.integrals.manualintegrate import manualintegrate

MAX_INTEGRAND_OPS = 40
# One slow integral shouldn't use up what the rest of the page's checks are allowed.
MAX_INTEGRATION_S = 1.0
_ALLOWED_FUNCTIONS = (sp.sin, sp.cos, sp.tan, sp.exp, sp.log, sp.Abs)


@contextmanager
def _time_limit(seconds: float) -> Iterator[None]:
    """SymPy can't be asked to stop. A signal can stop it, in the main thread of a verifier process."""

    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def give_up(_signal: int, _frame: object) -> None:
        raise TimeoutError

    previous = signal.signal(signal.SIGALRM, give_up)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class CannotIntegrate(ValueError):
    """Outside what Quack checks, or not a value it can stand behind. The reason is safe to log."""


def _is_linear_in(expr: sp.Expr, variable: sp.Symbol) -> bool:
    try:
        return sp.Poly(expr, variable).degree() <= 1
    except sp.PolynomialError:
        return False


def _is_tabulated(integrand: sp.Expr, variable: sp.Symbol) -> bool:
    """Sums and products of powers and the usual functions, of something linear in the variable."""

    if sp.count_ops(integrand) > MAX_INTEGRAND_OPS:
        return False
    for function in integrand.atoms(sp.Function):
        if not isinstance(function, _ALLOWED_FUNCTIONS) or not _is_linear_in(function.args[0], variable):
            return False
    for power in integrand.atoms(sp.Pow):
        if not power.has(variable):
            continue
        base_is_fixed = not power.base.has(variable)
        exponent_is_fixed = not power.exp.has(variable)
        if not (base_is_fixed and _is_linear_in(power.exp, variable)) and not (
            exponent_is_fixed and _is_linear_in(power.base, variable)
        ):
            return False
    return True


def _with_absolute_values(result: sp.Expr, integrand: sp.Expr) -> sp.Expr:
    """The integral of 1/x is ln|x|, and the integrator's ln(x) only says so for positive x."""

    if integrand.has(sp.log):
        return result
    return result.replace(lambda node: isinstance(node, sp.log), lambda node: sp.log(sp.Abs(node.args[0])))


@lru_cache(maxsize=64)
def antiderivative(integrand: sp.Expr, variable: sp.Symbol) -> sp.Expr | None:
    """One antiderivative, with no constant, or None if Quack won't vouch for one."""

    if not integrand.has(variable):
        return integrand * variable
    try:
        return sp.Poly(integrand, variable).integrate(variable).as_expr()
    except sp.PolynomialError:
        pass
    if not _is_tabulated(integrand, variable):
        return None
    try:
        with _time_limit(MAX_INTEGRATION_S):
            result = manualintegrate(integrand, variable)
            if result.has(sp.Integral, sp.Piecewise, sp.Si, sp.Ci, sp.erf, sp.Ei):
                return None
            if sp.simplify(sp.diff(result, variable) - integrand) != 0:
                return None
    except (TypeError, ValueError, NotImplementedError, AttributeError, ArithmeticError, RecursionError, TimeoutError):
        return None
    return _with_absolute_values(result, integrand)


def _refuse_a_break_in_the_interval(integrand: sp.Expr, variable: sp.Symbol, low: sp.Expr, high: sp.Expr) -> None:
    """The fundamental theorem needs a continuous integrand: ∫ from -1 to 1 of 1/x isn't 0."""

    if integrand.has(sp.tan):
        raise CannotIntegrate("tan may be undefined inside the interval")
    denominator = sp.fraction(sp.together(integrand))[1]
    if denominator.has(variable):
        try:
            roots = sp.roots(sp.Poly(denominator, variable))
        except sp.PolynomialError as exc:
            raise CannotIntegrate("a denominator Quack can't solve") from exc
        if sum(roots.values()) != sp.Poly(denominator, variable).degree():
            raise CannotIntegrate("a denominator Quack can't solve")
        if any(root.is_real is not False and low <= root <= high for root in roots):
            raise CannotIntegrate("the integrand is undefined inside the interval")
    # Each is linear, so what it is at the two ends says what it is everywhere between.
    for inner in [node.args[0] for node in integrand.atoms(sp.log)] + [
        power.base for power in integrand.atoms(sp.Pow) if not power.exp.is_integer and power.base.has(variable)
    ]:
        if any(inner.subs(variable, end) < 0 for end in (low, high)):
            raise CannotIntegrate("the integrand isn't real across the interval")


def definite_value(integrand: sp.Expr, variable: sp.Symbol, lower: sp.Expr, upper: sp.Expr) -> sp.Expr:
    """The exact value of a definite integral, by the fundamental theorem. Raises `CannotIntegrate`."""

    if lower.has(variable) or upper.has(variable):
        raise CannotIntegrate("a limit that depends on the variable of integration")
    is_polynomial = _is_polynomial(integrand, variable)
    if lower.is_number and upper.is_number:
        if lower.is_real is not True or upper.is_real is not True:
            raise CannotIntegrate("limits that aren't real numbers")
        if not is_polynomial:
            low, high = (lower, upper) if lower <= upper else (upper, lower)
            try:
                _refuse_a_break_in_the_interval(integrand, variable, low, high)
            except TypeError as exc:
                # A root SymPy can't place on the number line, so nobody can say it's outside.
                raise CannotIntegrate("a denominator Quack can't solve") from exc
    elif not is_polynomial:
        raise CannotIntegrate("letters as limits, on something that may not be continuous")
    found = antiderivative(integrand, variable)
    if found is None:
        raise CannotIntegrate("no antiderivative Quack can check")
    value = found.subs(variable, upper) - found.subs(variable, lower)
    if value.has(sp.nan, sp.zoo, sp.oo, -sp.oo):
        raise CannotIntegrate("the value isn't finite")
    return sp.nsimplify(value) if value.is_Float else value


def _is_polynomial(expr: sp.Expr, variable: sp.Symbol) -> bool:
    try:
        sp.Poly(expr, variable)
    except sp.PolynomialError:
        return False
    return True
