"""Collecting samples, and using them. Meta's API is mocked, so this says nothing about whether they help."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from quack.main import build_services, create_app
from quack.services.calibration import load_calibration_set
from quack.services.handwriting import HandwritingReading, ReadLine, WriterReference
from quack.services.writer_references import distrust_copies, select_references
from tests.accounts_support import account_settings, calibrate, register, sample, strokes
from tests.test_line_memory import compact
from tests.test_pipeline import CHAT, completion, png

PROMPTS = load_calibration_set().prompts
FIRST = PROMPTS[0].id


def put(client: TestClient, headers: dict[str, str], prompt_id: str, body: dict[str, Any] | None = None) -> httpx.Response:
    return client.put(f"/api/calibration/samples/{prompt_id}", json=body or sample(), headers=headers)


class TestCollecting:
    def test_six_prompts_as_written_in_the_brief(self, accounts_client: TestClient) -> None:
        state = accounts_client.get("/api/calibration", headers=register(accounts_client)).json()
        assert [p["display"] for p in state["prompts"]] == [
            "2 × 3 = 6", "1 ÷ 4 = 1/4", "x² + 3x", "d/dx(x³) = 3x²", "7x + 1 = 15", "(x + 2)²",
        ]
        assert state["onboardingStatus"] == "account_created"
        assert state["completedPromptIds"] == []

    def test_empty_samples_are_refused(self, accounts_client: TestClient) -> None:
        headers = register(accounts_client)
        assert put(accounts_client, headers, FIRST, {**sample(), "strokes": []}).status_code == 422
        empty = {**sample(), "strokes": [{**strokes(1)[0], "points": []}]}
        assert put(accounts_client, headers, FIRST, empty).status_code == 422
        assert accounts_client.get("/api/calibration", headers=headers).json()["completedPromptIds"] == []

    def test_a_stray_mark_is_refused_with_something_to_do_about_it(self, accounts_client: TestClient) -> None:
        response = put(accounts_client, register(accounts_client), "differentiation", sample(count=1))
        assert response.status_code == 422
        assert "unfinished" in response.json()["detail"]

    def test_unknown_prompt(self, accounts_client: TestClient) -> None:
        assert put(accounts_client, register(accounts_client), "nope").status_code == 404

    def test_not_an_image_is_refused(self, accounts_client: TestClient) -> None:
        body = {**sample(), "image": "data:image/png;base64,bm90IGFuIGltYWdl"}
        assert put(accounts_client, register(accounts_client), FIRST, body).status_code == 400

    def test_the_label_is_the_servers_whatever_the_upload_says(self, accounts_client: TestClient) -> None:
        headers = register(accounts_client)
        put(accounts_client, headers, FIRST, {**sample(), "referenceExpression": "2 * 3 = 5"})
        calibrate(accounts_client, headers)
        labels = [s["referenceExpression"] for s in accounts_client.get("/api/calibration/export", headers=headers).json()["samples"]]
        assert "2 * 3 = 6" in labels and "2 * 3 = 5" not in labels

    def test_interrupted_onboarding_resumes_where_it_stopped(self, tmp_path: Path) -> None:
        settings = account_settings(tmp_path)
        client = TestClient(create_app(build_services(settings)))
        headers = register(client)
        put(client, headers, PROMPTS[0].id)
        put(client, headers, PROMPTS[1].id)
        # A different device, after a restart.
        later = TestClient(create_app(build_services(settings)))
        state = later.get("/api/calibration", headers=headers).json()
        assert state["onboardingStatus"] == "calibration_in_progress"
        assert state["completedPromptIds"] == [PROMPTS[0].id, PROMPTS[1].id]

    def test_saving_a_sample_again_replaces_it(self, accounts_client: TestClient) -> None:
        headers = register(accounts_client)
        put(accounts_client, headers, FIRST)
        put(accounts_client, headers, FIRST, sample(count=20))
        calibrate(accounts_client, headers)
        saved = accounts_client.get("/api/calibration/export", headers=headers).json()["samples"]
        assert len(saved) == 6 and len({s["promptId"] for s in saved}) == 6

    def test_stays_complete_across_a_restart_and_signing_in_again(self, tmp_path: Path) -> None:
        settings = account_settings(tmp_path)
        client = TestClient(create_app(build_services(settings)))
        headers = register(client)
        calibrate(client, headers)
        assert client.get("/api/profile", headers=headers).json()["onboardingStatus"] == "calibration_completed"
        later = TestClient(create_app(build_services(settings)))
        login = later.post("/api/auth/local/login", json={"email": "a@example.com", "password": "correct horse"}).json()
        again = {"authorization": f"Bearer {login['accessToken']}"}
        assert later.get("/api/profile", headers=again).json() == {
            "userId": login["userId"], "onboardingStatus": "calibration_completed", "hasHandwritingSamples": True,
        }

    def test_redoing_one_sample_doesnt_ask_for_the_rest(self, accounts_client: TestClient) -> None:
        headers = register(accounts_client)
        calibrate(accounts_client, headers)
        put(accounts_client, headers, FIRST)
        assert accounts_client.get("/api/profile", headers=headers).json()["onboardingStatus"] == "calibration_completed"

    def test_deleting_samples_doesnt_ask_for_them_again(self, accounts_client: TestClient) -> None:
        headers = register(accounts_client)
        calibrate(accounts_client, headers)
        after = accounts_client.delete("/api/calibration", headers=headers).json()
        assert after["onboardingStatus"] == "calibration_completed" and after["hasHandwritingSamples"] is False
        assert accounts_client.get("/api/calibration/export", headers=headers).json()["samples"] == []

    def test_restarting_is_a_choice_that_starts_again(self, accounts_client: TestClient) -> None:
        headers = register(accounts_client)
        calibrate(accounts_client, headers)
        state = accounts_client.post("/api/calibration/restart", headers=headers).json()
        assert state["completedPromptIds"] == [] and state["onboardingStatus"] == "calibration_in_progress"


REFS = tuple(WriterReference(p.id, p.reference, f"data:image/png;base64,{p.id}") for p in PROMPTS)


def line(math: str, legibility: str = "clear") -> ReadLine:
    return ReadLine(
        raw_text=math, math=math, kind="math", legibility=legibility, alternatives=[],
        continues_previous="unknown", operation="arithmetic", y_top=0, y_bottom=0,
    )


class TestChoosingReferences:
    def test_never_more_than_the_limit(self) -> None:
        assert len(select_references(REFS, [], 2)) == 2
        assert len(select_references(REFS, ["x^2+7=(3)"], 1)) == 1

    def test_picks_samples_showing_the_symbols_in_doubt(self) -> None:
        chosen = select_references(REFS, ["(x+1)^2"], 1)
        assert chosen[0].prompt_id == "brackets_powers"

    def test_the_same_doubt_picks_the_same_samples(self) -> None:
        assert select_references(REFS, ["3x+4"], 2) == select_references(REFS, ["3x+4"], 2)

    def test_never_shows_a_reading_the_line_it_may_be_looking_at(self) -> None:
        chosen = select_references(REFS, ["2*3=6", "2*3=5"], 6)
        assert "digits_multiplication" not in [reference.prompt_id for reference in chosen]

    def test_no_samples_no_references(self) -> None:
        assert select_references((), ["2*3=6"], 2) == ()


class TestCopying:
    def test_a_reading_identical_to_a_sample_it_saw_cant_be_trusted(self) -> None:
        guarded = distrust_copies(HandwritingReading(lines=[line("2 * 3 = 6")]), REFS)
        assert guarded.lines[0].legibility == "unsure"

    def test_spelling_doesnt_hide_a_copy(self) -> None:
        guarded = distrust_copies(HandwritingReading(lines=[line("2*3=6")]), REFS)
        assert guarded.lines[0].legibility == "unsure"

    def test_other_lines_are_left_alone(self) -> None:
        guarded = distrust_copies(HandwritingReading(lines=[line("2 * 3 = 5"), line("x^2")]), REFS)
        assert [item.legibility for item in guarded.lines] == ["clear", "clear"]


class Model:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.reply: list[dict[str, Any]] = [compact("3 * 4 = 12")]
        self.refuse_with_references = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        if self.refuse_with_references and len(images(body)) > 1:
            return httpx.Response(400, json={"error": {"message": "too many images"}})
        return completion({"lines": self.reply})


def images(call: dict[str, Any]) -> list[str]:
    return [part["image_url"]["url"] for part in call["messages"][1]["content"] if part["type"] == "image_url"]


def texts(call: dict[str, Any]) -> str:
    return " ".join(part["text"] for part in call["messages"][1]["content"] if part["type"] == "text")


@pytest.fixture
def model() -> Any:
    with respx.mock:
        answer = Model()
        respx.post(CHAT).mock(side_effect=answer)
        yield answer


def check(client: TestClient, headers: dict[str, str], version: int = 1, **overrides: Any) -> dict[str, Any]:
    body = {
        "sessionId": "page",
        "snapshotVersion": version,
        "image": png(),
        "inkLines": [{"id": "line-1", "inImage": True}],
    } | overrides
    response = client.post("/api/analyze", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


class TestReadingWithReferences:
    def test_the_usual_check_has_no_references(self, accounts_client: TestClient, model: Model) -> None:
        headers = register(accounts_client)
        calibrate(accounts_client, headers)
        check(accounts_client, headers)
        assert len(model.calls) == 2
        assert all(len(images(call)) == 1 for call in model.calls)
        assert all("Reference sample" not in texts(call) for call in model.calls)

    def test_looking_again_brings_in_the_students_samples(self, accounts_client: TestClient, model: Model) -> None:
        headers = register(accounts_client)
        calibrate(accounts_client, headers)
        reply = check(accounts_client, headers, attempt=1)
        assert len(model.calls) == 2
        for call in model.calls:
            assert len(images(call)) == 3
            assert texts(call).count("Reference sample") == 2
            assert "Report nothing from a reference" in call["messages"][0]["content"]
            # The page to read comes last, and is the only one the question is about.
            assert call["messages"][1]["content"][-1]["type"] == "image_url"
            assert "only this one" in texts(call) and "It has 1 written line" in texts(call)
        assert [call["references"] for call in reply["timings"]["calls"]] == [2, 2]

    def test_asking_directly_brings_them_in_too(self, accounts_client: TestClient, model: Model) -> None:
        headers = register(accounts_client)
        calibrate(accounts_client, headers)
        check(accounts_client, headers, trigger="manual")
        assert all(len(images(call)) == 3 for call in model.calls)

    def test_uses_what_didnt_settle_to_choose(self, accounts_client: TestClient, model: Model) -> None:
        headers = register(accounts_client)
        calibrate(accounts_client, headers)
        model.reply = [compact("(x+2)^2", legibility="unsure")]
        check(accounts_client, headers)
        model.calls.clear()
        check(accounts_client, headers, version=2, attempt=1)
        assert "Reference sample 1 from this writer. It shows: (x + 2)^2" not in texts(model.calls[0])
        assert "x^2" in texts(model.calls[0])

    def test_nobody_elses_samples_are_sent(self, accounts_client: TestClient, model: Model) -> None:
        one, other = register(accounts_client), register(accounts_client, "b@example.com")
        calibrate(accounts_client, one)
        check(accounts_client, other, attempt=1)
        assert all(len(images(call)) == 1 for call in model.calls)

    def test_uncalibrated_students_get_the_baseline(self, accounts_client: TestClient, model: Model) -> None:
        check(accounts_client, register(accounts_client), trigger="manual")
        assert all(len(images(call)) == 1 for call in model.calls)

    def test_deleted_samples_stop_being_sent(self, accounts_client: TestClient, model: Model) -> None:
        headers = register(accounts_client)
        calibrate(accounts_client, headers)
        check(accounts_client, headers, attempt=1)
        accounts_client.delete("/api/calibration", headers=headers)
        model.calls.clear()
        check(accounts_client, headers, version=2, attempt=1)
        assert all(len(images(call)) == 1 for call in model.calls)

    def test_a_refused_request_falls_back_to_the_baseline(self, accounts_client: TestClient, model: Model) -> None:
        headers = register(accounts_client)
        calibrate(accounts_client, headers)
        model.refuse_with_references = True
        reply = check(accounts_client, headers, attempt=1)
        assert reply["status"] == "no_error"
        assert sum(len(images(call)) == 1 for call in model.calls) == 2

    @pytest.mark.parametrize(("mode", "expected"), [("off", 1), ("on_doubt", 1), ("always", 3)])
    def test_the_flag_chooses_for_a_first_look(self, tmp_path: Path, model: Model, mode: str, expected: int) -> None:
        client = TestClient(create_app(build_services(account_settings(tmp_path, QUACK_PERSONALIZATION=mode))))
        headers = register(client)
        calibrate(client, headers)
        check(client, headers)
        assert all(len(images(call)) == expected for call in model.calls)

    def test_off_means_off_for_looking_again_too(self, tmp_path: Path, model: Model) -> None:
        client = TestClient(create_app(build_services(account_settings(tmp_path, QUACK_PERSONALIZATION="off"))))
        headers = register(client)
        calibrate(client, headers)
        check(client, headers, attempt=1, trigger="manual")
        assert all(len(images(call)) == 1 for call in model.calls)

    def test_one_request_can_ask_for_a_mode_for_comparing_them(self, accounts_client: TestClient, model: Model) -> None:
        headers = register(accounts_client)
        calibrate(accounts_client, headers)
        check(accounts_client, headers, personalization="always")
        assert all(len(images(call)) == 3 for call in model.calls)

    def test_a_request_cant_ask_for_a_mode_in_production(self, tmp_path: Path) -> None:
        from quack.schemas import AnalyzeRequest

        settings = account_settings(tmp_path).model_copy(update={"env": "production"})
        analysis = build_services(account_settings(tmp_path)).analysis
        analysis.settings = settings
        request = AnalyzeRequest(
            session_id="s", snapshot_version=1, image=png(), ink_lines=[{"id": "l", "in_image": True}], personalization="always"
        )
        assert analysis.wants_writer(request) is False


class TestWrongWorkStaysWrong:
    """Calibrating on 2 * 3 = 6 mustn't make a later 2 * 3 = 5 come out right."""

    def test_a_wrong_sum_is_still_reported_with_references(self, accounts_client: TestClient, model: Model) -> None:
        headers = register(accounts_client)
        calibrate(accounts_client, headers)
        model.reply = [compact("2 * 3 = 5")]
        reply = check(accounts_client, headers, attempt=1)
        assert reply["status"] == "confirmed_error"
        assert reply["mistakes"][0]["currentStep"] == "5"

    def test_a_reading_copied_from_a_sample_doesnt_clear_a_mistake(self, tmp_path: Path, model: Model) -> None:
        # Every sample goes in, so the one that says the right answer is certainly shown.
        client = TestClient(create_app(build_services(account_settings(tmp_path, QUACK_REFERENCES_PER_READING=6))))
        headers = register(client)
        calibrate(client, headers)
        model.reply = [compact("2 * 3 = 5")]
        wrong = check(client, headers)["mistakes"][0]["id"]
        # The student hasn't changed anything, and the references talk the model into the sample's answer.
        model.reply = [compact("2 * 3 = 6")]
        reply = check(client, headers, version=2, attempt=1, trigger="manual", acknowledgedMistakeIds=[wrong])
        assert "2 * 3 = 6" in texts(model.calls[-1])
        assert reply["resolvedMistakeIds"] == []

    def test_a_reading_copied_from_a_sample_isnt_remembered(self, accounts_client: TestClient, model: Model) -> None:
        headers = register(accounts_client)
        calibrate(accounts_client, headers)
        model.reply = [compact("2 * 3 = 6")]
        reply = check(accounts_client, headers, attempt=1, personalization="always")
        assert reply["status"] != "confirmed_error"
        assert reply["settledLineIds"] == []

    def test_without_references_the_same_correct_line_settles(self, accounts_client: TestClient, model: Model) -> None:
        headers = register(accounts_client)
        calibrate(accounts_client, headers)
        model.reply = [compact("2 * 3 = 6")]
        assert check(accounts_client, headers)["settledLineIds"] == ["line-1"]
