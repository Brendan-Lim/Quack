"""Questions from templates, checked by the real verifier. Meta's API is mocked."""

from __future__ import annotations

import json
import random
from typing import Any

import httpx
import pytest
import respx
import sympy as sp
from fastapi.testclient import TestClient

from quack.services.handwriting import HandwritingReading, ReadLine
from quack.services.math_parser import parse_expression, parse_statement, to_sympy
from quack.services.math_verifier import LineInput, completes_answer, verify_lines
from quack.services.practice import EQUATION, SUPPORTED, answer_lines, generate_question
from tests.accounts_support import register
from tests.test_line_memory import compact
from tests.test_pipeline import CHAT, completion, png

SEEDS = range(40)
DIFFICULTIES = ("basic", "intermediate")


def every_question() -> list[tuple[str, str, int]]:
    return [(topic, difficulty, seed) for topic in SUPPORTED for difficulty in DIFFICULTIES for seed in SEEDS]


def independently(question: Any) -> sp.Expr:
    """Work out the answer a different way from how the template did."""

    math = question.math
    if math.startswith("d/dx("):
        inner = to_sympy(parse_expression(math[5:-1]))
        return sp.diff(inner, next(iter(inner.free_symbols)))
    return to_sympy(parse_expression(math))


def wrong_answer(expected: str) -> str:
    """A + C can soak up any constant, and an equation's answer has to stay an answer."""

    if expected.startswith("x ="):
        return f"x = {int(expected[3:]) + 1}"
    if any(function in expected for function in ("sin(", "cos(", "exp(")):
        # A constant offset can disappear relative to a large exponential at sample points.
        # A scaling error stays distinct; the verifier's conservative comparison is unchanged.
        return f"2*({expected})"
    return f"{expected} + x" if "C" in expected else f"{expected} + 1"


def finished_line(question: Any) -> str:
    return question.expected if question.method == EQUATION else f"{question.math} = {question.expected}"


def lines_for(question: Any, text: str) -> list[LineInput]:
    if question.method == EQUATION:
        return [LineInput(0, text, continues="yes", intent="solve", given=question.math)]
    return [LineInput(0, text, posed=True)]


class TestTemplates:
    @pytest.mark.parametrize(("topic", "difficulty", "seed"), every_question()[::7])
    def test_every_answer_is_right_and_the_verifier_agrees(self, topic: str, difficulty: str, seed: int) -> None:
        question = generate_question(topic, difficulty, random.Random(seed))
        x = sp.Symbol("x", real=True)
        if question.method == EQUATION:
            equation = parse_statement(question.math)
            solved = sp.solve(to_sympy(equation.parts[0]) - to_sympy(equation.parts[1]), x)
            assert solved == [sp.Integer(question.expected.split("=")[1])]
        elif question.method == "integration":
            answer = to_sympy(parse_expression(question.expected))
            integrand = to_sympy(parse_expression(question.math).args[0])
            assert sp.simplify(sp.diff(answer, x) - integrand) == 0
        else:
            assert sp.simplify(independently(question) - to_sympy(parse_expression(question.expected))) == 0
        statement = finished_line(question)
        assert verify_lines(lines_for(question, statement)).status == "valid", statement
        assert completes_answer(question.math, question.expected, statement), statement

    @pytest.mark.parametrize(("topic", "difficulty", "seed"), every_question()[::11])
    def test_a_wrong_answer_is_caught(self, topic: str, difficulty: str, seed: int) -> None:
        question = generate_question(topic, difficulty, random.Random(seed))
        wrong = wrong_answer(question.expected)
        statement = wrong if question.method == EQUATION else f"{question.math} = {wrong}"
        report = verify_lines(lines_for(question, statement))
        assert report.status == "invalid", question.math
        assert not completes_answer(question.math, question.expected, statement)

    def test_the_difficulties_are_what_the_brief_describes(self) -> None:
        rng = random.Random(1)
        assert generate_question("power_rule", "basic", rng).display.startswith("d/dx(x")
        assert "+" in generate_question("sum_and_constant_rules", "intermediate", rng).math

    def test_shown_as_a_student_would_write_it(self) -> None:
        question = generate_question("sum_and_constant_rules", "basic", random.Random(3))
        assert "²" in question.display and question.display.endswith("= ?") and "^" not in question.display
        assert "×" in generate_question("multiplication", "basic", random.Random(3)).display


