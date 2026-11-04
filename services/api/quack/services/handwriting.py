"""Turn an image of handwriting into structured lines. This module reads; it never judges."""

from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass
from typing import Literal

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict

from quack import prompts
from quack.llm.router import ModelRoute, ModelRouter, Purpose, StructuredResult
from quack.services.math_parser import written_task
from quack.services.math_verifier import LineInput, TaskIntent

MAX_IMAGE_BYTES = 5_000_000
# The app sends at most 2000 x 2000. This also bounds decode time on the server.
MAX_IMAGE_PIXELS = 6_000_000
MAX_LONG_EDGE = 2048
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}

DEVELOPER_PROMPT = prompts.load_prompt(prompts.TRANSCRIBE) + "\n\n" + prompts.load_prompt(prompts.OPERATION)
READING_PROMPT_VERSION = prompts.prompt_version(prompts.TRANSCRIBE, prompts.OPERATION)
# For a notebook, where the app knows how many lines it drew and where they are. Asking only for
# what's used saves output tokens, and those are what set the time. See docs/IPAD_TEST_ROUND_2.md.
COMPACT_PROMPT = prompts.load_prompt(prompts.TRANSCRIBE_COMPACT) + "\n\n" + prompts.load_prompt(prompts.OPERATION)
COMPACT_PROMPT_VERSION = prompts.prompt_version(prompts.TRANSCRIBE_COMPACT, prompts.OPERATION)
# After what's already cached, so that a reading with references shares the same stable start.
WRITER_REFERENCES_PROMPT = prompts.load_prompt(prompts.WRITER_REFERENCES)


@dataclass(frozen=True)
class WriterReference:
    """A sample of the student's own handwriting, and what they were copying when they wrote it."""

    prompt_id: str
    label: str
    image: str


class ImageRejected(ValueError):
    """The upload is not an image we are willing to process."""


class ReadLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str
    math: str
    kind: Literal["math", "prose", "crossed_out", "incomplete"]
    legibility: Literal["clear", "unsure", "illegible"]
    alternatives: list[str]
    continues_previous: Literal["yes", "no", "unknown"]
    operation: Literal[
        "expand", "simplify", "factor", "combine_like_terms", "arithmetic",
        "differentiate", "solve_equation", "other", "unknown",
    ]
    y_top: float
    y_bottom: float


class HandwritingReading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[ReadLine]


class CompactLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    math: str
    kind: Literal["math", "prose", "crossed_out", "incomplete"]
    legibility: Literal["clear", "unsure", "illegible"]
    alternatives: list[str]
    continues_previous: Literal["yes", "no", "unknown"]
    operation: Literal[
        "expand", "simplify", "factor", "combine_like_terms", "arithmetic",
        "differentiate", "solve_equation", "other", "unknown",
    ]


class CompactReading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[CompactLine]


def sanitise_image(data_url: str) -> tuple[str, str]:
    """Validate an uploaded image and re-encode it, returning (data_url, sha256).

    Re-encoding drops metadata such as location, and means the model only sees pixels
    that Pillow could actually decode.
    """

    if not data_url.startswith("data:image/") or ";base64," not in data_url:
        raise ImageRejected("expected a base64 image data URL")
    encoded = data_url.split(";base64,", 1)[1]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ImageRejected("invalid base64") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageRejected("image too large")

    try:
        with Image.open(io.BytesIO(raw)) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise ImageRejected(f"unsupported format {probe.format}")
            width, height = probe.size
            if width * height > MAX_IMAGE_PIXELS:
                raise ImageRejected("image has too many pixels")
            probe.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            if image.mode in ("RGBA", "LA", "P"):
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, "white")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                image = flattened
            else:
                image = image.convert("RGB")
            if max(image.size) > MAX_LONG_EDGE:
                image.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", optimize=True)
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        if isinstance(exc, ImageRejected):
            raise
        raise ImageRejected("could not decode image") from exc

    clean = buffer.getvalue()
    return "data:image/png;base64," + base64.b64encode(clean).decode(), hashlib.sha256(clean).hexdigest()


