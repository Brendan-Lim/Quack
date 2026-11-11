"""Chain-rule practice keys, missing-factor patterns and existing safety boundaries. No paid calls."""

from __future__ import annotations

import asyncio
import random
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import sympy as sp
from fastapi.testclient import TestClient

from quack.services.math_parser import parse_expression, to_sympy
from quack.services.math_verifier import LineInput, completes_answer, verify_lines
from quack.services.practice import MAX_TRIES, generate_question, new_question
from quack.services.verifier_pool import VerificationFailed
from tests.accounts_support import account_settings, register
from tests.test_line_memory import compact
from tests.test_pipeline import png
from tests.test_practice import Model, Practice, model  # noqa: F401


@pytest.mark.parametrize("difficulty", ("basic", "intermediate"))
@pytest.mark.parametrize("seed", range(24))
def test_independent_keys_equivalent_answers_and_missing_inner_factors(difficulty: str, seed: int) -> None:
    question = generate_question("chain_rule", difficulty, random.Random(seed))
    source = to_sympy(parse_expression(question.math[5:-1]))
    x = next(iter(source.free_symbols))
    expected = to_sympy(parse_expression(question.expected))
    assert sp.simplify(sp.diff(source, x) - expected) == 0
    # Equivalent products in a different written order must be accepted and finish the question.
    answer = str(sp.diff(source, x)).replace("**", "^")
    statement = f"{question.math} = {answer}"
    assert verify_lines([LineInput(0, statement, posed=True)]).status == "valid"
    assert completes_answer(question.math, question.expected, statement)
    inner = source.base if source.is_Pow else source.args[0]
    rate = sp.diff(inner, x)
    assert rate != 1
    missing = str(sp.simplify(expected / rate)).replace("**", "^")
    report = verify_lines([LineInput(0, f"{question.math} = {missing}", posed=True)])
    assert report.first_invalid is not None
    assert report.first_invalid.error_type == "chain_rule_inner_missing"
    assert not completes_answer(question.math, question.expected, f"= {missing}")


@pytest.mark.parametrize("difficulty", ("basic", "intermediate"))
def test_seeded_fixtures_cover_all_families_and_difficulty(difficulty: str) -> None:
    families = set()
    for seed in range(24):
        source = to_sympy(parse_expression(generate_question("chain_rule", difficulty, random.Random(seed)).math[5:-1]))
        families.add(source.func)
        inner = source.base if source.is_Pow else source.args[0]
        x = next(iter(source.free_symbols))
        expected_degrees = (1,) if difficulty == "basic" else (2, 3)
        assert sp.degree(inner, x) in expected_degrees
    assert families == {sp.sin, sp.cos, sp.exp}


def test_all_generated_questions_and_answers_have_drawable_synthetic_glyphs() -> None:
    """The browser draws from this same alphabet; exp must work whichever question is chosen."""

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "evals"))
    from synthetic.render import GLYPHS, render

    for difficulty, count in (("basic", 9), ("intermediate", 18)):
        questions = {
            question.math: question
            for question in (generate_question("chain_rule", difficulty, random.Random(seed)) for seed in range(1000))
        }
        assert len(questions) == count
        for question in questions.values():
            text = f"{question.math} = {question.expected}"
            assert not set(text) - set(GLYPHS) - {" ", "^"}, text
            rendered = render([{"text": text}])
            assert rendered.getextrema()[0][0] < 255  # It drew ink, rather than a blank fixture.


def test_uncertain_practice_does_not_finish_or_penalise(accounts_client: TestClient, model: Model) -> None:  # noqa: F811
    practice = Practice(accounts_client, "chain_rule")
    model.reply = [compact(practice.row["expected_answer"], legibility="unsure", alternatives=["0"])]
    reply = accounts_client.post(f"/api/practice/problems/{practice.problem['id']}/check", headers=practice.headers, json={
        "sessionId": practice.problem["checkSessionId"], "snapshotVersion": 1,
        "image": png(),
        "inkLines": [{"id": "unsure", "inImage": True}],
    }).json()
    assert reply["outcome"] == "in_progress"
    assert reply["mistakes"] == []
    assert practice.row["status"] == "open"
    assert practice.summary()["correct"] == 0


def test_uncheckable_questions_fail_closed_without_model_calls(accounts_client: TestClient, model: Model) -> None:  # noqa: F811
    headers = register(accounts_client)
    failure = AsyncMock(side_effect=VerificationFailed("timeout"))
    with patch("quack.services.practice.shared_pool.verify", failure):
        reply = accounts_client.post("/api/practice/sessions", headers=headers, json={"topic": "chain_rule"})
    assert reply.status_code == 503
    assert failure.await_count == MAX_TRIES
    assert not model.calls


async def test_cancelled_generation_does_not_retry() -> None:
    cancelled = AsyncMock(side_effect=asyncio.CancelledError)
    with patch("quack.services.practice.shared_pool.verify", cancelled), pytest.raises(asyncio.CancelledError):
        await new_question("chain_rule", "basic", set(), 4)
    assert cancelled.await_count == 1


def test_no_model_for_generation_and_zero_budget_blocks_reading(tmp_path: Path, model: Model) -> None:  # noqa: F811
    from quack.main import build_services, create_app

    client = TestClient(create_app(build_services(account_settings(tmp_path, QUACK_BUDGET_PROJECT_USD=0))))
    practice = Practice(client, "chain_rule")
    assert model.calls == []
    reply = practice.answer(model, practice.row["expected_answer"])
    assert model.calls == []
    assert reply["outcome"] == "in_progress"
    assert reply["modelIssue"]["category"] == "budget_exceeded"
    assert practice.row["status"] == "open"


def test_another_account_cannot_reveal_chain_rule_key(accounts_client: TestClient) -> None:
    practice = Practice(accounts_client, "chain_rule")
    other = register(accounts_client, "other@example.com")
    reply = accounts_client.post(f"/api/practice/problems/{practice.problem['id']}/reveal", headers=other)
    assert reply.status_code == 404
