"""The five newer paths, played through the backend: each level opens, and its questions are checked as a
student would write them, right and wrong, with a mocked model reading the lines as given.

What this shows: the verifier's verdicts and when a question counts as finished, for these questions. What it
doesn't: how the real model reads handwritten fractions, degree signs or integral signs. Nothing here has been
tried with an Apple Pencil.
"""

from __future__ import annotations

import random
import uuid
from fractions import Fraction
from typing import Any

import pytest
import respx
import sympy as sp
from fastapi.testclient import TestClient

from quack.services.curriculum import catalog, course
from quack.services.lesson_questions import TEMPLATES, is_checkable
from quack.services.lessons import question_columns
from quack.services.starter_questions import STARTER
from tests.accounts_support import register
from tests.test_ipad_qa_nov4 import ByEffort, line
from tests.test_lessons import store_of
from tests.test_pipeline import CHAT, png

NEW_PATHS = ("arithmetic_foundations", "fractions_decimals", "geometry", "trigonometry", "calculus")


@pytest.fixture
def model() -> Any:
    with respx.mock:
        answer = ByEffort()
        respx.post(CHAT).mock(side_effect=answer)
        yield answer


class Path:
    def __init__(self, client: TestClient, course_id: str, level: str, template: str, params: dict[str, Any]) -> None:
        self.client, self.course_id, self.level = client, course_id, level
        self.headers = register(client, f"{uuid.uuid4().hex[:12]}@example.com")
        self.user = client.get("/api/profile", headers=self.headers).json()["userId"]
        found = course(course_id)
        assert found is not None
        for earlier in found.levels:
            if earlier.id == level:
                break
            store_of(client).mark_level(self.user, course_id, earlier.id, "moved_on_at")
        response = client.post(f"/api/learn/courses/{course_id}/levels/{level}/start", headers=self.headers)
        assert response.status_code == 200, response.text
        self.state = response.json()
        question = TEMPLATES[template].build(random.Random(0), params)
        columns = question_columns(question)
        assignments = ", ".join(f"{name} = ?" for name in columns)
        store_of(client).database.execute(
            f"UPDATE practice_problems SET {assignments} WHERE user_id = ? AND id = ?",
            (*columns.values(), self.user, self.state["question"]["id"]),
        )
        self.version = 0

    @property
    def row(self) -> dict[str, Any]:
        return store_of(self.client).problem(self.user, self.state["question"]["id"])

    def write(self, model: ByEffort, *maths: str) -> dict[str, Any]:
        model.read([line(math) for math in maths])
        self.version += 1
        question = self.state["question"]
        response = self.client.post(
            f"/api/practice/problems/{question['id']}/check",
            json={
                "sessionId": question["checkSessionId"],
                "snapshotVersion": self.version,
                "image": png(size=(30 + self.version, 20)),
                "acknowledgedMistakeIds": [],
                "inkLines": [{"id": f"l{self.version}-{i}", "inImage": True} for i in range(len(maths))],
                "studySessionId": self.state["sessionId"],
                "trigger": "manual",
            },
            headers=self.headers,
        )
        assert response.status_code == 200, response.text
        return response.json()