class TestWhatCountsAsFinished:
    @pytest.mark.parametrize(
        ("question", "expected", "line"),
        [
            ("7 * 4", "28", "7 * 4 = 28"),
            ("7 * 4", "28", "= 28"),
            ("3 ÷ 4", "3/4", "3 ÷ 4 = 3/4"),
            ("3 ÷ 4", "3/4", "3 ÷ 4 = 0.75"),
            ("3(x + 5)", "3x + 15", "3(x + 5) = 3x + 15"),
            ("3(x + 5)", "3x + 15", "= 15 + 3x"),
            ("4x + 3x + 2", "7x + 2", "= 7x + 2"),
            ("d/dx(5x^3 + 2x)", "15x^2 + 2", "d/dx(5x^3 + 2x) = 15x^2 + 2"),
        ],
    )
    def test_finished(self, question: str, expected: str, line: str) -> None:
        assert completes_answer(question, expected, line)

    @pytest.mark.parametrize(
        ("question", "expected", "line"),
        [
            ("7 * 4", "28", "7 * 4 = 7 * 4"),
            ("7 * 4", "28", "7 * 4 = 14 + 14"),
            ("7 * 4", "28", "7 * 4 = 27"),
            ("3(x + 5)", "3x + 15", "3(x + 5) = 3(x + 5)"),
            ("3(x + 5)", "3x + 15", "3(x + 5) = (x + 5)3"),
            ("4x + 3x + 2", "7x + 2", "4x + 3x + 2 = 4x + 3x + 2"),
            ("7 * 4", "28", "not maths ("),
        ],
    )
    def test_not_finished(self, question: str, expected: str, line: str) -> None:
        assert not completes_answer(question, expected, line)


def read(*maths: str) -> HandwritingReading:
    return HandwritingReading(
        lines=[
            ReadLine(
                raw_text=math, math=math, kind="math", legibility="clear", alternatives=[],
                continues_previous="unknown", operation="other", y_top=0, y_bottom=0,
            )
            for math in maths
        ]
    )


class TestPuttingTheQuestionInFront:
    def test_a_bare_answer_becomes_a_statement(self) -> None:
        assert answer_lines("7 * 4")(read("27")).lines[0].math == "7 * 4 = 27"

    def test_a_copied_question_is_left_as_it_is(self) -> None:
        assert answer_lines("7 * 4")(read("7 * 4 = 27")).lines[0].math == "7 * 4 = 27"

    def test_working_stays_as_one_piece(self) -> None:
        lines = answer_lines("3(x + 5)")(read("3x + 15", "15 + 3x")).lines
        assert [line.math for line in lines] == ["3(x + 5) = 3x + 15", "= 15 + 3x"]
        assert [line.continues_previous for line in lines] == ["no", "yes"]

    def test_other_readings_of_the_line_get_it_too(self) -> None:
        reading = read("27")
        reading.lines[0].alternatives = ["28"]
        assert answer_lines("7 * 4")(reading).lines[0].alternatives == ["7 * 4 = 28"]

    def test_words_are_left_alone(self) -> None:
        reading = HandwritingReading(lines=[read("hello").lines[0].model_copy(update={"kind": "prose", "math": ""})])
        assert answer_lines("7 * 4")(reading).lines[0].math == ""


class Model:
    def __init__(self) -> None:
        self.reply: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        if body["response_format"]["json_schema"]["name"] == "CompactReading":
            return completion({"lines": self.reply})
        return completion({"level": 1, "text": "Have another look at that step.", "references_step": "x"})


@pytest.fixture
def model() -> Any:
    with respx.mock:
        answer = Model()
        respx.post(CHAT).mock(side_effect=answer)
        yield answer


