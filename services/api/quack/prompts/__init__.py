"""Versioned prompts. Bump the file name when a prompt changes, so evaluations stay comparable."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent

TRANSCRIBE = "transcribe.v9"
TRANSCRIBE_COMPACT = "transcribe.v8"
WRITER_REFERENCES = "writer_references.v1"
OPERATION = "operation.v1"
HINT = "hint.v3"
EXPLAIN = "explain.v2"
VOICE_INTENT = "voice_intent.v1"
ASK = "ask.v1"


@lru_cache
def load_prompt(name: str) -> str:
    return (_DIR / f"{name}.md").read_text().strip()


def prompt_version(*names: str) -> str:
    joined = "\n".join(load_prompt(name) for name in names)
    return hashlib.sha256(joined.encode()).hexdigest()[:12]