# (course, level, template, params, prompt shown, right working, wrong working or None)
CASES = [
    (
        "arithmetic_foundations",
        "arithmetic_add_subtract",
        "arithmetic.add",
        {"a": 17, "b": 26},
        "Work out 17 + 26",
        ["17 + 26", "= 43"],
        ["= 33"],
    ),
    (
        "arithmetic_foundations",
        "arithmetic_add_subtract",
        "arithmetic.subtract",
        {"a": 52, "b": 19},
        "Work out 52 − 19",
        ["= 33"],
        ["= 47"],
    ),
    (
        "arithmetic_foundations",
        "arithmetic_multiply_divide",
        "arithmetic.multiply",
        {"a": 7, "b": 8},
        "Work out 7 × 8",
        ["56"],
        ["54"],
    ),
    (
        "arithmetic_foundations",
        "arithmetic_multiply_divide",
        "arithmetic.divide",
        {"a": 6, "b": 8},
        "Work out 48 ÷ 6",
        ["8"],
        ["7"],
    ),
    (
        "arithmetic_foundations",
        "arithmetic_order_of_operations",
        "arithmetic.multiply_first",
        {"a": 3, "b": 4, "c": 5},
        "Work out 3 + 4 × 5",
        ["3 + 20", "= 23"],
        ["= 35"],
    ),
    (
        "arithmetic_foundations",
        "arithmetic_order_of_operations",
        "arithmetic.brackets_first",
        {"a": 12, "b": 4, "c": 3},
        "Work out (12 − 4) × 3",
        ["8 × 3", "= 24"],
        ["= 0"],
    ),
    (
        "fractions_decimals",
        "fractions_equivalent",
        "fractions.simplify",
        {"p": 2, "q": 3, "k": 3},
        "Simplify 6/9",
        ["2/3"],
        ["3/4"],
    ),
    (
        "fractions_decimals",
        "fractions_operations",
        "fractions.add_different_bottoms",
        {"bottoms": (2, 3), "a": 1, "c": 1},
        "Work out 1/2 + 1/3",
        ["3/6 + 2/6", "= 5/6"],
        ["= 2/5"],
    ),
    (
        "fractions_decimals",
        "fractions_operations",
        "fractions.multiply",
        {"first": (3, 4), "second": (2, 3)},
        "Work out 3/4 × 2/3",
        ["6/12", "= 1/2"],
        ["= 5/7"],
    ),
    (
        "fractions_decimals",
        "fractions_decimals",
        "fractions.to_decimal",
        {"p": 1, "q": 4},
        "Write as a decimal: 1/4",
        ["0.25"],
        ["0.4"],
    ),
    (
        "fractions_decimals",
        "fractions_decimals",
        "fractions.to_fraction",
        {"p": 3, "q": 4},
        "Write as a fraction in its lowest terms: 0.75",
        ["75/100", "= 3/4"],
        ["= 3/5"],
    ),
    (
        "geometry",
        "geometry_perimeter",
        "geometry.rectangle_perimeter",
        {"l": 8, "w": 5},
        "A rectangle is 8 cm long and 5 cm wide.",
        ["8 + 5 + 8 + 5", "= 26"],
        ["8 × 5", "= 40"],
    ),
    (
        "geometry",
        "geometry_area",
        "geometry.rectangle_area",
        {"l": 7, "w": 4},
        "A rectangle is 7 cm long and 4 cm wide.",
        ["7 × 4", "= 28"],
        ["= 22"],
    ),
    (
        "geometry",
        "geometry_area",
        "geometry.triangle_area",
        {"b": 10, "h": 6},
        "A triangle has a base of 10 cm",
        ["1/2 × 10 × 6", "= 30"],
        ["10 × 6", "= 60"],
    ),
    (
        "trigonometry",
        "trigonometry_ratios",
        "trigonometry.sin_ratio",
        {"triple": (6, 8, 10), "ratio": "sin"},
        "Write sin A as a fraction",
        ["6/10", "= 3/5"],
        ["= 4/5"],
    ),
    (
        "trigonometry",
        "trigonometry_exact_values",
        "trigonometry.exact_value",
        {"at": ("cos", 60), "k": 5},
        "Work out 5 cos 60°",
        ["5 × 1/2", "= 5/2"],
        None,
    ),
    (
        "trigonometry",
        "trigonometry_exact_values",
        "trigonometry.exact_value",
        {"at": ("sin", 30), "k": 4},
        "Work out 4 sin 30°",
        ["4 sin 30°", "= 4 × 1/2", "= 2"],
        None,
    ),
    (
        "trigonometry",
        "trigonometry_challenge",
        "trigonometry.side_from_angle",
        {"h": 10, "at": ("sin", 30)},
        "hypotenuse of 10 cm",
        ["10 × sin(30°)", "= 10 × 1/2", "= 5"],
        None,
    ),
    (
        "calculus",
        "calculus_power_rule",
        "calculus.differentiate_power",
        {"n": 3},
        "Differentiate: d/dx(x³)",
        ["3x^2"],
        ["3x^2 + C"],
    ),
    (
        "calculus",
        "calculus_power_rule",
        "calculus.differentiate_term",
        {"a": 4, "n": 2},
        "Differentiate: d/dx(4x²)",
        ["8x"],
        ["4x"],
    ),
    (
        "calculus",
        "calculus_integration",
        "calculus.integrate_power",
        {"n": 1},
        "Integrate: ∫ 2x dx",
        ["x^2 + C"],
        ["2x^2 + C"],
    ),
    (
        "calculus",
        "calculus_challenge",
        "calculus.differentiate_sum",
        {"a": 2, "b": 5, "c": 4},
        "Differentiate: d/dx(2x³ + 5x + 4)",
        ["6x^2 + 5"],
        ["6x^2 + 5x"],
    ),
]


def ids(case: tuple[Any, ...]) -> str:
    return f"{case[2]}-{case[3]}"