class Practice:
    def __init__(self, client: TestClient, topic: str = "multiplication") -> None:
        self.client = client
        self.headers = register(client)
        self.problem = client.post("/api/practice/sessions", json={"topic": topic}, headers=self.headers).json()
        self.version = 0

    @property
    def row(self) -> dict[str, Any]:
        store = self.client.app.state.services.accounts.store
        user = self.client.get("/api/profile", headers=self.headers).json()["userId"]
        return store.problem(user, self.problem["id"])

    def answer(self, model: Model, *maths: str, acknowledged: list[str] | None = None) -> dict[str, Any]:
        self.version += 1
        model.reply = [compact(math) for math in maths]
        response = self.client.post(
            f"/api/practice/problems/{self.problem['id']}/check",
            json={
                "sessionId": self.problem["checkSessionId"],
                "snapshotVersion": self.version,
                "image": png(),
                "acknowledgedMistakeIds": acknowledged or [],
                "inkLines": [{"id": f"line-{self.version}-{i}", "inImage": True} for i in range(len(maths))],
            },
            headers=self.headers,
        )
        assert response.status_code == 200, response.text
        return response.json()

    def summary(self) -> dict[str, Any]:
        return self.client.post(f"/api/practice/sessions/{self.problem['sessionId']}/finish", headers=self.headers).json()


class TestAnswering:
    def test_a_right_answer_finishes_it_with_no_alert(self, accounts_client: TestClient, model: Model) -> None:
        practice = Practice(accounts_client)
        reply = practice.answer(model, practice.row["expected_answer"])
        assert reply["outcome"] == "correct"
        assert reply["mistakes"] == []
        assert practice.row["status"] == "correct"

    def test_a_wrong_answer_gets_the_usual_alert_and_hints(self, accounts_client: TestClient, model: Model) -> None:
        practice = Practice(accounts_client)
        wrong = str(int(practice.row["expected_answer"]) + 1)
        reply = practice.answer(model, wrong)
        assert reply["outcome"] == "incorrect"
        assert reply["intervention"]["shouldQuack"] is True
        mistake = reply["mistakes"][0]
        hint = accounts_client.post(
            "/api/hint",
            json={
                "sessionId": practice.problem["checkSessionId"],
                "mistakeId": mistake["id"],
                "level": 1,
                "studySessionId": practice.problem["sessionId"],
            },
            headers=practice.headers,
        )
        assert hint.status_code == 200
        assert practice.row["expected_answer"] not in hint.json()["text"]

    def test_fixing_it_counts_once_fixed(self, accounts_client: TestClient, model: Model) -> None:
        practice = Practice(accounts_client)
        expected = practice.row["expected_answer"]
        mistake = practice.answer(model, str(int(expected) + 1))["mistakes"][0]["id"]
        reply = practice.answer(model, expected, acknowledged=[mistake])
        assert reply["resolvedMistakeIds"] == [mistake]
        assert reply["outcome"] == "correct"

    def test_two_unsure_readings_dont_finish_it(self, accounts_client: TestClient, model: Model) -> None:
        practice = Practice(accounts_client)
        model.reply = [compact(practice.row["expected_answer"], legibility="unsure")]
        practice.version += 1
        reply = accounts_client.post(
            f"/api/practice/problems/{practice.problem['id']}/check",
            json={"sessionId": "x", "snapshotVersion": 1, "image": png(), "inkLines": [{"id": "l", "inImage": True}]},
            headers=practice.headers,
        ).json()
        assert reply["outcome"] == "in_progress"
        assert practice.row["status"] == "open"

    def test_true_but_unfinished_isnt_an_alert_or_an_answer(self, accounts_client: TestClient, model: Model) -> None:
        practice = Practice(accounts_client, "combining_like_terms")
        working = practice.row["prompt_math"]
        reply = practice.answer(model, working)
        assert reply["mistakes"] == []
        assert reply["outcome"] == "in_progress"

    @pytest.mark.parametrize("topic", SUPPORTED)
    def test_every_topic_through_the_pipeline(self, accounts_client: TestClient, model: Model, topic: str) -> None:
        practice = Practice(accounts_client, topic)
        wrong = practice.answer(model, wrong_answer(practice.row["expected_answer"]))
        assert wrong["outcome"] == "incorrect", wrong
        mistake = wrong["mistakes"][0]["id"]
        right = practice.answer(model, practice.row["expected_answer"], acknowledged=[mistake])
        assert right["outcome"] == "correct", right


