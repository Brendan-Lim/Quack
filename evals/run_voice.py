"""Measure Muse Voice Transcribe on maths vocabulary, using macOS `say` to produce speech.

    python evals/run_voice.py --confirm-credits

The audio is synthesised, so this checks the integration and vocabulary, not real students'
speech, accents or classrooms. Audio is generated in a temporary directory and not kept.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT.parent / "services" / "api")]

from metrics import edit_distance  # noqa: E402

from quack.config import get_settings  # noqa: E402
from quack.llm.ledger import Ledger  # noqa: E402
from quack.llm.router import ModelError, ModelRouter  # noqa: E402
from quack.services.voice import classify_intent, transcribe_wav  # noqa: E402

PHRASES = [
    ("Hey Quack, explain my mistake", "explain", ["explain", "mistake"]),
    ("Hey Quack, give me a hint", "hint", ["hint"]),
    ("Hey Quack, why can't I cancel this term", "explain", ["cancel", "term"]),
    ("Hey Quack, did I use the chain rule correctly", "explain", ["chain rule"]),
    ("How do I differentiate x squared plus two x", "explain", ["differentiate", "squared"]),
    ("Hey Quack, is my derivative right", "check", ["derivative"]),
    ("What is the power rule", "explain", ["power rule"]),
    ("Check my working please", "check", ["check", "working"]),
    ("Why does the exponent go down by one", "explain", ["exponent"]),
    ("Should I expand the brackets first", "explain", ["expand", "brackets"]),
]


def words(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", "", text.lower()).split()


def speak(text: str, path: Path) -> None:
    subprocess.run(["say", "-o", str(path), "--data-format=LEI16@24000", "--file-format=WAVE", text], check=True)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--confirm-credits", action="store_true")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    if not args.confirm_credits:
        print("Refusing to spend: pass --confirm-credits once you have checked your active credits.")
        return 2

    settings = get_settings()
    ledger = Ledger(settings.ledger_path, settings.budget_project_usd, settings.budget_session_usd, settings.budget_request_usd)
    router = ModelRouter(settings, ledger)
    run_id = args.run_id or time.strftime("voice-%Y%m%d-%H%M%S")
    rows = []
    start_spend = ledger.project_spend
    with tempfile.TemporaryDirectory() as folder:
        for index, (phrase, wanted, vocabulary) in enumerate(PHRASES):
            path = Path(folder) / f"{index}.wav"
            speak(phrase, path)
            started = time.perf_counter()
            try:
                transcript = await transcribe_wav(router, path.read_bytes(), run_id=run_id)
                seconds = time.perf_counter() - started
                intent = await classify_intent(router, transcript)
            except ModelError as exc:
                print(f"  FAILED {phrase!r}: {exc.category} ({exc.status})")
                rows.append({"phrase": phrase, "error": exc.category})
                continue
            distance = edit_distance(" ".join(words(phrase)), " ".join(words(transcript)))
            heard = [term for term in vocabulary if term in transcript.lower()]
            rows.append(
                {
                    "phrase": phrase,
                    "transcript": transcript,
                    "char_error_rate": distance / max(len(" ".join(words(phrase))), 1),
                    "vocabulary": vocabulary,
                    "vocabulary_heard": heard,
                    "wanted_intent": wanted,
                    "intent": intent.intent,
                    "transcribe_seconds": seconds,
                }
            )
            print(f"  {phrase!r} -> {transcript!r} [{intent.intent}] {seconds:.1f}s", flush=True)

    done = [row for row in rows if "error" not in row]
    out = ROOT / "results" / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "voice.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    if done:
        terms = sum(len(row["vocabulary"]) for row in done)
        hit = sum(len(row["vocabulary_heard"]) for row in done)
        print(
            f"{len(done)}/{len(rows)} transcribed. Vocabulary {hit}/{terms}. "
            f"Intent {sum(row['intent'] == row['wanted_intent'] for row in done)}/{len(done)}. "
            f"Mean character error {sum(row['char_error_rate'] for row in done) / len(done):.1%}. "
            f"Spend about ${ledger.project_spend - start_spend:.4f}."
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