async def read_lines(
    router: ModelRouter,
    image: str,
    line_count: int,
    *,
    purpose: Purpose = Purpose.TRANSCRIBE,
    route: ModelRoute | None = None,
    session_id: str = "",
    run_id: str = "runtime",
    references: tuple[WriterReference, ...] = (),
) -> StructuredResult[HandwritingReading]:
    """Read an image whose line count is known, asking only for what the verifier uses.

    With `references`, it's first shown how this writer forms their symbols. See writer_references.py.
    """

    noun = "line" if line_count == 1 else "lines"
    content: list[dict] = []
    for number, reference in enumerate(references, 1):
        content.append({"type": "text", "text": f"Reference sample {number} from this writer. It shows: {reference.label}"})
        content.append({"type": "image_url", "image_url": {"url": reference.image}})
    lead = "Now read the handwriting in this last image, and only this one." if references else None
    lead = lead or "Read the handwriting in this image."
    content.append({"type": "text", "text": f"{lead} It has {line_count} written {noun}."})
    content.append({"type": "image_url", "image_url": {"url": image}})
    instructions = f"{COMPACT_PROMPT}\n\n{WRITER_REFERENCES_PROMPT}" if references else COMPACT_PROMPT
    messages = [
        {"role": "developer", "content": instructions},
        {"role": "user", "content": content},
    ]
    result = await router.complete_json(
        purpose, messages, CompactReading, session_id=session_id, run_id=run_id, route=route
    )
    # Where a line is comes from the ink, so its height is left at nothing rather than estimated.
    lines = [ReadLine(raw_text=line.math, y_top=0, y_bottom=0, **line.model_dump()) for line in result.value.lines]
    return StructuredResult(HandwritingReading(lines=lines), result.record)


async def read_handwriting(
    router: ModelRouter,
    image: str,
    *,
    page_context: str | None = None,
    purpose: Purpose = Purpose.TRANSCRIBE,
    route: ModelRoute | None = None,
    session_id: str = "",
    run_id: str = "runtime",
) -> StructuredResult[HandwritingReading]:
    instruction = "Read the handwriting in this image."
    content: list[dict] = [{"type": "text", "text": instruction}]
    content.append({"type": "image_url", "image_url": {"url": image}})
    if page_context is not None:
        content.append(
            {
                "type": "text",
                "text": "This second image shows the surrounding page, for context only. "
                "Report lines from the first image only.",
            }
        )
        content.append({"type": "image_url", "image_url": {"url": page_context}})
    messages = [
        # Stable instructions first, so that automatic prefix caching applies.
        {"role": "developer", "content": DEVELOPER_PROMPT},
        {"role": "user", "content": content},
    ]
    return await router.complete_json(
        purpose, messages, HandwritingReading, session_id=session_id, run_id=run_id, route=route
    )


# What a practice line that copies out the question is read as. The same for every reading, so two readings that
# each see the question copied out agree on it, whichever digit each thought it saw.
RESTATED_QUESTION = "(the question, copied out)"


def to_line_inputs(
    reading: HandwritingReading,
    posed: bool = False,
    *,
    intents: list[TaskIntent] | None = None,
    page_intent: TaskIntent = "auto",
    given: str = "",
    values: tuple[tuple[str, str], ...] = (),
) -> list[LineInput]:
    """Keep what the verifier should consider, in reading order. For the rest, see `LineInput`.

    `intents` runs alongside `reading.lines`. A line of words that is only a task, like "Simplify",
    says what the next line is doing, in place of the model's label on that line.
    """

    lines: list[LineInput] = []
    after_prose = False
    stated: TaskIntent = "auto"
    for position, line in enumerate(reading.lines):
        if line.kind == "crossed_out":
            continue
        if line.kind == "prose" and line.raw_text == RESTATED_QUESTION:
            # The question copied out (see `practice.restates_the_question`): what's under it follows from the
            # question as Quack set it, just as if the copy weren't there.
            continue
        if line.kind == "prose":
            # Could be "hence", or could be "Question 2". Words between lines mean the model's
            # claim that they continue can't justify an accusation.
            after_prose = True
            stated = written_task(line.raw_text or line.math) or stated  # type: ignore[assignment]
            continue
        continues = line.continues_previous
        if after_prose and continues == "yes":
            continues = "unknown"
        after_prose = False
        lines.append(
            LineInput(
                index=len(lines),
                math=line.math,
                legible=line.legibility == "clear",
                continues=continues,
                operation=line.operation,
                alternatives=tuple(line.alternatives),
                incomplete=line.kind == "incomplete",
                posed=posed,
                intent=(intents[position] if intents and intents[position] != "auto" else stated),
                page_intent=page_intent,
                given=given if not lines else "",
                values=values,
            )
        )
        stated = "auto"
    return lines