class TestAnswerStaysHidden:
    def test_not_in_any_reply_until_asked_for(self, accounts_client: TestClient, model: Model) -> None:
        practice = Practice(accounts_client)
        expected = practice.row["expected_answer"]
        assert "expected" not in json.dumps(practice.problem).lower()
        assert expected not in json.dumps({k: v for k, v in practice.problem.items() if k != "id" and k != "sessionId" and k != "checkSessionId"})
        reply = practice.answer(model, str(int(expected) + 1))
        visible = json.dumps({k: reply[k] for k in ("mistake", "mistakes", "intervention", "outcome", "status")})
        assert "expected" not in visible.lower()
        assert f'"{expected}"' not in visible

    def test_revealing_is_on_record_and_doesnt_count_as_right(self, accounts_client: TestClient) -> None:
        practice = Practice(accounts_client)
        url = f"/api/practice/problems/{practice.problem['id']}/reveal"
        answer = accounts_client.post(url, headers=practice.headers).json()["expectedAnswer"]
        assert answer == practice.row["expected_answer"]
        summary = practice.summary()
        assert (summary["correct"], summary["revealed"]) == (0, 1)

    def test_revealing_after_getting_it_right_leaves_it_right(self, accounts_client: TestClient, model: Model) -> None:
        practice = Practice(accounts_client)
        practice.answer(model, practice.row["expected_answer"])
        accounts_client.post(f"/api/practice/problems/{practice.problem['id']}/reveal", headers=practice.headers)
        assert practice.summary()["correct"] == 1


class TestIsolation:
    def test_another_account_cant_see_or_answer_a_problem(self, accounts_client: TestClient, model: Model) -> None:
        practice = Practice(accounts_client)
        other = register(accounts_client, "b@example.com")
        base = f"/api/practice/problems/{practice.problem['id']}"
        assert accounts_client.post(f"{base}/reveal", headers=other).status_code == 404
        body = {"sessionId": "x", "snapshotVersion": 1, "image": png()}
        assert accounts_client.post(f"{base}/check", json=body, headers=other).status_code == 404
        session = f"/api/practice/sessions/{practice.problem['sessionId']}"
        assert accounts_client.post(f"{session}/next", headers=other).status_code == 404
        assert accounts_client.post(f"{session}/finish", headers=other).status_code == 404

    def test_practice_needs_signing_in(self, accounts_client: TestClient) -> None:
        assert accounts_client.post("/api/practice/sessions", json={"topic": "division"}).status_code == 401

    def test_unsupported_topics_are_refused(self, accounts_client: TestClient) -> None:
        response = accounts_client.post("/api/practice/sessions", json={"topic": "product_rule"}, headers=register(accounts_client))
        assert response.status_code == 422


