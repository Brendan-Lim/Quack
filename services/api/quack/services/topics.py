"""What a verified mistake was about, for grouping and for practice.

Only says what the verifier's own labels say. A mistake it couldn't classify stays unclassified, and
doesn't get a topic it merely might have been about.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Topic:
    id: str
    label: str
    group: str
    # The practice this leads to, if Quack has questions it can check. None means it doesn't.
    practice: str | None = None


ARITHMETIC, ALGEBRA, CALCULUS, OTHER = "Arithmetic", "Algebra", "Calculus", "Other"

TOPICS = {
    topic.id: topic
    for topic in (
        Topic("multiplication", "Multiplication", ARITHMETIC, "multiplication"),
        Topic("division", "Division and fractions", ARITHMETIC, "division"),
        Topic("addition_subtraction", "Adding and subtracting", ARITHMETIC, "addition_subtraction"),
        Topic("powers", "Powers", ARITHMETIC),
        Topic("rounding", "Rounding", ARITHMETIC),
        Topic("arithmetic", "Arithmetic", ARITHMETIC),
        Topic("expanding_brackets", "Expanding brackets", ALGEBRA, "expanding_brackets"),
        Topic("combining_like_terms", "Combining like terms", ALGEBRA, "combining_like_terms"),
        Topic("factorising", "Factorising", ALGEBRA),
        Topic("algebra_fractions", "Algebraic fractions", ALGEBRA),
        Topic("exponent_rules", "Exponent rules", ALGEBRA),
        Topic("algebra_signs", "Signs in algebra", ALGEBRA),
        Topic("solving_equations", "Solving equations", ALGEBRA, "solving_equations"),
        Topic("algebra", "Algebra", ALGEBRA),
        Topic("power_rule", "Power rule", CALCULUS, "power_rule"),
        Topic("sum_and_constant_rules", "Sum and constant rules", CALCULUS, "sum_and_constant_rules"),
        Topic("standard_derivatives", "Derivatives of sin, cos and other functions", CALCULUS),
        Topic("chain_rule", "Chain rule", CALCULUS),
        Topic("product_rule", "Product rule", CALCULUS),
        Topic("quotient_rule", "Quotient rule", CALCULUS),
        Topic("integration", "Integration", CALCULUS, "integration"),
        Topic("definite_integrals", "Definite integrals", CALCULUS, "integration"),
        # Differentiation that went wrong in a way it can't name. Practice starts from the basic rule,
        # which doesn't claim that was the trouble.
        Topic("differentiation", "Differentiation", CALCULUS, "power_rule"),
        Topic("unclassified", "Other mistakes", OTHER),
    )
}

_ARITHMETIC_BY_OPERATION = {
    "multiplication": "multiplication",
    "division": "division",
    "addition": "addition_subtraction",
    "subtraction": "addition_subtraction",
    "power": "powers",
    "rounding": "rounding",
}
_DERIVATIVE_BY_ERROR = {
    "power_rule_coefficient": "power_rule",
    "power_rule_exponent": "power_rule",
    "derivative_sign": "sum_and_constant_rules",
    "constant_not_dropped": "sum_and_constant_rules",
    "missing_term": "sum_and_constant_rules",
    "derivative_of_sine": "standard_derivatives",
    "derivative_of_cosine": "standard_derivatives",
    "derivative_of_tangent": "standard_derivatives",
    "derivative_of_exponential": "standard_derivatives",
    "derivative_of_logarithm": "standard_derivatives",
    "chain_rule_inner_missing": "chain_rule",
    "product_rule_not_applied": "product_rule",
    "quotient_rule_not_applied": "quotient_rule",
}
_BY_CATEGORY = {
    "algebra_expansion": "expanding_brackets",
    "algebra_like_terms": "combining_like_terms",
    "algebra_factorisation": "factorising",
    "algebra_fractions": "algebra_fractions",
    "algebra_exponents": "exponent_rules",
    "algebra_sign": "algebra_signs",
    "equation_solving": "solving_equations",
    "algebra": "algebra",
    # Putting a question's values in for its letters, which only lessons ask for.
    "substitution": "algebra",
}


def topic_of(category: str, error_type: str, parsed_operation: str) -> str:
    if category == "arithmetic":
        if error_type == "rounding_error":
            return "rounding"
        return _ARITHMETIC_BY_OPERATION.get(parsed_operation, "arithmetic")
    if category == "differentiation":
        return _DERIVATIVE_BY_ERROR.get(error_type, "differentiation")
    if category == "integration":
        return "definite_integrals" if error_type == "incorrect_definite_integral" else "integration"
    return _BY_CATEGORY.get(category, "unclassified")


def topic_named(topic_id: str | None) -> Topic:
    return TOPICS.get(topic_id or "", TOPICS["unclassified"])
