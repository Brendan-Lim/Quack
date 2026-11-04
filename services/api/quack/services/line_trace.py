"""Why a written line was or wasn't flagged, for development only.

A mistake can go missing at any stage between the ink and the duck, and from the outside they all
look the same. This says which stage it was, line by line. It's kept in memory by the API when
QUACK_ENV=development, shown in the debug panel, and never written to disk or sent in production.
"""

from __future__ import annotations

from dataclasses import dataclass

from quack.schemas import InkLine, LineReadingTrace, LineTrace
from quack.services.handwriting import HandwritingReading, ReadLine
from quack.services.math_verifier import StepResult, VerificationReport, reads_the_same

READING_REASONS = {
    "low_legibility", "illegible_line", "alternative_reading_is_valid", "incomplete_line", "reading_contested",
}


@dataclass(frozen=True)
class TraceInput:
    version: int
    ink_lines: list[InkLine] | None
    first: HandwritingReading | None
    second: HandwritingReading | None
    page: HandwritingReading
    report: VerificationReport
    disputed: frozenset[str]
    acknowledged: frozenset[str]
    new: frozenset[str]
    resolved: frozenset[str]
    remembered: frozenset[str]
    settled: frozenset[str]


def _reading_of(line: ReadLine | None) -> LineReadingTrace | None:
    if line is None:
        return None
    return LineReadingTrace(math=line.math, kind=line.kind, legibility=line.legibility, alternatives=line.alternatives)


def _same(one: ReadLine | None, other: ReadLine | None) -> bool:
    """As the pipeline judges it. `(x^2)/(2)` and `x^2/2` were once traced as a disagreement they weren't."""

    if one is None or other is None or one.math.replace(" ", "") == other.math.replace(" ", ""):
        return True
    return reads_the_same(one.math, other.math)


def _disposition(
    line: ReadLine,
    steps: list[StepResult],
    first: ReadLine | None,
    second: ReadLine | None,
    data: TraceInput,
    shown: set[str],
) -> str:
    wrong = [step for step in steps if step.status == "invalid"]
    if wrong:
        fingerprint = wrong[0].fingerprint or ""
        if fingerprint in data.disputed:
            return "transcription_disagreement"
        if fingerprint in data.resolved:
            return "stale_or_invalidated"
        if fingerprint in shown:
            return "suppressed_duplicate"
        shown.add(fingerprint)
        if fingerprint in data.acknowledged:
            return "known_mistake_active"
        return "confirmed_new_mistake" if fingerprint in data.new else "verification_invalid"
    if not _same(first, second):
        return "transcription_disagreement"
    if line.kind == "prose":
        return "not_mathematics"
    if line.kind == "crossed_out":
        return "crossed_out"
    if line.kind == "incomplete" or line.legibility != "clear":
        return "transcription_uncertain"
    if any(step.status == "unsupported" or step.operation_type in ("reading", "notation") for step in steps):
        return "parse_unsupported"
    if any(step.reason in READING_REASONS for step in steps):
        return "transcription_uncertain"
    if any(step.status == "uncertain" for step in steps):
        return "verification_uncertain"
    return "verification_valid" if steps else "not_checked"


def trace_lines(data: TraceInput) -> list[LineTrace]:
    """One entry per line in the check. A line with no id couldn't be tied to its ink."""

    lines = data.page.lines
    drawn = [ink for ink in data.ink_lines or [] if ink.in_image]
    # The readings only cover what was drawn, in order. Anything else came from memory.
    readings_of = {ink.id: index for index, ink in enumerate(drawn)}
    ink_at = list(data.ink_lines) if data.ink_lines and len(data.ink_lines) == len(lines) else [None] * len(lines)
    considered = [index for index, line in enumerate(lines) if line.kind not in ("prose", "crossed_out")]
    steps_at: dict[int, list[StepResult]] = {}
    for step in data.report.steps:
        if step.line < len(considered):
            steps_at.setdefault(considered[step.line], []).append(step)

    def nth(reading: HandwritingReading | None, ink: InkLine | None, position: int) -> ReadLine | None:
        if reading is None:
            return None
        index = readings_of.get(ink.id) if ink is not None else position
        return reading.lines[index] if index is not None and index < len(reading.lines) else None

    shown: set[str] = set()
    traces = []
    for position, line in enumerate(lines):
        ink = ink_at[position]
        steps = steps_at.get(position, [])
        first, second = nth(data.first, ink, position), nth(data.second, ink, position)
        wrong = next((step for step in steps if step.status == "invalid"), None)
        reason = next((step.reason for step in steps if step.reason), None)
        statuses = [step.status for step in steps]
        traces.append(
            LineTrace(
                line_id=ink.id if ink else None,
                position=position,
                snapshot_version=data.version,
                in_image=ink.in_image if ink else True,
                remembered=ink.id in data.remembered if ink else False,
                first=_reading_of(first),
                second=_reading_of(second),
                used=line.math,
                settled=ink.id in data.settled if ink else False,
                verdict=next((s for s in ("invalid", "uncertain", "unsupported", "valid") if s in statuses), None),
                reason=reason,
                parsed_operation=wrong.parsed_operation if wrong else None,
                mistake_id=f"m_{wrong.fingerprint}" if wrong and wrong.fingerprint else None,
                disposition=_disposition(line, steps, first, second, data, shown),
            )
        )
    return traces