class TestSeparateFromTheNotebook:
    def test_practice_mistakes_dont_appear_in_a_notebook_recap(self, accounts_client: TestClient, model: Model) -> None:
        practice = Practice(accounts_client)
        notebook = accounts_client.post("/api/sessions/current", headers=practice.headers).json()["session"]["id"]
        mistake = practice.answer(model, str(int(practice.row["expected_answer"]) + 1))["mistakes"][0]["id"]
        accounts_client.post(
            "/api/learning/events",
            json={"events": [{"id": "practice-event-1", "eventType": "mistake_detected", "mistakeId": mistake, "sessionId": practice.problem["sessionId"]}]},
            headers=practice.headers,
        )
        recap = accounts_client.post(f"/api/sessions/{notebook}/finish", headers=practice.headers).json()
        assert recap["glance"]["found"] == 0
        rows = accounts_client.app.state.services.accounts.store.database.query(
            "SELECT source FROM learning_events WHERE event_type = 'mistake_detected'"
        )
        assert [row["source"] for row in rows] == ["practice"]

    def test_practice_doesnt_count_towards_repeats(self, accounts_client: TestClient, model: Model) -> None:
        practice = Practice(accounts_client)
        mistake = practice.answer(model, str(int(practice.row["expected_answer"]) + 1))["mistakes"][0]["id"]
        accounts_client.post(
            "/api/learning/events",
            json={"events": [{"id": "practice-event-2", "eventType": "mistake_detected", "mistakeId": mistake, "sessionId": practice.problem["sessionId"]}]},
            headers=practice.headers,
        )
        notebook = accounts_client.post("/api/sessions/current", headers=practice.headers).json()["session"]["id"]
        model.reply = [compact("2 * 3 = 5", continues_previous="no")]
        found = accounts_client.post(
            "/api/analyze",
            json={"sessionId": "page", "snapshotVersion": 1, "image": png(), "inkLines": [{"id": "l", "inImage": True}], "studySessionId": notebook},
            headers=practice.headers,
        ).json()["mistakes"][0]["id"]
        accounts_client.post(
            "/api/learning/events",
            json={"events": [{"id": "notebook-event-1", "eventType": "mistake_detected", "mistakeId": found, "sessionId": notebook}]},
            headers=practice.headers,
        )
        recap = accounts_client.post(f"/api/sessions/{notebook}/finish", headers=practice.headers).json()
        assert "recent sessions" not in recap["recommendation"]["reason"]
        assert "2 mistakes" not in recap["recommendation"]["reason"]


class TestSummary:
    def test_one_right_answer_isnt_mastery(self, accounts_client: TestClient, model: Model) -> None:
        practice = Practice(accounts_client)
        practice.answer(model, practice.row["expected_answer"])
        summary = practice.summary()
        assert (summary["attempted"], summary["correct"], summary["hints"]) == (1, 1, 0)
        assert summary["message"] == (
            "You got one multiplication question right, with no help. One question doesn't say much, so try another when you're ready."
        )
        assert "master" not in summary["message"].lower()

    def test_two_right_with_no_help(self, accounts_client: TestClient, model: Model) -> None:
        practice = Practice(accounts_client, "power_rule")
        practice.answer(model, practice.row["expected_answer"])
        second = accounts_client.post(f"/api/practice/sessions/{practice.problem['sessionId']}/next", headers=practice.headers).json()
        assert second["difficulty"] == "intermediate"
        assert second["id"] != practice.problem["id"]
        practice.problem = second
        practice.answer(model, practice.row["expected_answer"])
        summary = practice.summary()
        assert summary["correct"] == 2
        assert summary["message"] == "You got 2 power rule questions right with no hints. Try a harder example next time."

    def test_nothing_answered_says_so(self, accounts_client: TestClient) -> None:
        summary = Practice(accounts_client).summary()
        assert (summary["attempted"], summary["correct"]) == (0, 0)
        assert "No multiplication questions" in summary["message"]

    def test_stays_on_after_a_recap(self, accounts_client: TestClient, model: Model) -> None:
        headers = register(accounts_client)
        notebook = accounts_client.post("/api/sessions/current", headers=headers).json()["session"]["id"]
        accounts_client.post(f"/api/sessions/{notebook}/finish", headers=headers)
        problem = accounts_client.post(
            "/api/practice/sessions", json={"topic": "division", "originSessionId": notebook}, headers=headers
        ).json()
        accounts_client.post(f"/api/practice/sessions/{problem['sessionId']}/finish", headers=headers)
        detail = accounts_client.get(f"/api/sessions/{notebook}/recap", headers=headers).json()
        assert detail["practice"][0]["topic"] == "division"

    def test_practice_with_no_mistakes_offers_a_topic_without_claiming_anything(self, accounts_client: TestClient) -> None:
        headers = register(accounts_client)
        problem = accounts_client.post("/api/practice/sessions", json={}, headers=headers).json()
        assert problem["topic"] == SUPPORTED[0]
        topics = accounts_client.get("/api/practice/topics", headers=headers).json()["topics"]
        assert [topic["topic"] for topic in topics] == list(SUPPORTED)
