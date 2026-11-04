"""Showing a reading a few samples of this student's own handwriting.

This is not training: no model changes, and it's all forgotten after the request. It only tells a
reading how this writer makes their symbols. Two things keep a sample's text from leaking into what's
read. A reading never gets a sample of what it might be looking at, and a reading that comes back
saying exactly what a sample it was shown says can't be believed, so it counts as unsure.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from quack.services.handwriting import HandwritingReading, WriterReference
from quack.services.user_store import StoredSample

# The symbols a student's writing is most often confused over, for when nothing says what to look for.
USUAL_DOUBTS = "012345678x*^=+"


def _plain(text: str) -> str:
    return re.sub(r"[\s*×]", "", text).replace("÷", "/").replace("−", "-").lower()


def _symbols(text: str) -> set[str]:
    # A multiplication sign is dropped from `plain` so that spellings compare, but its shape matters here.
    return set(re.sub(r"\s", "", text.replace("×", "*").replace("−", "-").lower()))


@dataclass(frozen=True)
class WriterProfile:
    user_id: str
    samples: tuple[WriterReference, ...]

    @classmethod
    def from_samples(cls, user_id: str, samples: list[StoredSample]) -> WriterProfile:
        return cls(user_id, tuple(WriterReference(s.prompt_id, s.reference_expression, s.image_data) for s in samples))


def select_references(
    samples: tuple[WriterReference, ...], doubted: list[str], limit: int
) -> tuple[WriterReference, ...]:
    """Pick the few samples showing most of the symbols in question, in a repeatable way.

    `doubted` is what readings of the line said it might be. It only supplies a set of characters.
    """

    wanted = set().union(*(_symbols(text) for text in doubted)) or set(USUAL_DOUBTS)
    excluded = {_plain(text) for text in doubted}
    candidates = [sample for sample in samples if _plain(sample.label) not in excluded]
    chosen: list[WriterReference] = []
    remaining = set(wanted)
    while candidates and len(chosen) < limit:
        best = max(candidates, key=lambda sample: len(_symbols(sample.label) & remaining))
        if chosen and not _symbols(best.label) & remaining:
            break
        chosen.append(best)
        candidates.remove(best)
        remaining -= _symbols(best.label)
    return tuple(chosen)


def references_tag(references: tuple[WriterReference, ...], user_id: str) -> str:
    """For cache keys. A reading with references is a different reading, and it's this student's."""

    joined = "|".join(reference.prompt_id for reference in references)
    return hashlib.sha256(f"{user_id}:{joined}".encode()).hexdigest()[:12]


def distrust_copies(reading: HandwritingReading, references: tuple[WriterReference, ...]) -> HandwritingReading:
    """A line reading exactly like a sample that was just shown may have been copied from it.

    So it can't settle, and can't show that a mistake has been fixed. It costs a line that really does
    say that a moment's silence, against telling a student who wrote 2 * 3 = 5 that they're right.
    """

    labels = {_plain(reference.label) for reference in references}
    lines = [
        line.model_copy(update={"legibility": "unsure"})
        if line.kind in ("math", "incomplete") and _plain(line.math) in labels and line.legibility == "clear"
        else line
        for line in reading.lines
    ]
    return HandwritingReading(lines=lines)
