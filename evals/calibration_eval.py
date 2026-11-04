"""Does showing a reading the student's own samples help? Spends real credit.

    python evals/calibration_eval.py --references evals/dataset/private/refs-NAME.json --writer NAME \
        --modes off,on_doubt,always --max-usd 2 --confirm-credits

`--references` is the file from Settings, Download them. The cases are real handwriting by the same
person, collected with /collect and imported with import_collected.py. Anything that's the same as a
calibration prompt is left out, as a reading of those proves nothing.

Each mode runs through the real AnalysisService, with its two readings and its verifier, so the one
thing that differs is the references:

    off       Never shown them. The baseline.
    on_doubt  What the app does: look once, and if a line doesn't settle, look again with them.
    always    Shown them on every reading.

Without --confirm-credits it only says what it would run. Synthetic ink is refused unless
--synthetic-smoke is given, and the report then says it isn't handwriting accuracy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT.parent / "services" / "api")]

from calibration_metrics import render_report, score_mode
from metrics import squash

from quack.config import Settings, get_settings
from quack.llm.ledger import Ledger
from quack.llm.router import ModelRouter
from quack.schemas import AnalyzeRequest, AnalyzeResponse, InkLine
from quack.services.analysis import AnalysisService
from quack.services.handwriting import WriterReference
from quack.services.mistake_manager import MistakeManager
from quack.services.writer_references import WriterProfile

MODES = ("off", "on_doubt", "always")
DEFAULT_MANIFEST = ROOT / "dataset" / "private" / "manifest.jsonl"


@dataclass(frozen=True)
class Dataset:
    cases: list[dict[str, Any]]
    excluded: list[str]


def load_references(path: Path) -> WriterProfile:
    samples = json.loads(path.read_text())["samples"]
    references = tuple(WriterReference(s["promptId"], s["referenceExpression"], s["image"]) for s in samples)
    return WriterProfile("evaluation", references)


def held_out(entries: list[dict[str, Any]], writer: WriterProfile) -> Dataset:
    """Nothing the writer calibrated on can count as evidence that calibration works."""

    labels = {squash(sample.label) for sample in writer.samples}
    cases, excluded = [], []
    for entry in entries:
        (excluded if labels & {squash(line) for line in entry["lines"]} else cases).append(entry)
    return Dataset(cases, [entry["id"] for entry in excluded])


def _image_of(entry: dict[str, Any], base: Path) -> str:
    import base64

    return "data:image/png;base64," + base64.b64encode((base / entry["image"]).read_bytes()).decode()


async def _check(
    service: AnalysisService, entry: dict[str, Any], image: str, session: str, mode: str, attempt: int, writer: WriterProfile
) -> AnalyzeResponse:
    request = AnalyzeRequest(
        session_id=session,
        snapshot_version=attempt + 1,
        image=image,
        ink_lines=[InkLine(id=f"{entry['id']}-{i}", in_image=True) for i in range(len(entry["lines"]))],
        attempt=attempt,
        personalization=mode,  # type: ignore[arg-type]
    )
    return await service.analyze(request, writer)


async def run_case(
    service: AnalysisService, entry: dict[str, Any], base: Path, mode: str, writer: WriterProfile
) -> dict[str, Any]:
    image = _image_of(entry, base)
    session = f"eval-{mode}-{uuid.uuid4().hex[:12]}"
    started = time.perf_counter()
    response = await _check(service, entry, image, session, mode, 0, writer)
    responses = [response]
    # This is what the app does with a line that doesn't settle, and in this mode that's the point.
    if mode == "on_doubt" and not response.mistakes and len(response.settled_line_ids) < len(entry["lines"]):
        response = await _check(service, entry, image, session, mode, 1, writer)
        responses.append(response)
    elapsed = (time.perf_counter() - started) * 1000
    calls = [call for item in responses for call in item.timings.calls]
    issue = next((item.model_issue.category for item in responses if item.model_issue), None)
    return {
        "case": entry["id"],
        "mode": mode,
        "ground_truth": entry["lines"],
        "expected": entry["expected"],
        "status": response.status,
        "reason": response.reason,
        "transcription": [line.text for line in response.lines],
        "error": issue,
        "latency_ms": elapsed,
        "input_tokens": sum(call.input_tokens for call in calls),
        "output_tokens": sum(call.output_tokens for call in calls),
        "cost_usd": sum(call.cost_usd for call in calls),
        "reference_reads": sum(call.references > 0 for call in calls),
    }


def build_service(settings: Settings) -> AnalysisService:
    ledger = Ledger(
        settings.ledger_path, settings.budget_project_usd, settings.budget_session_usd,
        settings.budget_request_usd, settings.budget_hourly_usd,
    )
    # Development, so that one request can choose its mode. Both readings, as the app has them.
    tuned = settings.model_copy(
        update={"env": "development", "confirm_alerts": True, "confirm_in_parallel": True, "reading_cache_size": 0}
    )
    return AnalysisService(tuned, ModelRouter(tuned, ledger), MistakeManager())


async def run_all(
    service: AnalysisService,
    cases: list[dict[str, Any]],
    base: Path,
    modes: list[str],
    writer: WriterProfile,
    max_usd: float,
) -> dict[str, list[dict[str, Any]]]:
    """Each case in every mode before the next case, so that stopping early leaves a fair comparison."""

    records: dict[str, list[dict[str, Any]]] = {mode: [] for mode in modes}
    spent = 0.0
    for entry in cases:
        if spent >= max_usd:
            print(f"Stopped at ${spent:.2f}, the limit, with {len(records[modes[0]])} of {len(cases)} cases done.")
            break
        for mode in modes:
            record = await run_case(service, entry, base, mode, writer)
            records[mode].append(record)
            spent += record["cost_usd"]
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--writer", help="only this contributor's cases")
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--split", choices=("dev", "heldout"), help="see COLLECTION_PROTOCOL: touch heldout once, at the end")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-usd", type=float, required=True)
    parser.add_argument("--run", default=time.strftime("calibration-%Y%m%d-%H%M%S"))
    parser.add_argument("--confirm-credits", action="store_true", help="this spends real credit")
    parser.add_argument("--synthetic-smoke", action="store_true", help="allow synthetic ink, to test the pipeline only")
    args = parser.parse_args()

    modes = args.modes.split(",")
    unknown = set(modes) - set(MODES)
    if unknown:
        print(f"Unknown modes: {', '.join(sorted(unknown))}")
        return 1
    if not args.manifest.exists():
        print(f"No handwriting at {args.manifest}. Collect some with /collect, and bring it in with import_collected.py.")
        return 1

    entries = [json.loads(line) for line in args.manifest.read_text().splitlines() if line.strip()]
    if args.writer:
        entries = [entry for entry in entries if entry.get("contributor") == args.writer]
    if args.split:
        entries = [entry for entry in entries if entry.get("split") == args.split]
    if not args.synthetic_smoke:
        synthetic = [entry["id"] for entry in entries if entry.get("provenance") != "real"]
        if synthetic:
            print(f"Refused: {len(synthetic)} cases aren't real handwriting, so they can't be evidence about writers.")
            return 1

    writer = load_references(args.references)
    dataset = held_out(entries, writer)
    cases = dataset.cases[: args.limit] if args.limit else dataset.cases
    writers = sorted({entry.get("contributor", "unknown") for entry in cases})
    print(
        f"{len(cases)} held-out cases by {len(writers)} writer(s), "
        f"{len(dataset.excluded)} left out as calibration prompts, modes {modes}."
    )
    if not args.confirm_credits:
        print("Nothing sent. Add --confirm-credits to run it, which spends real credit.")
        return 0
    if not cases:
        print("Nothing to run.")
        return 1

    settings = get_settings()
    service = build_service(settings)
    base = args.manifest.parent if (args.manifest.parent / cases[0]["image"]).exists() else ROOT
    records = asyncio.run(run_all(service, cases, base, modes, writer, args.max_usd))

    out = ROOT / "results" / args.run
    out.mkdir(parents=True, exist_ok=True)
    (out / "records.json").write_text(json.dumps(records, indent=1))
    scores = {mode: score_mode(records[mode]) for mode in modes}
    meta = {
        "run": args.run,
        "provenance": "synthetic ink" if args.synthetic_smoke else "real handwriting",
        "writers": len(writers),
        "held_out": len(records[modes[0]]),
        "excluded": len(dataset.excluded),
        "samples": len(writer.samples),
        "references_per_reading": service.settings.references_per_reading,
    }
    report = render_report(scores, meta)
    (out / "report.md").write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
