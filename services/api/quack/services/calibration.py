"""One-time handwriting calibration: the prompts, and what counts as having written one.

The prompts are data, so changing them needs no code. A new set gets a new file and version, because
the samples and evaluations of the old one go with it. Nothing here reads a sample or decides that it
says the right thing: a student's handwriting being hard to read is the whole reason to keep it.
"""

from __future__ import annotations

import base64
import io
import json
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from quack.schemas import PenStrokeInput
from quack.services.handwriting import sanitise_image

_PROMPTS = Path(__file__).resolve().parent.parent / "calibration" / "prompts.v1.json"
# Long enough to see a symbol's shape. References go in each request that uses them, so they're kept small.
REFERENCE_LONG_EDGE = 640
# Fewer strokes per symbol than anyone's joined-up writing could manage, so only a stray mark fails.
MIN_STROKES_PER_SYMBOL = 0.3
MIN_INK_WIDTH = 40
MIN_INK_PER_SYMBOL = 12


@dataclass(frozen=True)
class CalibrationPrompt:
    id: str
    display: str
    reference: str
    captures: str

    @property
    def symbols(self) -> int:
        return len(self.display.replace(" ", ""))

    @property
    def min_strokes(self) -> int:
        return max(2, math.ceil(self.symbols * MIN_STROKES_PER_SYMBOL))


@dataclass(frozen=True)
class CalibrationSet:
    version: int
    prompts: tuple[CalibrationPrompt, ...]

    def prompt(self, prompt_id: str) -> CalibrationPrompt | None:
        return next((prompt for prompt in self.prompts if prompt.id == prompt_id), None)


def load_calibration_set(path: Path = _PROMPTS) -> CalibrationSet:
    data = json.loads(path.read_text())
    return CalibrationSet(data["version"], tuple(CalibrationPrompt(**prompt) for prompt in data["prompts"]))


class SampleRejected(ValueError):
    """Says what to do about it, for showing to the student."""


def check_sample(prompt: CalibrationPrompt, strokes: list[PenStrokeInput]) -> None:
    """Refuse an empty or plainly unfinished sample. A wrong one can't be told from a hard to read one."""

    points = [point for stroke in strokes for point in stroke.points]
    if not points:
        raise SampleRejected("Write the expression first.")
    width = max(point.x for point in points) - min(point.x for point in points)
    ink = sum(
        math.hypot(after.x - before.x, after.y - before.y)
        for stroke in strokes
        for before, after in zip(stroke.points, stroke.points[1:], strict=False)
    )
    if len(strokes) < prompt.min_strokes or width < MIN_INK_WIDTH or ink < MIN_INK_PER_SYMBOL * prompt.symbols:
        raise SampleRejected("That looks unfinished. Try writing the whole expression.")


def reference_image(data_url: str) -> str:
    """Clean an uploaded sample, as for any other image, and shrink it for sending with a reading."""

    clean, _ = sanitise_image(data_url)
    raw = base64.b64decode(clean.split(";base64,", 1)[1])
    with Image.open(io.BytesIO(raw)) as image:
        image.thumbnail((REFERENCE_LONG_EDGE, REFERENCE_LONG_EDGE))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