class TestEveryPathIsOpen:
    def test_all_six_are_available_and_load(self) -> None:
        paths = {entry.id: entry for entry in catalog().paths}
        assert set(paths) == {*NEW_PATHS, "algebra_foundations"}
        for path_id in paths:
            assert paths[path_id].status == "available"
            found = course(path_id)
            assert found is not None and found.levels[-1].kind == "challenge"

    @pytest.mark.parametrize("path_id", NEW_PATHS)
    def test_every_level_opens(self, accounts_client: TestClient, path_id: str) -> None:
        headers = register(accounts_client, f"open-{path_id}@example.com".replace("_", "-"))
        found = course(path_id)
        assert found is not None
        view = accounts_client.get(f"/api/learn/courses/{path_id}", headers=headers).json()
        assert view["levels"][0]["status"] == "available"
        assert all(level["status"] == "locked" for level in view["levels"][1:])
        started = accounts_client.post(f"/api/learn/courses/{path_id}/levels/{found.levels[0].id}/start", headers=headers)
        assert started.status_code == 200, started.text
        assert started.json()["question"]["prompt"]


class TestRightAndWrong:
    @pytest.mark.parametrize("case", CASES, ids=ids)
    def test_the_right_working_finishes_it(
        self, accounts_client: TestClient, model: ByEffort, case: tuple[Any, ...]
    ) -> None:
        course_id, level, template, params, prompt, right, _ = case
        path = Path(accounts_client, course_id, level, template, params)
        assert prompt in path.row["prompt_display"]
        reply = path.write(model, *right)
        assert reply["mistakes"] == [], reply["mistakes"]
        assert reply["outcome"] == "correct", reply
        assert path.row["status"] == "correct"

    @pytest.mark.parametrize("case", [case for case in CASES if case[6] is not None], ids=ids)
    def test_a_wrong_answer_is_a_mistake_and_never_accepted(
        self, accounts_client: TestClient, model: ByEffort, case: tuple[Any, ...]
    ) -> None:
        course_id, level, template, params, _, right, wrong = case
        path = Path(accounts_client, course_id, level, template, params)
        working = right[:-1] + wrong if right[-1].startswith("=") and wrong[0].startswith("=") else wrong
        reply = path.write(model, *working)
        assert reply["outcome"] == "incorrect", reply
        assert reply["mistakes"]
        assert path.row["status"] != "correct"


class TestTheFormMatters:
    def test_a_fraction_not_in_its_lowest_terms_does_not_finish_simplify(
        self, accounts_client: TestClient, model: ByEffort
    ) -> None:
        path = Path(
            accounts_client, "fractions_decimals", "fractions_equivalent", "fractions.simplify", {"p": 2, "q": 3, "k": 3}
        )
        for working in (["6/9"], ["4/6"], ["0.6666"]):
            reply = path.write(model, *working)
            assert reply["outcome"] != "correct", working
        assert path.write(model, "2/3")["outcome"] == "correct"

    def test_a_fraction_is_not_a_decimal(self, accounts_client: TestClient, model: ByEffort) -> None:
        path = Path(accounts_client, "fractions_decimals", "fractions_decimals", "fractions.to_decimal", {"p": 1, "q": 4})
        assert path.write(model, "1/4")["outcome"] != "correct"
        assert path.write(model, "25/100")["outcome"] != "correct"

    def test_a_decimal_is_not_a_fraction(self, accounts_client: TestClient, model: ByEffort) -> None:
        path = Path(accounts_client, "fractions_decimals", "fractions_decimals", "fractions.to_fraction", {"p": 3, "q": 4})
        assert path.write(model, "0.75")["outcome"] != "correct"
        assert path.write(model, "75/100")["outcome"] != "correct"

    def test_an_integral_needs_its_constant(self, accounts_client: TestClient, model: ByEffort) -> None:
        path = Path(accounts_client, "calculus", "calculus_integration", "calculus.integrate_power", {"n": 1})
        assert path.write(model, "x^2")["outcome"] != "correct"

    def test_a_derivative_gets_no_constant(self, accounts_client: TestClient, model: ByEffort) -> None:
        path = Path(accounts_client, "calculus", "calculus_power_rule", "calculus.differentiate_power", {"n": 3})
        reply = path.write(model, "3x^2 + C")
        assert reply["outcome"] == "incorrect"
        assert reply["mistakes"][0]["errorType"] == "constant_added_to_derivative"


class TestTrigonometryIsHonestAboutUnits:
    def test_a_wrong_exact_value_is_never_accepted(self, accounts_client: TestClient, model: ByEffort) -> None:
        # In degrees sin 30 is 1/2; nothing on the page says degrees, so a wrong value is unsure rather than wrong,
        # and it never finishes the question.
        path = Path(
            accounts_client,
            "trigonometry",
            "trigonometry_exact_values",
            "trigonometry.exact_value",
            {"at": ("sin", 30), "k": 4},
        )
        reply = path.write(model, "4 × 0.87")
        assert reply["outcome"] != "correct"
        assert path.row["status"] != "correct"


