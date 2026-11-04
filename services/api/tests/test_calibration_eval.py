"""The comparison harness, against a scripted model. These numbers mean nothing about real reading."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import respx

from quack.config import Settings
from tests.test_line_memory import compact
from tests.test_pipeline import CHAT, completion, png

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "evals")]

from calibration_eval import build_service, held_out, run_all  # noqa: E402
from calibration_metrics import render_report, score_mode  # noqa: E402

from quack.services.handwriting import WriterReference  # noqa: E402
from quack.services.writer_references import WriterProfile  # noqa: E402

WRITER = WriterProfile("w", (WriterReference("a", "2 * 3 = 6", png()), WriterReference("b", "x^2 + 3x", png())))
HARNESS = ROOT / "evals" / "calibration_eval.py"


def record(**fields: Any) -> dict[str, Any]:
    return {
        "case": "c", "ground_truth": ["3*4=10"], "transcription": ["3*4=10"], "expected": "confirmed_error",
        "status": "confirmed_error", "latency_ms": 100.0, "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01,
        "reference_reads": 0,
    } | fields


class TestScoring:
    def test_counts_a_read_and_a_caught_mistake(self) -> None:
        score = score_mode([record()])
        assert (score["exact_lines"], score["precision"], score["recall"], score["false_alerts"]) == (1, 1, 1, 0)
        assert score["zeros"] == {"lines": 1, "right": 1}
        assert score["multiplication"] == {"lines": 1, "right": 1}

    def test_a_misread_zero_counts_against_it(self) -> None:
        score = score_mode([record(transcription=["3*4=16"], expected="confirmed_error", status="uncertain")])
        assert score["exact_lines"] == 0
        assert score["zeros"] == {"lines": 1, "right": 0}
        assert score["recall"] == 0
        assert score["symbol_error_rate"] > 0

    def test_a_false_alert_is_named(self) -> None:
        score = score_mode([record(case="fine", ground_truth=["2+2=4"], transcription=["2+2=5"], expected="no_error")])
        assert score["false_alerts"] == 1
        assert score["false_alert_cases"] == ["fine"]
        assert score["precision"] == 0

    def test_failed_requests_arent_scored_as_reads(self) -> None:
        score = score_mode([record(error="timeout", transcription=[]), record()])
        assert score["failed"] == 1
        assert score["lines"] == 1

    def test_no_alerts_means_no_precision_rather_than_a_perfect_one(self) -> None:
        assert score_mode([record(expected="no_error", status="no_error")])["precision"] is None

    def test_synthetic_runs_say_so_in_the_report(self) -> None:
        meta = {"run": "r", "writers": 1, "held_out": 1, "excluded": 0, "samples": 6, "references_per_reading": 2}
        synthetic = render_report({"off": score_mode([record()])}, {**meta, "provenance": "synthetic ink"})
        assert "not handwriting accuracy" in synthetic
        real = render_report({"off": score_mode([record()])}, {**meta, "provenance": "real handwriting"})
        assert "not handwriting accuracy" not in real


class TestHeldOut:
    def test_calibration_prompts_cant_be_evidence(self) -> None:
        entries = [
            {"id": "same", "lines": ["2*3=6"]},
            {"id": "spelled_differently", "lines": ["2 * 3 = 6"]},
            {"id": "wrong_version", "lines": ["2*3=5"]},
            {"id": "other", "lines": ["7*8=56"]},
        ]
        dataset = held_out(entries, WRITER)
        assert [entry["id"] for entry in dataset.cases] == ["wrong_version", "other"]
        assert dataset.excluded == ["same", "spelled_differently"]


class TestRunning:
    def run(self, tmp_path: Path, modes: list[str], reading: list[dict[str, Any]], max_usd: float = 5) -> tuple[dict, list[dict]]:
        image = tmp_path / "case.png"
        import base64

        image.write_bytes(base64.b64decode(png().split(",", 1)[1]))
        cases = [{"id": "c1", "lines": ["3*4=10"], "expected": "confirmed_error", "image": "case.png"}]
        settings = Settings(
            MODEL_API_KEY="k", MODEL_API_BASE_URL="https://api.test/v1", QUACK_LEDGER_PATH=tmp_path / "u.jsonl",
            QUACK_AUTH_MODE="off", _env_file=None,
        )
        calls: list[dict] = []

        def answer(request: httpx.Request) -> httpx.Response:
            calls.append(json.loads(request.content))
            return completion({"lines": reading})

        with respx.mock:
            respx.post(CHAT).mock(side_effect=answer)
            records = asyncio.run(run_all(build_service(settings), cases, tmp_path, modes, WRITER, max_usd))
        return records, calls

    def test_each_mode_sends_what_it_says(self, tmp_path: Path) -> None:
        records, calls = self.run(tmp_path, ["off", "always"], [compact("3 * 4 = 10", continues_previous="no")])
        image_counts = [sum(part["type"] == "image_url" for part in call["messages"][1]["content"]) for call in calls]
        assert image_counts == [1, 1, 3, 3]
        assert records["off"][0]["reference_reads"] == 0
        assert records["always"][0]["reference_reads"] == 2
        assert records["off"][0]["status"] == records["always"][0]["status"] == "confirmed_error"

    def test_on_doubt_only_looks_again_when_a_line_didnt_settle(self, tmp_path: Path) -> None:
        settled, calls = self.run(tmp_path, ["on_doubt"], [compact("3 * 4 = 12", continues_previous="no")])
        assert len(calls) == 2
        assert settled["on_doubt"][0]["reference_reads"] == 0
        unsure, calls = self.run(tmp_path, ["on_doubt"], [compact("3 * 4 = 12", continues_previous="no", legibility="unsure")])
        assert len(calls) == 4
        assert unsure["on_doubt"][0]["reference_reads"] == 2

    def test_stops_at_the_limit(self, tmp_path: Path) -> None:
        records, calls = self.run(tmp_path, ["off"], [compact("3 * 4 = 10")], max_usd=0)
        assert records["off"] == [] and calls == []


class TestCommandLine:
    def invoke(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(HARNESS), *args], capture_output=True, text=True, timeout=120)

    def references(self, tmp_path: Path) -> Path:
        path = tmp_path / "refs.json"
        path.write_text(json.dumps({"samples": [{"promptId": "a", "referenceExpression": "2 * 3 = 6", "image": png()}]}))
        return path

    def test_sends_nothing_without_saying_so(self, tmp_path: Path) -> None:
        manifest = tmp_path / "m.jsonl"
        manifest.write_text(json.dumps({"id": "c", "lines": ["3*4=10"], "provenance": "real", "image": "x.png", "expected": "confirmed_error"}) + "\n")
        result = self.invoke("--references", str(self.references(tmp_path)), "--manifest", str(manifest), "--max-usd", "1")
        assert result.returncode == 0, result.stderr
        assert "Nothing sent" in result.stdout

    def test_refuses_synthetic_ink_as_evidence(self, tmp_path: Path) -> None:
        manifest = tmp_path / "m.jsonl"
        manifest.write_text(json.dumps({"id": "c", "lines": ["3*4=10"], "provenance": "synthetic", "image": "x.png"}) + "\n")
        result = self.invoke("--references", str(self.references(tmp_path)), "--manifest", str(manifest), "--max-usd", "1")
        assert result.returncode == 1
        assert "aren't real handwriting" in result.stdout

    def test_says_when_theres_no_handwriting(self, tmp_path: Path) -> None:
        result = self.invoke("--references", str(self.references(tmp_path)), "--manifest", str(tmp_path / "none.jsonl"), "--max-usd", "1")
        assert result.returncode == 1
        assert "Collect some" in result.stdout
