"""Draw labelled test images from single-stroke glyphs. This is synthetic data, not handwriting.

It tests that the pipeline works end to end and lets experiments run before real samples
exist. Results from it must never be reported as handwriting accuracy.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

GLYPHS: dict[str, list[list[list[float]]]] = json.loads((Path(__file__).parent / "glyphs.json").read_text())
ADVANCE = 0.92
LINE_GAP = 1.75
SUPERSCRIPT_SCALE = 0.62
MARGIN = 40


@dataclass(frozen=True)
class Style:
    size: int = 56
    jitter: float = 0.02
    slant: float = 0.0
    tightness: float = 1.0
    stroke_width: int = 4
    wobble: float = 0.012


NEAT = Style()
CASUAL = Style(jitter=0.045, slant=0.08, wobble=0.03)
MESSY = Style(jitter=0.09, slant=0.16, tightness=0.84, wobble=0.055, stroke_width=3)
OVERLAPPING = Style(jitter=0.05, slant=0.06, tightness=0.66, wobble=0.03)


def _layout(text: str) -> list[tuple[str, float]]:
    """Split a line into (character, size scale, raised) with `^` applied to the next group."""

    out: list[tuple[str, float]] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "^":
            i += 1
            depth = 0
            start = i
            while i < len(text):
                if text[i] == "(":
                    depth += 1
                elif text[i] == ")":
                    depth -= 1
                i += 1
                if depth == 0 and not (text[i - 1] == "-" and i - 1 == start):
                    break
            out.extend((c, SUPERSCRIPT_SCALE) for c in text[start:i] if not c.isspace())
            continue
        out.append((ch, 1.0))
        i += 1
    return out


def _draw_glyph(draw: ImageDraw.ImageDraw, ch: str, x: float, y: float, size: float, style: Style, rng: random.Random) -> None:
    ox, oy = rng.gauss(0, style.jitter) * size, rng.gauss(0, style.jitter) * size
    for stroke in GLYPHS[ch]:
        points = []
        for px, py in stroke:
            wx = rng.gauss(0, style.wobble) * size
            wy = rng.gauss(0, style.wobble) * size
            points.append((x + ox + wx + (px * 0.8 + (1 - py) * style.slant) * size, y + oy + wy + py * size))
        if len(points) == 1:
            points = points * 2
        draw.line(points, fill="#262626", width=style.stroke_width, joint="curve")


def _line_width(text: str, style: Style) -> float:
    return sum(ADVANCE * style.tightness * style.size * scale * (0.55 if ch == " " else 1) for ch, scale in _layout(text))


def _draw_line(draw: ImageDraw.ImageDraw, text: str, x: float, y: float, style: Style, rng: random.Random) -> float:
    cursor = x
    for ch, scale in _layout(text):
        if ch == " ":
            cursor += style.size * 0.5 * style.tightness
            continue
        if ch not in GLYPHS:
            raise KeyError(f"no glyph for {ch!r} in {text!r}")
        raised = y - style.size * 0.28 if scale < 1 else y
        _draw_glyph(draw, ch, cursor, raised, style.size * scale, style, rng)
        cursor += ADVANCE * style.tightness * style.size * scale
    return cursor


def render(
    lines: list[dict],
    style: Style = NEAT,
    seed: int = 0,
    above: list[str] | None = None,
) -> Image.Image:
    """`lines` entries: {"text": "..."} or {"stack": [top, bottom], "prefix": "= "}, optional "strike": true."""

    rng = random.Random(seed)
    context = [{"text": text} for text in (above or [])]
    everything = context + lines

    def height_units(entry: dict) -> float:
        return 2.8 if "stack" in entry else LINE_GAP

    width = 0.0
    for entry in everything:
        if "stack" in entry:
            width = max(width, _line_width(entry.get("prefix", ""), style) + max(_line_width(t, style) for t in entry["stack"]))
        else:
            width = max(width, _line_width(entry["text"], style))
    height = sum(height_units(entry) for entry in everything) * style.size
    image = Image.new("RGB", (int(width + 2 * MARGIN), int(height + 2 * MARGIN)), "white")
    draw = ImageDraw.Draw(image)

    y = float(MARGIN)
    for entry in everything:
        if "stack" in entry:
            prefix_end = _draw_line(draw, entry.get("prefix", ""), MARGIN, y + style.size * 0.65, style, rng)
            top, bottom = entry["stack"]
            span = max(_line_width(top, style), _line_width(bottom, style))
            _draw_line(draw, top, prefix_end + (span - _line_width(top, style)) / 2, y, style, rng)
            bar_y = y + style.size * 1.22
            draw.line([(prefix_end, bar_y), (prefix_end + span, bar_y + rng.gauss(0, 2))], fill="#262626", width=style.stroke_width)
            _draw_line(draw, bottom, prefix_end + (span - _line_width(bottom, style)) / 2, y + style.size * 1.42, style, rng)
        else:
            end = _draw_line(draw, entry["text"], MARGIN, y, style, rng)
            if entry.get("strike"):
                mid = y + style.size * 0.55
                draw.line([(MARGIN - 8, mid + 6), (end + 8, mid - 8)], fill="#262626", width=style.stroke_width + 1)
                draw.line([(MARGIN - 8, mid - 6), (end + 8, mid + 8)], fill="#262626", width=style.stroke_width + 1)
        y += height_units(entry) * style.size
    return image