class TestTheGenerators:
    @pytest.mark.parametrize("template_id", sorted(STARTER))
    def test_answers_are_worked_out_right(self, template_id: str) -> None:
        from quack.services.math_parser import parse_expression, to_sympy

        for seed in range(30):
            question = STARTER[template_id].build(random.Random(seed), None).question
            math, expected = question.math, question.expected
            # Quack's own strings, built from integers, so the parser may read them.
            if math.startswith("∫"):
                integrand, answer = math.removeprefix("∫").removesuffix("dx"), expected.removesuffix("+ C")
                antiderivative = to_sympy(parse_expression(answer))
                x = next(iter(antiderivative.free_symbols))
                assert sp.expand(sp.diff(antiderivative, x) - to_sympy(parse_expression(integrand))) == 0, math
            else:
                worked = to_sympy(parse_expression(math)).replace(
                    lambda part: isinstance(part, (sp.sin, sp.cos, sp.tan)),
                    lambda part: part.func(part.args[0] * sp.pi / 180),  # the paths' trigonometry is in degrees
                )
                assert sp.simplify(worked - to_sympy(parse_expression(expected))) == 0, (math, expected)

    def test_no_division_by_zero_and_no_negative_lengths(self) -> None:
        for template_id, template in STARTER.items():
            for seed in range(40):
                question = template.build(random.Random(seed), None).question
                assert "/0" not in question.math.replace(" ", ""), template_id
                if template_id.startswith("geometry."):
                    assert Fraction(question.expected) > 0

    @pytest.mark.parametrize("template_id", sorted(STARTER))
    async def test_the_verifier_accepts_every_answer_key(self, template_id: str) -> None:
        for seed in range(6):
            assert await is_checkable(STARTER[template_id].build(random.Random(seed), None), 10), (template_id, seed)

    def test_questions_vary(self) -> None:
        for template_id, template in STARTER.items():
            asked = {template.build(random.Random(seed), None).question.math for seed in range(30)}
            assert len(asked) >= 4, template_id


class TestLabelsAndUnits:
    """`P = 26` and `sin A = 6/10` were called wrong before: the letters were read as unknowns."""

    @pytest.mark.parametrize("working", [["26 cm"], ["P = 26"], ["Perimeter = 26"], ["P = 2(8 + 5)", "= 26 cm"], ["26cm"]])
    def test_a_perimeter_with_a_label_or_a_unit(
        self, accounts_client: TestClient, model: ByEffort, working: list[str]
    ) -> None:
        path = Path(accounts_client, "geometry", "geometry_perimeter", "geometry.rectangle_perimeter", {"l": 8, "w": 5})
        reply = path.write(model, *working)
        assert reply["mistakes"] == [] and reply["outcome"] == "correct", reply

    @pytest.mark.parametrize("working", [["P = 40"], ["40 cm"]])
    def test_a_labelled_wrong_answer_is_still_wrong(
        self, accounts_client: TestClient, model: ByEffort, working: list[str]
    ) -> None:
        path = Path(accounts_client, "geometry", "geometry_perimeter", "geometry.rectangle_perimeter", {"l": 8, "w": 5})
        assert path.write(model, *working)["outcome"] == "incorrect"

    @pytest.mark.parametrize("working", [["sin A = 6/10", "= 3/5"], ["sinA = 3/5"]])
    def test_a_ratio_with_its_name(self, accounts_client: TestClient, model: ByEffort, working: list[str]) -> None:
        path = Path(
            accounts_client,
            "trigonometry",
            "trigonometry_ratios",
            "trigonometry.sin_ratio",
            {"triple": (6, 8, 10), "ratio": "sin"},
        )
        assert path.write(model, *working)["outcome"] == "correct"

    def test_a_side_with_its_name(self, accounts_client: TestClient, model: ByEffort) -> None:
        path = Path(
            accounts_client,
            "trigonometry",
            "trigonometry_challenge",
            "trigonometry.side_from_angle",
            {"h": 10, "at": ("sin", 30)},
        )
        assert path.write(model, "opp = 10 × sin 30", "= 5")["outcome"] == "correct"

    def test_only_questions_with_no_letters(self) -> None:
        from quack.services.practice import is_numeric_question

        assert is_numeric_question("2(8 + 5)") and is_numeric_question("10 * sin(30)") and is_numeric_question("3/4")
        for question in ("3m + 2m", "d/dx(x^3)", "∫ 2x dx", "2x + 3 = 7", "x^2 + 3x"):
            assert not is_numeric_question(question)

    def test_algebra_lines_are_left_alone(self) -> None:
        from quack.services.handwriting import HandwritingReading, ReadLine
        from quack.services.practice import answer_lines

        page = HandwritingReading(
            lines=[
                ReadLine(
                    raw_text="5m",
                    math="5m",
                    kind="math",
                    legibility="clear",
                    alternatives=[],
                    continues_previous="unknown",
                    operation="other",
                    y_top=0,
                    y_bottom=1,
                )
            ]
        )
        assert answer_lines("3m + 2m")(page).lines[0].math == "3m + 2m = 5m"
