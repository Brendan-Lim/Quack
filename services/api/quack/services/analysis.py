"""The detection pipeline: read, verify, confirm, then decide whether to quack."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field, replace

from quack.config import Settings
from quack.llm.router import ModelError, ModelRouter, Purpose, StructuredResult
from quack.schemas import (
    AnalysisStatus,
    AnalyzeRequest,
    AnalyzeResponse,
    AskRequest,
    CallTiming,
    Clarification,
    InkLine,
    Intervention,
    LineTrace,
    Mistake,
    ModelIssue,
    ReadLineSummary,
    Region,
    Timings,
)
from quack.services.ask import SelectionFinding
from quack.services.handwriting import (
    COMPACT_PROMPT_VERSION,
    READING_PROMPT_VERSION,
    HandwritingReading,
    ReadLine,
    WriterReference,
    read_handwriting,
    read_lines,
    sanitise_image,
    to_line_inputs,
)
from quack.services.line_trace import TraceInput, trace_lines
from quack.services.math_parser import MathParseError, parse_statement
from quack.services.math_verifier import (
    LineInput,
    StepResult,
    TaskIntent,
    VerificationReport,
    is_a_claim_on_its_own,
    is_a_sum,
    plausible_alternatives,
    reads_the_same,
)
from quack.services.mistake_manager import MistakeManager, TrackedMistake
from quack.services.verifier_pool import VerificationFailed, shared_pool
from quack.services.writer_references import WriterProfile, distrust_copies, references_tag, select_references

log = logging.getLogger("quack.analysis")

STATUS_BY_VERDICT: dict[str, AnalysisStatus] = {
    "valid": "no_error",
    "invalid": "confirmed_error",
    "uncertain": "uncertain",
    "unsupported": "unsupported",
}

MODEL_ISSUE_MESSAGES = {
    "no_api_key": "Quack isn't connected to Meta's API yet.",
    "auth": "Meta's API rejected Quack's key.",
    "rate_limited": "Meta's API is busy right now.",
    "timeout": "Meta's API took too long to answer.",
    "network": "Quack can't reach Meta's API.",
    "server_error": "Meta's API is having trouble.",
    "budget_exceeded": "Quack has reached its usage limit.",
    "invalid_output": "Quack couldn't understand the model's answer.",
    "truncated": "Quack couldn't finish reading this page.",
    "invalid_request": "Meta's API refused the request.",
}


MAX_TRACES_PER_SESSION = 40
MAX_TRACED_SESSIONS = 20
MAX_DOUBTS_REMEMBERED = 1000
MAX_FAILED_READINGS_REMEMBERED = 200
# Kinds of failure that say a reading of this image ran out of room, not that the model can't be reached.
# Another call is worth making for these, and for nothing else.
READING_RAN_OUT = ("truncated", "timeout", "invalid_output")

# A reading before anything is judged by it. Practice uses this to put the question in front of an answer.
ReadingFilter = Callable[[HandwritingReading], HandwritingReading]


def model_issue(error: ModelError) -> ModelIssue:
    return ModelIssue(
        category=error.category, message=MODEL_ISSUE_MESSAGES.get(error.category, "Quack can't check your work right now.")
    )


def classify(reading: HandwritingReading, report: VerificationReport) -> tuple[AnalysisStatus, str | None]:
    """The final verdict for one reading. Used by the product and by evals, so they agree."""

    if not reading.lines:
        return "uncertain", "nothing_readable"
    if report.status == "valid" and not to_line_inputs(reading):
        return "unsupported", "no_mathematics"
    return STATUS_BY_VERDICT[report.status], _abstain_reason(report)


@dataclass
class _Timer:
    started: float = field(default_factory=time.perf_counter)
    interpret_ms: float = 0
    verify_ms: float = 0
    model_requests: int = 0
    cost_usd: float = 0
    cache_hit: bool = False
    stages: dict[str, float] = field(default_factory=dict)
    calls: list[CallTiming] = field(default_factory=list)
    # Everything on this page answers a question Quack posed. It travels with the check, like the rest.
    posed: bool = False
    # What the student has said this page and each line are for, and what the practice question is for.
    page_intent: TaskIntent = "auto"
    line_intents: dict[str, str] = field(default_factory=dict)
    forced_intent: TaskIntent = "auto"
    # An equation Quack set, standing above what's written. See `LineInput.given`.
    given: str = ""
    # The values a practice question gives its letters. See `LineInput.values`.
    values: tuple[tuple[str, str], ...] = ()
    # What a practice question puts in front of the page, applied again once the page is laid out.
    fill: ReadingFilter | None = None
    # What became of the second reading, for the development trace.
    second_reading: str = "none"
    # The lines holding each known mistake, by fingerprint. Empty for one out of view, so it's known
    # not to be here, and missing for one the client couldn't tie to any line.
    mistake_lines: dict[str, frozenset[str]] = field(default_factory=dict)
    # Lines whose ink has been read as different answers, with those answers. See `LineReadings`.
    contested: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Lines whose remembered reading was used though this check read the same ink as another answer.
    overruled: set[str] = field(default_factory=set)

    def stage(self, name: str) -> None:
        self.stages[name] = round((time.perf_counter() - self.started) * 1000, 1)


class LineMemory:
    """What two readings agreed each written line says, by session.

    A line's id comes from its strokes, so a remembered reading can only ever be about that exact
    ink. Lines that haven't changed aren't drawn or read again, which is most of what makes a check
    on a full page as quick as one on an empty page. Only agreed readings are kept, since whatever
    is kept here is what later lines get checked against.
    """

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._lines: OrderedDict[tuple[str, str], ReadLine] = OrderedDict()

    def get(self, session_id: str, line_id: str) -> ReadLine | None:
        key = (session_id, line_id)
        line = self._lines.get(key)
        if line is not None:
            self._lines.move_to_end(key)
        return line

    def put(self, session_id: str, line_id: str, line: ReadLine) -> None:
        self._lines[(session_id, line_id)] = line
        self._lines.move_to_end((session_id, line_id))
        while len(self._lines) > self._capacity:
            self._lines.popitem(last=False)


class LineReadings:
    """Each different answer each written line has been read as, by session.

    Two readings agreeing in one check isn't the whole story. On 2026-11-02 the same ink was read as
    `d/dx(sin(x))=Con(x)`, then as `cos(x)` by both readings, then as `con(x)` by both again. The
    right answer being one of the readings is no reason to believe it. So once any reading, in an
    earlier check, has said a line's ink is a different answer to the same question, the line can't be
    called right until it's written again. Within the check that first reads it two ways, that's
    ordinary disagreement, handled as it always was. A mistake still needs two readings that agree.
    """

    PER_LINE = 6

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._lines: OrderedDict[tuple[str, str], list[str]] = OrderedDict()

    def add(self, session_id: str, line_id: str, maths: Iterable[str]) -> None:
        key = (session_id, line_id)
        seen = self._lines.setdefault(key, [])
        for math in maths:
            if math and not any(_same_answer(math, other) for other in seen):
                seen.append(math)
        del seen[: -self.PER_LINE]
        self._lines.move_to_end(key)
        while len(self._lines) > self._capacity:
            self._lines.popitem(last=False)

    def answers(self, session_id: str, line_id: str) -> tuple[str, ...]:
        return tuple(self._lines.get((session_id, line_id), ()))

    def contested(self, session_id: str, line_id: str, earlier: tuple[str, ...]) -> tuple[str, ...]:
        """The answers this ink has been read as, if an earlier check read it as a different one."""

        now = self.answers(session_id, line_id)
        if not any(_another_answer(old, new) for old in earlier for new in now):
            return ()
        return tuple(math for math in now if any(_another_answer(math, other) for other in now))


def _same_answer(one: str, other: str) -> bool:
    """Spacing, `C` for `c` and `O` for `0` are how a line was spelled, not what it says."""

    if one.replace(" ", "").casefold() == other.replace(" ", "").casefold():
        return True
    return reads_the_same(one, other) and reads_the_same(other, one)


def _another_answer(one: str, other: str) -> bool:
    """`d/dx(sin(x)) = cos(x)` and `d/dx(sin(x)) = con(x)`: the same question, and a different answer.

    A reading that lost part of the question, like `x dx = ...` for `∫ x dx = ...`, is ordinary
    disagreement, which another look can settle. Only the parser sees the text.
    """

    if _same_answer(one, other):
        return False
    try:
        first, second = parse_statement(one), parse_statement(other)
    except (MathParseError, ValueError, TypeError, RecursionError):
        return False
    return (
        first.kind == second.kind
        and len(first.parts) >= 2
        and len(second.parts) >= 2
        and first.parts[0] == second.parts[0]
        and first.parts[1:] != second.parts[1:]
    )


@dataclass
class _Page:
    """A reading laid out against the ink, with the id of the written line behind each entry."""

    reading: HandwritingReading
    line_ids: list[str | None]

    def line_id_of(self, step: StepResult) -> str | None:
        return self.line_id_at(step.line)

    def line_id_at(self, position: int | None) -> str | None:
        """`position` counts the lines the verifier was given, which leaves out words and crossings out."""

        considered = [
            line_id
            for line, line_id in zip(self.reading.lines, self.line_ids, strict=True)
            if line.kind not in ("prose", "crossed_out")
        ]
        return considered[position] if position is not None and position < len(considered) else None


def _same_text(first: ReadLine, second: ReadLine) -> bool:
    """`sin x` and `sin(x)` are two spellings of one thing, so they're agreement."""

    if first.kind != second.kind:
        return False
    return first.math.replace(" ", "") == second.math.replace(" ", "") or reads_the_same(first.math, second.math)


def _agree(first: ReadLine, second: ReadLine) -> bool:
    """Do two readings say the same thing, with no doubt from either?"""

    return (
        _same_text(first, second)
        and first.legibility == second.legibility == "clear"
        and not first.alternatives
        and not second.alternatives
    )


def _both_crossed_out(first: ReadLine, second: ReadLine) -> bool:
    """Two readings that each see the line struck through agree on what matters about it: it isn't working.

    What's under the strike is hard to read, and doesn't need to be: the verifier leaves crossed-out lines out.
    On the iPad on 2026-11-04 (evening), a struck-through `= 3x + 34` was seen as crossed out by both readings of
    three checks, read as `3x+34`, `5x+34` or illegible, so it was never "read", and a right `= 3x + 50` under it
    couldn't finish the question until it was rubbed out.
    """

    return first.kind == second.kind == "crossed_out"


@dataclass
class _Pending:
    task: asyncio.Task[StructuredResult[HandwritingReading]]
    # Whether some analysis has counted this call as its own. Another that waits on it shares it.
    claimed: bool = False
    began: float = field(default_factory=time.monotonic)


class AnalysisService:
    def __init__(self, settings: Settings, router: ModelRouter, mistakes: MistakeManager) -> None:
        self.settings = settings
        self.router = router
        self.mistakes = mistakes
        self._readings: OrderedDict[str, HandwritingReading] = OrderedDict()
        # Readings under way. The same image asked for again while one runs waits for it, rather
        # than paying for it again: repeated presses of Check my work once ran eight at a time.
        self._pending: dict[str, _Pending] = {}
        self._memory = LineMemory(settings.line_memory_size)
        self._answers = LineReadings(settings.line_memory_size)
        # Second readings that ran out of room on this exact image. Asked again, they would again.
        self._ran_out: OrderedDict[str, str] = OrderedDict()
        # Why each line was or wasn't flagged, by session, in memory and in development only.
        self._traces: OrderedDict[str, deque[dict[str, object]]] = OrderedDict()
        # What the lines that didn't settle might say, by session, for choosing references to show.
        self._doubts: OrderedDict[str, list[str]] = OrderedDict()

    def _personalization(self, request: AnalyzeRequest) -> str:
        override = request.personalization if self.settings.is_development else None
        return override or self.settings.personalization

    def wants_writer(self, request: AnalyzeRequest) -> bool:
        """Will this check be shown the student's own handwriting? If not, there's no need to load it.

        The usual first look has none of the cost, so the fast path stays as it was. Lines that didn't
        settle get another look anyway, and that's when knowing how this student writes can help. A
        student asking directly gets it too, as what they're asking about may be a shared misreading.
        """

        mode = self._personalization(request)
        if mode == "off" or not request.ink_lines:
            return False
        return mode == "always" or request.attempt > 0 or request.trigger == "manual"

    def _references_for(self, request: AnalyzeRequest, writer: WriterProfile | None) -> tuple[WriterReference, ...]:
        if writer is None or not self.wants_writer(request):
            return ()
        doubted = self._doubts.get(request.session_id, [])
        return select_references(writer.samples, doubted, self.settings.references_per_reading)

    def _note_doubts(
        self,
        session_id: str,
        readings: Iterable[HandwritingReading | None],
        ink_lines: list[InkLine],
        settled: Iterable[str],
    ) -> None:
        agreed = set(settled)
        drawn = [line for line in ink_lines if line.in_image]
        texts: list[str] = []
        for reading in readings:
            if reading is None or len(reading.lines) != len(drawn):
                continue
            for ink, line in zip(drawn, reading.lines, strict=True):
                if ink.id not in agreed and line.kind in ("math", "incomplete"):
                    texts.extend(text for text in (line.math, *line.alternatives) if text)
        self._doubts[session_id] = list(dict.fromkeys(texts))
        self._doubts.move_to_end(session_id)
        while len(self._doubts) > MAX_DOUBTS_REMEMBERED:
            self._doubts.popitem(last=False)

    def _note_answers(
        self, session_id: str, readings: Iterable[HandwritingReading | None], ink_lines: list[InkLine] | None, timer: _Timer
    ) -> None:
        """Keep what each drawn line was read as, and note which lines have been read as different answers."""

        if not ink_lines:
            return
        earlier = {ink.id: self._answers.answers(session_id, ink.id) for ink in ink_lines}
        drawn = [line for line in ink_lines if line.in_image]
        for reading in readings:
            if reading is None or len(reading.lines) != len(drawn):
                continue
            for ink, line in zip(drawn, reading.lines, strict=True):
                if line.kind == "math":
                    self._answers.add(session_id, ink.id, (line.math,))
        for ink in ink_lines:
            answers = self._answers.contested(session_id, ink.id, earlier[ink.id])
            if answers:
                timer.contested[ink.id] = answers

    def _misread(
        self, session_id: str, gone: list[str], scopes: dict[str, list[str]], timer: _Timer
    ) -> frozenset[str]:
        """Known mistakes whose own ink has since been read as another answer.

        Ink read as two answers is neither right nor wrong, so an alert on it is withdrawn. Its line's
        history must hold the mistake itself, so it's the same ink, and not a rewrite of it read two
        ways, which would otherwise count as a fix. The client sees the ink hasn't changed, and says so.
        """

        found = set()
        for mistake_id in gone:
            tracked = self.mistakes.get_mistake(session_id, mistake_id)
            if tracked is None or not tracked.previous_step:
                continue
            claim = f"{tracked.previous_step} = {tracked.current_step}"
            answers = [answer for line_id in scopes.get(mistake_id, ()) for answer in timer.contested.get(line_id, ())]
            if any(_same_answer(answer, claim) for answer in answers):
                found.add(mistake_id)
        return frozenset(found)

    def traces(self, session_id: str) -> list[dict[str, object]]:
        return list(self._traces.get(session_id, ()))

    def _keep_trace(self, session_id: str, entry: dict[str, object]) -> None:
        self._traces.setdefault(session_id, deque(maxlen=MAX_TRACES_PER_SESSION)).append(entry)
        self._traces.move_to_end(session_id)
        while len(self._traces) > MAX_TRACED_SESSIONS:
            self._traces.popitem(last=False)

    def _key(self, digest: str, purpose: Purpose, label: str, line_count: int | None) -> str:
        route = self.router.routes[purpose]
        version = READING_PROMPT_VERSION if line_count is None else f"{COMPACT_PROMPT_VERSION}:{line_count}"
        # The label keeps readings apart, so a confirmation can never be answered by the cached
        # reading it's supposed to be checking, even if two purposes share a route.
        return f"{digest}:{version}:{route.model}:{route.reasoning_effort}:{label}"

    async def _read_with_references(
        self, image: str, line_count: int, purpose: Purpose, session_id: str, references: tuple[WriterReference, ...]
    ) -> StructuredResult[HandwritingReading]:
        try:
            result = await read_lines(
                self.router, image, line_count, purpose=purpose, session_id=session_id, references=references
            )
        except ModelError as exc:
            if exc.category != "invalid_request":
                raise
            # Refused because of what's been added, perhaps. The reading the check would have had is still there.
            log.warning("references_refused", extra={"purpose": str(purpose)})
            return await read_lines(self.router, image, line_count, purpose=purpose, session_id=session_id)
        return StructuredResult(distrust_copies(result.value, references), result.record)

    def _start(
        self,
        image: str,
        digest: str,
        purpose: Purpose,
        session_id: str,
        label: str,
        line_count: int | None = None,
        references: tuple[WriterReference, ...] = (),
    ) -> _Pending | None:
        """Begin a reading unless it's cached or already under way. Returns the one under way."""

        key = self._key(digest, purpose, label, line_count)
        if key in self._readings:
            return None
        pending = self._pending.get(key)
        if pending is None:
            if line_count is None:
                reading = read_handwriting(self.router, image, purpose=purpose, session_id=session_id)
            elif references:
                reading = self._read_with_references(image, line_count, purpose, session_id, references)
            else:
                reading = read_lines(self.router, image, line_count, purpose=purpose, session_id=session_id)
            task = asyncio.ensure_future(reading)
            pending = self._pending[key] = _Pending(task)
            task.add_done_callback(lambda done: self._finished(key, done))
        return pending

    def _finished(self, key: str, task: asyncio.Task[StructuredResult[HandwritingReading]]) -> None:
        # Kept even if nobody is waiting any more, so a confirmation started early isn't wasted.
        self._pending.pop(key, None)
        if task.cancelled() or task.exception() is not None:
            return
        self._readings[key] = task.result().value
        while len(self._readings) > self.settings.reading_cache_size:
            self._readings.popitem(last=False)

    async def _read(
        self,
        image: str,
        digest: str,
        purpose: Purpose,
        session_id: str,
        timer: _Timer,
        label: str = "first",
        started: _Pending | None = None,
        line_count: int | None = None,
        references: tuple[WriterReference, ...] = (),
    ) -> HandwritingReading:
        """`started` is a reading this analysis began earlier. It's used even if it has already
        failed, so a failure is reported rather than paid for a second time."""

        route = self.router.routes[purpose]
        pending = started or self._start(image, digest, purpose, session_id, label, line_count, references)
        if pending is None:
            key = self._key(digest, purpose, label, line_count)
            self._readings.move_to_end(key)
            timer.cache_hit = True
            timer.calls.append(CallTiming(purpose=str(purpose), effort=route.reasoning_effort, ms=0, cached=True))
            return self._readings[key]

        shared = pending.claimed
        pending.claimed = True
        waiting = time.perf_counter()
        try:
            # Shielded, so a request that gives up can't cancel a reading another is waiting for.
            result = await asyncio.shield(pending.task)
        finally:
            timer.interpret_ms += (time.perf_counter() - waiting) * 1000
        waited = round((time.perf_counter() - waiting) * 1000, 1)
        if shared:
            timer.cache_hit = True
            timer.calls.append(CallTiming(purpose=str(purpose), effort=route.reasoning_effort, ms=waited, cached=True))
            return result.value
        record = result.record
        timer.calls.append(
            CallTiming(
                purpose=str(purpose),
                effort=route.reasoning_effort,
                ms=waited,
                attempts=record.attempt,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                reasoning_tokens=record.reasoning_tokens,
                cost_usd=round(record.estimated_usd, 6),
                references=len(references),
            )
        )
        timer.model_requests += 1
        timer.cost_usd += record.estimated_usd
        return result.value

    def _stand_in_label(self, label: str) -> str:
        return label.replace("confirm", "stand_in", 1)

    def _second_is_owed(self, digest: str, line_count: int | None, label: str) -> bool:
        """Not if this reading has already run out of room on this image, as asked again it would again,
        or a stand-in has already read it."""

        stand_in = self._key(digest, Purpose.TRANSCRIBE, self._stand_in_label(label), line_count)
        key = self._key(digest, Purpose.TRANSCRIBE_CONFIRM, label, line_count)
        return key in self._readings or (key not in self._ran_out and stand_in not in self._readings)

    def _note_ran_out(self, digest: str, line_count: int | None, label: str, category: str) -> None:
        if not self.settings.reading_cache_size:
            return
        self._ran_out[self._key(digest, Purpose.TRANSCRIBE_CONFIRM, label, line_count)] = category
        while len(self._ran_out) > MAX_FAILED_READINGS_REMEMBERED:
            self._ran_out.popitem(last=False)

    async def _second_reading(
        self,
        image: str,
        digest: str,
        session_id: str,
        timer: _Timer,
        label: str,
        started: _Pending | None,
        line_count: int | None,
        references: tuple[WriterReference, ...],
    ) -> tuple[HandwritingReading, bool]:
        """The independent reading, and whether it's the one meant or a stand-in for it.

        At `low` effort, hard lines can use up all their tokens or time thinking and return nothing:
        all six failed calls of the 2026-11-02 iPad session were this, all on calculus, and asking
        again ran out again. Without a second reading no alert can stand, so a line could wait for ever
        on a reading that will never come. So after a wait, another reading at the first look's effort
        goes alongside it. It's still a separate reading that must agree, and the first to finish is
        used. It's the same effort as the first look, so it could share a misreading more easily. For
        that reason it gets none of `_reconciled`'s allowance: it and the first must both be clear.
        """

        key = self._key(digest, Purpose.TRANSCRIBE_CONFIRM, label, line_count)
        category = self._ran_out.get(key)
        wanted = None if not self._second_is_owed(digest, line_count, label) else asyncio.ensure_future(
            self._read(
                image, digest, Purpose.TRANSCRIBE_CONFIRM, session_id, timer,
                label=label, started=started, line_count=line_count, references=references,
            )
        )
        if wanted is not None:
            wanted.add_done_callback(_retrieve)
            # From when it was asked for, alongside the first reading, not from when that finished. A slow
            # first look used to put the stand-in off by as long again: 25 s in on 2026-11-02.
            waited = time.monotonic() - started.began if started is not None else 0.0
            await asyncio.wait({wanted}, timeout=max(0.0, self.settings.second_reading_patience_s - waited))
            if wanted.done():
                failure = wanted.exception()
                if failure is None:
                    timer.second_reading = "read"
                    return wanted.result(), False
                if not isinstance(failure, ModelError) or failure.category not in READING_RAN_OUT:
                    timer.second_reading = f"failed_{getattr(failure, 'category', 'unknown')}"
                    raise failure
                category = failure.category
                self._note_ran_out(digest, line_count, label, category)
                wanted = None
        stand_in = asyncio.ensure_future(
            self._read(
                image, digest, Purpose.TRANSCRIBE, session_id, timer,
                label=self._stand_in_label(label), line_count=line_count, references=references,
            )
        )
        stand_in.add_done_callback(_retrieve)
        waiting = {stand_in} | ({wanted} if wanted is not None else set())
        last_failure: BaseException | None = None
        while waiting:
            done, waiting = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
            for task in (wanted, stand_in):
                if task not in done:
                    continue
                failure = task.exception()
                if failure is None:
                    is_stand_in = task is stand_in
                    timer.second_reading = f"stand_in_after_{category or 'waiting'}" if is_stand_in else "read_late"
                    return task.result(), is_stand_in
                if task is wanted and isinstance(failure, ModelError) and failure.category in READING_RAN_OUT:
                    category = failure.category
                    self._note_ran_out(digest, line_count, label, category)
                last_failure = failure
        timer.second_reading = f"failed_{getattr(last_failure, 'category', 'unknown')}"
        assert last_failure is not None
        raise last_failure

    async def _verify(
        self, page: _Page, acknowledged: frozenset[str], timer: _Timer
    ) -> tuple[VerificationReport, frozenset[str]]:
        started = time.perf_counter()
        intents: list[TaskIntent] = [
            timer.forced_intent if timer.forced_intent != "auto" else timer.line_intents.get(line_id or "", "auto")  # type: ignore[misc]
            for line_id in page.line_ids
        ]
        lines = to_line_inputs(
            page.reading, posed=timer.posed, intents=intents, page_intent=timer.page_intent, given=timer.given,
            values=timer.values,
        )
        try:
            # The verifier knows mistakes by their maths. Which copy of one is which is decided here.
            originals = frozenset(_original_of(fingerprint) for fingerprint in acknowledged)
            report, where = await shared_pool.verify(lines, originals, self.settings.verify_timeout_s)
        except VerificationFailed as exc:
            failed = StepResult("uncertain", "reading", 0, reason=f"verification_{exc}")
            return VerificationReport("uncertain", (failed,)), acknowledged
        finally:
            timer.verify_ms += (time.perf_counter() - started) * 1000
        report = _not_called_right(
            _as_copies(report, page, acknowledged, timer.mistake_lines), page, timer.contested, timer.overruled
        )
        return report, _still_there(page, acknowledged, where, timer.mistake_lines)

    async def _reconciled(
        self, first: HandwritingReading, second: HandwritingReading
    ) -> tuple[HandwritingReading, HandwritingReading]:
        """Settle what two readings of a wrong sum have between them.

        A sum has no working to go wrong, just digits. If one reading is sure of them and the other
        says exactly the same with a vaguer doubt, that's agreement. Handwritten zeros got `unsure`
        often enough for 3 * 4 = 10 to go unreported. A doubt that names another digit still counts,
        two unsure readings still say nothing, and this only ever lets a mistake be seen: telling a
        student they've fixed something still takes two clear readings.

        A derivative or an integral with its answer is the same, as it says all it needs to on one line
        and is true or false as it stands. `3x + 2x = 5` isn't: what it means depends on what it's for.
        """

        if len(first.lines) != len(second.lines):
            return first, second
        lines = [list(first.lines), list(second.lines)]
        for position, (one, other) in enumerate(zip(first.lines, second.lines, strict=True)):
            says_it_all = is_a_sum(one.math) or is_a_claim_on_its_own(one.math)
            if not (one.kind == other.kind == "math" and says_it_all and reads_the_same(one.math, other.math)):
                continue
            legibility = (one.legibility, other.legibility)
            if "clear" not in legibility or "illegible" in legibility or not await self._is_false(one.math):
                continue
            doubts = plausible_alternatives(one.math, list(dict.fromkeys((*one.alternatives, *other.alternatives))))
            update = {"legibility": "clear", "alternatives": doubts}
            lines[0][position] = one.model_copy(update=update)
            # Spelled as the first reading has it, so they agree on what's remembered.
            lines[1][position] = other.model_copy(update={**update, "math": one.math})
        return HandwritingReading(lines=lines[0]), HandwritingReading(lines=lines[1])

    async def _is_false(self, math: str) -> bool:
        try:
            report, _ = await shared_pool.verify([LineInput(0, math)], frozenset(), self.settings.verify_timeout_s)
        except VerificationFailed:
            return False
        return report.status == "invalid"

    async def analyze(
        self,
        request: AnalyzeRequest,
        writer: WriterProfile | None = None,
        reading_filter: ReadingFilter | None = None,
        *,
        given: str = "",
        intent: TaskIntent = "auto",
        values: tuple[tuple[str, str], ...] = (),
    ) -> AnalyzeResponse:
        timer = _Timer(
            # An answer to an equation doesn't say that its two sides are the same thing.
            posed=reading_filter is not None and not given,
            page_intent=request.task_context,
            line_intents=dict(request.line_intents),
            forced_intent=intent,
            given=given,
            values=values,
            fill=reading_filter,
            mistake_lines={
                **{_fingerprint_of(mistake_id): frozenset() for mistake_id in request.out_of_view_mistake_ids},
                **{_fingerprint_of(mistake_id): frozenset(lines) for mistake_id, lines in request.mistake_line_ids.items()},
            },
        )
        filtered: ReadingFilter = reading_filter or (lambda reading: reading)
        version = request.snapshot_version
        session_id = request.session_id

        traced: TraceInput | None = None
        digest = ""
        ink_lines = request.ink_lines

        def respond(status: AnalysisStatus, **fields: object) -> AnalyzeResponse:
            trace: list[LineTrace] = trace_lines(traced) if traced is not None and self.settings.is_development else []
            if self.settings.is_development:
                self._keep_trace(
                    session_id,
                    {
                        "requestId": request.request_id,
                        "snapshotVersion": version,
                        "trigger": request.trigger,
                        "attempt": request.attempt,
                        "status": status,
                        "reason": fields.get("reason"),
                        "stale": fields.get("stale", False),
                        "lines": [line.model_dump(by_alias=True) for line in trace],
                        # Enough to tell which stage a line was lost at. Never the image, only its hash.
                        "imageDigest": digest[:12],
                        "lineIds": [line.id for line in ink_lines or []],
                        "drawnLineIds": [line.id for line in ink_lines or [] if line.in_image],
                        "cacheHit": timer.cache_hit,
                        "modelRequests": timer.model_requests,
                        "secondReading": timer.second_reading,
                        "settledLineIds": fields.get("settled_line_ids", []),
                        "newMistakeIds": [m.id for m in fields.get("mistakes", []) if m.is_new],  # type: ignore[attr-defined]
                        "keptMistakeIds": [m.id for m in fields.get("mistakes", []) if not m.is_new],  # type: ignore[attr-defined]
                        "resolvedMistakeIds": list(fields.get("resolved_mistake_ids", [])),  # type: ignore[call-overload]
                        "undecidedMistakeIds": list(fields.get("undecided_mistake_ids", [])),  # type: ignore[call-overload]
                        "contestedLines": dict(timer.contested),
                    },
                )
            timings = Timings(
                interpret_ms=round(timer.interpret_ms, 1),
                verify_ms=round(timer.verify_ms, 1),
                total_ms=round((time.perf_counter() - timer.started) * 1000, 1),
                model_requests=timer.model_requests,
                cost_usd=round(timer.cost_usd, 6),
                cache_hit=timer.cache_hit,
                stages=dict(timer.stages),
                calls=list(timer.calls),
            )
            log.info(
                "analysis",
                extra={
                    "request_id": request.request_id,
                    "trigger": request.trigger,
                    "status": status,
                    "total_ms": timings.total_ms,
                    "stages": timings.stages,
                    "calls": [call.model_dump() for call in timings.calls],
                },
            )
            return AnalyzeResponse(
                snapshot_version=version,
                request_id=request.request_id,
                status=status,
                timings=timings,
                contested_line_ids=list(timer.contested),
                trace=trace,
                **fields,  # type: ignore[arg-type]
            )

        # Before any session state changes, so a bad upload can't move the version forward.
        image, digest = await asyncio.to_thread(sanitise_image, request.image)
        timer.stage("sanitised")

        if not self.mistakes.begin(session_id, version):
            return respond("uncertain", stale=True, reason="stale_snapshot")

        acknowledged_ids = list(dict.fromkeys(request.acknowledged_mistake_ids))
        acknowledged = frozenset(
            _fingerprint_of(mistake_id) for mistake_id in (*acknowledged_ids, *request.out_of_view_mistake_ids)
        )

        drawn = sum(line.in_image for line in ink_lines) if ink_lines else None
        if ink_lines:
            unread = [
                line.id for line in ink_lines if not line.in_image and self._memory.get(session_id, line.id) is None
            ]
            if unread:
                # The client thinks these have been read, and this server doesn't remember them.
                return respond("uncertain", reason="lines_unread", unread_line_ids=unread)

        # Every line is one already agreed on, such as after an undo. There's nothing to read.
        from_memory = drawn == 0
        # Asked directly, what's on the page counts over what was remembered. A misreading both
        # readings shared would otherwise stay until the student rewrote the line.
        reread = request.trigger == "manual"
        # Looking again at lines that couldn't be settled wants new readings, not the cached ones.
        references = self._references_for(request, writer)
        again_label = f":{request.attempt}" if request.attempt else ""
        if references:
            # Only ever this student's, and only with these samples.
            again_label += f":w{references_tag(references, writer.user_id)}"  # type: ignore[union-attr]
        first_label, confirm_label = f"first{again_label}", f"confirm{again_label}"
        first = (
            None
            if from_memory
            else self._start(image, digest, Purpose.TRANSCRIBE, session_id, first_label, drawn, references)
        )
        confirm = None
        if (
            not from_memory
            and self.settings.confirm_alerts
            and self.settings.confirm_in_parallel
            and self._second_is_owed(digest, drawn, confirm_label)
        ):
            # Speculative. If this check turns out to need no confirmation, its reply needn't wait
            # for it, and the reading is kept in case the same image is checked again.
            confirm = self._start(image, digest, Purpose.TRANSCRIBE_CONFIRM, session_id, confirm_label, drawn, references)

        try:
            raw = (
                HandwritingReading(lines=[])
                if from_memory
                else filtered(
                    await self._read(
                        image, digest, Purpose.TRANSCRIBE, session_id, timer,
                        label=first_label, started=first, line_count=drawn, references=references,
                    )
                )
            )
        except ModelError as exc:
            # Abstain. A failed request must never look like either a mistake or a pass.
            return respond("uncertain", reason=f"model_{exc.category}", model_issue=model_issue(exc))
        timer.stage("read")

        # A check of drawn lines waits for this anyway, to know which lines it can stop reading, so
        # what the two readings have between them can be settled before anything is judged by them.
        first_read, second_read = raw, None
        stand_in = False
        second_failure: ModelError | None = None
        # Not `confirm`, which is None when this image has already been read.
        if ink_lines and not from_memory and self.settings.confirm_alerts and self.settings.confirm_in_parallel:
            try:
                reading_two, stand_in = await self._second_reading(
                    image, digest, session_id, timer, confirm_label, confirm, drawn, references
                )
                second_read = filtered(reading_two)
            except ModelError as exc:
                second_failure = exc
                log.info("second_reading_failed", extra={"category": exc.category})
            timer.stage("second_read")
        second_raw = second_read
        self._note_answers(session_id, (first_read, second_read), ink_lines, timer)
        if second_raw is not None and not stand_in:
            raw, second_raw = await self._reconciled(raw, second_raw)

        page = self._lay_out(raw, ink_lines, session_id, reread, timer.fill, timer.overruled)
        if page is None:
            # Only some of the working was drawn, and the reading doesn't line up with it, so nothing
            # can be said about the page. Drawing all of it next time gets it back in step.
            return respond("uncertain", reason="lines_misaligned", unread_line_ids=[line.id for line in ink_lines or []])
        reading = page.reading
        report, present = await self._verify(page, acknowledged, timer)
        timer.stage("verified")

        if report.status == "uncertain" and self.settings.second_reading_on_uncertain and _is_reading_problem(report):
            try:
                raw = filtered(
                    await self._read(
                        image, digest, Purpose.TRANSCRIBE_CONFIRM, session_id, timer,
                        label=f"second{again_label}", line_count=drawn, references=references,
                    )
                )
                relaid = self._lay_out(raw, ink_lines, session_id, reread, timer.fill, timer.overruled)
                if relaid is not None:
                    page, reading = relaid, relaid.reading
                    report, present = await self._verify(page, acknowledged, timer)
            except ModelError as exc:
                log.info("second_reading_failed", extra={"category": exc.category})

        lines = [ReadLineSummary(text=line.math or line.raw_text, kind=line.kind, legibility=line.legibility)
                 for line in reading.lines]

        async def again() -> HandwritingReading:
            if from_memory:
                return raw
            if second_raw is not None:
                return second_raw
            if second_failure is not None:
                raise second_failure
            return filtered(
                await self._read(
                    image, digest, Purpose.TRANSCRIBE_CONFIRM, session_id, timer,
                    label=confirm_label, started=confirm, line_count=drawn, references=references,
                )
            )

        remembered_before = frozenset(
            line.id for line in ink_lines or [] if self._memory.get(session_id, line.id) is not None
        )
        settled = [line.id for line in ink_lines or []] if from_memory else []
        if ink_lines and second_raw is not None:
            # These lines are never read again, however long the page gets.
            settled = self._remember_agreed(raw, second_raw, ink_lines, session_id, reread)
            self._note_doubts(session_id, (first_read, second_read), ink_lines, settled)

        def trace_of(
            checked: VerificationReport,
            disputed: frozenset[str] = frozenset(),
            new: Iterable[StepResult] = (),
            gone: Iterable[str] = (),
        ) -> TraceInput:
            return TraceInput(
                version=version,
                ink_lines=ink_lines,
                first=first_read,
                second=second_read,
                page=reading,
                report=checked,
                disputed=disputed,
                acknowledged=acknowledged,
                new=frozenset(step.fingerprint or "" for step in new),
                resolved=frozenset(_fingerprint_of(mistake_id) for mistake_id in gone),
                remembered=remembered_before,
                settled=frozenset(settled),
            )

        checked = report
        fresh = _unique(step for step in report.invalid_steps if step.fingerprint not in acknowledged)
        traced = trace_of(checked)
        disputed: frozenset[str] = frozenset()
        if fresh and self.settings.confirm_alerts:
            try:
                agreed = await self._confirmed(again, ink_lines, session_id, reread, acknowledged, timer)
            except ModelError as exc:
                # Stay silent, and the next pause tries again.
                log.info("confirmation_failed", extra={"category": exc.category})
                return respond(
                    "uncertain", reason=f"model_{exc.category}", model_issue=model_issue(exc),
                    lines=lines, settled_line_ids=settled,
                )
            timer.stage("alert_confirmed")
            disputed = frozenset(step.fingerprint or "" for step in fresh if step.fingerprint not in agreed)
            traced = trace_of(checked, disputed)
            if len(disputed) == len(fresh):
                # Say nothing about the page at all. The two readings don't agree on what it says.
                return respond("uncertain", reason="readings_disagree", lines=lines, settled_line_ids=settled)
            # One disputed line says nothing about another. It stays silent, and the rest stand.
            report = report.without(disputed)
            fresh = [step for step in fresh if step.fingerprint not in disputed]

        # A mistake has only gone if we could read the page. An unrelated integral nearby doesn't
        # stop a fix being recognised, but a line that couldn't be read might be the mistake.
        scopes = request.mistake_line_ids
        gone = [m for m in acknowledged_ids if _fingerprint_of(m) not in present]
        misread = self._misread(session_id, gone, scopes, timer)
        resolved = [m for m in gone if _shows_a_fix(m, page, report, scopes, m in misread)]
        fix_issue: ModelIssue | None = None
        fix_reason: str | None = None
        if resolved and self.settings.confirm_alerts:
            try:
                resolved, fix_reason = await self._confirm_resolved(
                    again, ink_lines, session_id, reread, resolved, acknowledged, timer, scopes, misread
                )
            except ModelError as exc:
                # The alert stays for now, and saying why means the check is tried again.
                log.info("confirmation_failed", extra={"category": exc.category})
                resolved, fix_issue = [], model_issue(exc)
            timer.stage("fix_confirmed")
        # A mistake can't be both gone and being reported.
        report = report.without(frozenset(_fingerprint_of(mistake_id) for mistake_id in resolved))
        new_fingerprints = {step.fingerprint for step in fresh}
        shown = _unique(report.invalid_steps)
        fresh_shown = [step for step in shown if step.fingerprint in new_fingerprints]
        undecided = [m for m in gone if m not in resolved]
        verified = _verified_lines(report, page, settled)
        decision = self.mistakes.record(
            session_id,
            version,
            new=[_tracked(step) for step in fresh_shown],
            seen=[_tracked(step) for step in shown if step.fingerprint not in new_fingerprints],
            resolved=resolved,
        )
        timer.stage("decided")
        traced = trace_of(checked, disputed, fresh_shown, decision.resolved_ids)

        if not shown:
            status, reason = classify(reading, report)
            if fix_issue is not None or fix_reason is not None:
                # One reading says a known mistake has gone. That's no basis for saying all is well.
                status, reason = "uncertain", f"model_{fix_issue.category}" if fix_issue else fix_reason
            return respond(
                status,
                reason=reason if status != "no_error" else None,
                resolved_mistake_ids=list(decision.resolved_ids),
                stale=decision.stale,
                model_issue=fix_issue,
                lines=lines,
                settled_line_ids=settled,
                verified_line_ids=verified,
                undecided_mistake_ids=undecided,
                clarification=_clarification_in(report, page, settled),  # type: ignore[arg-type]
                reminder=_reminder_in(report),
            )

        mistakes = [_public(step, reading, page, step.fingerprint in new_fingerprints) for step in shown]  # type: ignore[arg-type]
        return respond(
            "confirmed_error",
            mistake=next((m for m in mistakes if m.is_new), mistakes[0]),
            mistakes=mistakes,
            intervention=Intervention(should_quack=decision.should_quack),
            resolved_mistake_ids=list(decision.resolved_ids),
            stale=decision.stale,
            model_issue=fix_issue,
            lines=lines,
            settled_line_ids=settled,
            verified_line_ids=verified,
            undecided_mistake_ids=undecided,
            clarification=_clarification_in(report, page, settled),  # type: ignore[arg-type]
            reminder=_reminder_in(report),
        )

    async def inspect_selection(
        self, request: AskRequest, session_id: str, references: tuple[WriterReference, ...] = ()
    ) -> tuple[SelectionFinding, list[ReadLineSummary], list[str], _Timer, Clarification | None]:
        """What the checker can say about circled lines, using what's remembered of them where it can.

        The same two readings and the same agreement as a check, and nothing is recorded: no version,
        no mistake and no event. Being circled can't make a line a mistake, or change what's counted.
        """

        result = await self._inspect(request, session_id, references)
        if self.settings.is_development:
            finding, _, unread, timer, _ = result
            self._keep_trace(
                session_id,
                {
                    "requestId": None,
                    "trigger": "circle",
                    "action": request.action,
                    "status": finding.verdict,
                    "reason": finding.step.reason if finding.step else None,
                    "lineIds": [line.id for line in request.ink_lines],
                    "drawnLineIds": [line.id for line in request.ink_lines if line.in_image],
                    "selectedLineIds": list(request.selected_line_ids),
                    "unreadLineIds": unread,
                    "cacheHit": timer.cache_hit,
                    "modelRequests": timer.model_requests,
                    "secondReading": timer.second_reading,
                },
            )
        return result

    async def _inspect(
        self, request: AskRequest, session_id: str, references: tuple[WriterReference, ...]
    ) -> tuple[SelectionFinding, list[ReadLineSummary], list[str], _Timer, Clarification | None]:
        timer = _Timer(page_intent=request.task_context, line_intents=dict(request.line_intents))
        ink_lines = request.ink_lines
        partial = frozenset(request.partial_line_ids) & frozenset(request.selected_line_ids)
        # Nothing can be said of a fragment, so it isn't worth paying to read one. A question can still be asked.
        if partial and request.action != "question":
            return SelectionFinding("unchecked", partial=True), [], [], timer, None
        unread = [line.id for line in ink_lines if not line.in_image and self._memory.get(session_id, line.id) is None]
        if unread:
            return SelectionFinding("unclear"), [], unread, timer, None
        drawn = sum(line.in_image for line in ink_lines)
        first_raw = HandwritingReading(lines=[])
        second_raw: HandwritingReading | None = None
        if drawn:
            if request.image is None:
                return SelectionFinding("unclear"), [], [line.id for line in ink_lines], timer, None
            image, digest = await asyncio.to_thread(sanitise_image, request.image)
            tag = f":w{references_tag(references, session_id)}" if references else ""
            first_label, confirm_label = f"first{tag}", f"confirm{tag}"
            first = self._start(image, digest, Purpose.TRANSCRIBE, session_id, first_label, drawn, references)
            confirm = (
                self._start(image, digest, Purpose.TRANSCRIBE_CONFIRM, session_id, confirm_label, drawn, references)
                if self._second_is_owed(digest, drawn, confirm_label)
                else None
            )
            first_raw = await self._read(
                image, digest, Purpose.TRANSCRIBE, session_id, timer,
                label=first_label, started=first, line_count=drawn, references=references,
            )
            second_raw, stand_in = await self._second_reading(
                image, digest, session_id, timer, confirm_label, confirm, drawn, references
            )
            self._note_answers(session_id, (first_raw, second_raw), ink_lines, timer)
            if not stand_in:
                first_raw, second_raw = await self._reconciled(first_raw, second_raw)
        else:
            self._note_answers(session_id, (), ink_lines, timer)
        page = self._lay_out(first_raw, ink_lines, session_id)
        if page is None:
            return SelectionFinding("unclear"), [], [line.id for line in ink_lines], timer, None
        settled = [line.id for line in ink_lines if self._memory.get(session_id, line.id) is not None]
        if second_raw is not None:
            settled = self._remember_agreed(first_raw, second_raw, ink_lines, session_id)
        report, _ = await self._verify(page, frozenset(), timer)
        second_report = None
        second_page = None
        if second_raw is not None:
            second_page = self._lay_out(second_raw, ink_lines, session_id)
            if second_page is not None:
                second_report, _ = await self._verify(second_page, frozenset(), timer)
        selected = set(request.selected_line_ids)
        chosen = [
            line
            for line, line_id in zip(page.reading.lines, page.line_ids, strict=True)
            if line_id in selected
        ]
        lines = [
            ReadLineSummary(text=line.math or line.raw_text, kind=line.kind, legibility=line.legibility) for line in chosen
        ]
        if partial:
            working = tuple(line.math or line.raw_text for line in chosen)
            return SelectionFinding("unchecked", working=working, partial=True), lines, [], timer, None
        second = _SecondOpinion(second_page, second_report) if second_raw is not None else None
        finding = _finding_for(page, report, second, selected, chosen, frozenset(settled))
        others = [answer for line_id in request.selected_line_ids for answer in timer.contested.get(line_id, ())]
        if finding.verdict == "unclear" and others:
            # Say what else it's been read as, not only what it was read as just now.
            read_as = [answer for answer in others if not any(_same_answer(answer, text) for text in finding.working)]
            finding = replace(finding, alternatives=tuple(read_as) or finding.alternatives)
        clarification = _clarification_in(report, page, settled) if finding.verdict == "ambiguous_intent" else None
        return finding, lines, [], timer, clarification

    def _lay_out(
        self,
        raw: HandwritingReading,
        ink_lines: list[InkLine] | None,
        session_id: str,
        reread: bool = False,
        fill: ReadingFilter | None = None,
        overruled: set[str] | None = None,
    ) -> _Page | None:
        """Put a reading of the drawn lines together with what's remembered of the rest.

        None if only some lines were drawn and the reading doesn't match them, since there's then no
        telling which is which. If all were drawn it's used as it stands, with nothing to anchor it.

        `fill` is what a practice question puts in front of the page. Each reading had it applied to the
        lines drawn, so it goes over the whole page again here: a remembered line was filled for where it
        was then, and with the line above it rubbed out, `= 5(3) + 4` had nothing left to follow.
        """

        if not ink_lines:
            return _Page(raw, [None] * len(raw.lines))
        drawn = [line for line in ink_lines if line.in_image]
        if len(raw.lines) != len(drawn):
            return _Page(raw, [None] * len(raw.lines)) if len(drawn) == len(ink_lines) else None
        read = iter(raw.lines)
        merged: list[ReadLine] = []
        for ink in ink_lines:
            just_read = next(read) if ink.in_image else None
            # An agreed line stays as agreed when it's redrawn as context for the line below it.
            remembered = self._memory.get(session_id, ink.id)
            line = just_read if reread and just_read is not None else remembered or just_read
            if (
                overruled is not None and line is remembered and just_read is not None
                and remembered.kind == just_read.kind == "math" and not _same_answer(remembered.math, just_read.math)
            ):
                overruled.add(ink.id)
            assert line is not None
            if just_read is not None:
                line = line.model_copy(update={"continues_previous": just_read.continues_previous})
            merged.append(line)
        page = fill(HandwritingReading(lines=merged)) if fill is not None else HandwritingReading(lines=merged)
        lines = [
            line.model_copy(update={"continues_previous": "unknown"})
            if ink.spaced and line.continues_previous == "yes" else line
            for ink, line in zip(ink_lines, page.lines, strict=True)
        ]
        return _Page(HandwritingReading(lines=lines), [line.id for line in ink_lines])

    def _remember_agreed(
        self,
        first: HandwritingReading,
        second: HandwritingReading,
        ink_lines: list[InkLine],
        session_id: str,
        reread: bool = False,
    ) -> list[str]:
        drawn = [line for line in ink_lines if line.in_image]
        if len(first.lines) != len(drawn) or len(second.lines) != len(drawn):
            return [line.id for line in ink_lines if not line.in_image]
        for ink, one, other in zip(drawn, first.lines, second.lines, strict=True):
            unknown = reread or self._memory.get(session_id, ink.id) is None
            if unknown and (_agree(one, other) or _both_crossed_out(one, other)):
                self._memory.put(session_id, ink.id, one)
        return [line.id for line in ink_lines if self._memory.get(session_id, line.id) is not None]

    async def _confirmed(
        self,
        again: Callable[[], Awaitable[HandwritingReading]],
        ink_lines: list[InkLine] | None,
        session_id: str,
        reread: bool,
        acknowledged: frozenset[str],
        timer: _Timer,
    ) -> frozenset[str]:
        """Read again, independently. Each mistake stands only if it comes out of this one too.

        Misreading a digit is the most likely way to accuse a student wrongly. If the second
        reading fails we stay silent, and the next pause tries again.
        """

        second = self._lay_out(await again(), ink_lines, session_id, reread, timer.fill, timer.overruled)
        if second is None:
            return frozenset()
        report, _ = await self._verify(second, acknowledged, timer)
        return frozenset(step.fingerprint for step in report.invalid_steps if step.fingerprint)

    async def _confirm_resolved(
        self,
        again: Callable[[], Awaitable[HandwritingReading]],
        ink_lines: list[InkLine] | None,
        session_id: str,
        reread: bool,
        resolved: list[str],
        acknowledged: frozenset[str],
        timer: _Timer,
        scopes: dict[str, list[str]],
        misread: frozenset[str] = frozenset(),
    ) -> tuple[list[str], str | None]:
        """Telling a student they've fixed something needs the same care as saying they haven't.

        One misread line can make a mistake seem gone, and it would then quack again at the next
        reading that gets it right. Returns what's fixed, and if anything wasn't, why: `fix_disputed`
        if the second reading still finds it, `fix_unconfirmed` if that reading is too unclear to say.
        """

        second = self._lay_out(await again(), ink_lines, session_id, reread, timer.fill, timer.overruled)
        if second is None:
            return [], "fix_unconfirmed"
        report, present = await self._verify(second, acknowledged, timer)
        still_there = [mistake_id for mistake_id in resolved if _fingerprint_of(mistake_id) in present]
        fixed = [
            mistake_id
            for mistake_id in resolved
            if mistake_id not in still_there and _shows_a_fix(mistake_id, second, report, scopes, mistake_id in misread)
        ]
        if len(fixed) == len(resolved):
            return fixed, None
        return fixed, "fix_disputed" if still_there else "fix_unconfirmed"


@dataclass(frozen=True)
class _SecondOpinion:
    """The second reading of what was drawn. Either is None if it can't be laid out on the ink."""

    page: _Page | None
    report: VerificationReport | None

    def agrees_it_is_wrong(self, wrong: StepResult) -> bool:
        return self.report is not None and any(other.fingerprint == wrong.fingerprint for other in self.report.invalid_steps)

    def agrees_it_is_right(self, selected: set[str]) -> bool:
        if self.page is None or self.report is None:
            return False
        steps = [step for step in self.report.steps if self.page.line_id_of(step) in selected]
        return bool(steps) and all(step.status == "valid" for step in steps)


def _finding_for(
    page: _Page,
    report: VerificationReport,
    second: _SecondOpinion | None,
    selected: set[str],
    chosen: list[ReadLine],
    settled: frozenset[str],
) -> SelectionFinding:
    """A step only counts as wrong if both readings say so, and as right if both do. With nothing
    drawn there's only what two readings already agreed, so `second` is None and has nothing to add."""

    working = tuple(line.math or line.raw_text for line in chosen)
    alternatives = tuple(text for line in chosen for text in line.alternatives)
    # The reading has more lines or fewer than the ink, so there's no telling which is which.
    if not any(page.line_ids):
        return SelectionFinding("unclear", working=working)
    if not chosen or all(line.kind in ("prose", "crossed_out") for line in chosen):
        return SelectionFinding("not_math", working=working)
    steps = [step for step in report.steps if page.line_id_of(step) in selected]
    agreed = selected <= settled
    # A line neither reading is sure of can't be called anything, whatever it seems to say.
    if not agreed:
        return SelectionFinding("unclear", working=working, alternatives=alternatives)
    wrong = next((step for step in steps if step.status == "invalid"), None)
    if wrong is not None:
        if second is not None and not second.agrees_it_is_wrong(wrong):
            return SelectionFinding("unclear", working=working, alternatives=alternatives)
        return SelectionFinding("incorrect", wrong, working)
    if any(step.reason == "ambiguous_intent" for step in steps):
        return SelectionFinding("ambiguous_intent", working=working)
    if any(step.status == "unsupported" for step in steps):
        return SelectionFinding("unsupported", working=working)
    if any(step.status == "uncertain" for step in steps):
        doubts = [step for step in steps if step.status == "uncertain"]
        unreadable = ("low_legibility", "illegible_line", "alternative_reading_is_valid", "reading_contested")
        reading_problem = any(step.reason in unreadable for step in doubts)
        return SelectionFinding("unclear" if reading_problem else "unsupported", doubts[0], working, alternatives)
    valid = next((step for step in reversed(steps) if step.status == "valid"), None)
    if valid is not None:
        # The line itself may be agreed on, and still follow from a line above that isn't.
        if second is not None and not second.agrees_it_is_right(selected):
            return SelectionFinding("unclear", working=working, alternatives=alternatives)
        return SelectionFinding("correct", valid, working)
    return SelectionFinding("unchecked", working=working)


def _clarification_in(report: VerificationReport, page: _Page, settled: Iterable[str]) -> Clarification | None:
    """Only about a line two readings agree on, as a misread x would otherwise start a conversation."""

    agreed = set(settled)
    for step in report.steps:
        line_id = page.line_id_of(step)
        if step.reason == "ambiguous_intent" and line_id and line_id in agreed:
            return Clarification(
                line_id=line_id,
                step=step.current_step or "",
                question="Are you simplifying this, or solving it?",
            )
    return None


def _reminder_in(report: VerificationReport) -> str | None:
    return next((step.note for step in report.steps if step.note), None)


def _unique(steps: Iterable[StepResult]) -> list[StepResult]:
    """The same wrong line can come up more than once, and it's one mistake."""

    return list({step.fingerprint: step for step in reversed(list(steps))}.values())[::-1]


def _public(step: StepResult, reading: HandwritingReading, page: _Page, is_new: bool) -> Mistake:
    return Mistake(
        id=f"m_{step.fingerprint}",
        category=step.error_category or "unknown",
        error_type=step.error_type or "unknown",
        operation_type=step.operation_type,
        previous_step=step.previous_step or "",
        current_step=step.current_step or "",
        verified=True,
        region=_region_for(reading, step),
        line_id=page.line_id_of(step),
        problem_id=page.line_id_at(step.problem_line),
        parsed_operation=step.parsed_operation or "",
        is_new=is_new,
    )


def _can_show_a_fix(reading: HandwritingReading, report: VerificationReport) -> bool:
    """A line we're unsure of might be the mistake itself, read as what it should have said."""

    all_clear = all(line.legibility == "clear" for line in reading.lines if line.kind == "math")
    readable = not _is_reading_problem(report) and not _nothing_was_checked(reading, report)
    return bool(reading.lines) and all_clear and readable


def _shows_a_fix(
    mistake_id: str, page: _Page, report: VerificationReport, scopes: dict[str, list[str]], misread: bool = False
) -> bool:
    """Can this reading say this mistake's been put right? It's about the lines the mistake is on.

    With no idea which those are, every line on the page might be the mistake, so all must be clear.
    Knowing them, a line that couldn't be read somewhere else has no bearing, which on a busy page
    was enough to stop any fix being recognised. They must say something, and be right or be some
    other mistake. `∫ x dx`, with its answer rubbed out, has fixed nothing.
    """

    lines = set(scopes.get(mistake_id, ()))
    # The reading didn't line up with the ink, so there's no telling which of its lines is which.
    if not lines or not any(page.line_ids):
        return _can_show_a_fix(page.reading, report)
    positions = [position for position, line_id in enumerate(page.line_ids) if line_id in lines]
    if not positions:
        return False
    for position in positions:
        line = page.reading.lines[position]
        if line.kind != "math" or line.legibility != "clear":
            return False
        # What's on the line above can decide whether this one's a mistake, unless it stands alone.
        above = page.reading.lines[position - 1] if position else None
        if above is not None and line.continues_previous != "no" and above.kind == "math" and above.legibility != "clear":
            return False
    # With no finding on it a line can still be fine: a line below may put it right, or it's an
    # equation being solved. But it has to claim something, and a bare `∫ x dx` claims nothing.
    if not all("=" in page.reading.lines[position].math for position in positions):
        return False
    steps = [step for step in report.steps if page.line_id_of(step) in lines]
    return all(
        step.status in ("valid", "invalid")
        or step.reason == "corrected_on_the_next_line"
        # The ink it was found on has since been read as something else. It can't be said to be wrong.
        or (misread and step.reason == "reading_contested")
        for step in steps
    )


def _verified_lines(report: VerificationReport, page: _Page, settled: Iterable[str]) -> list[str]:
    """Lines both readings agree on, the verifier found right, and whose line above is agreed too if they follow it."""

    agreed = set(settled)
    statuses: dict[str, list[str]] = {}
    for step in report.steps:
        line_id = page.line_id_of(step)
        if line_id:
            statuses.setdefault(line_id, []).append(step.status)
    verified = []
    for position, line_id in enumerate(page.line_ids):
        if line_id is None or line_id not in agreed or not statuses.get(line_id):
            continue
        if any(status != "valid" for status in statuses[line_id]):
            continue
        above = page.line_ids[position - 1] if position else None
        if above is not None and page.reading.lines[position].continues_previous != "no" and above not in agreed:
            continue
        verified.append(line_id)
    return verified


def _retrieve(task: asyncio.Task[HandwritingReading]) -> None:
    """A reading raced and left behind may fail later, with nobody there to hear about it."""

    if not task.cancelled():
        task.exception()


def _fingerprint_of(mistake_id: str) -> str:
    return mistake_id.removeprefix("m_")


def _original_of(fingerprint: str) -> str:
    """The maths a copy is of. A copy's fingerprint is the original's, then the line it was found on."""

    return fingerprint.split("-", 1)[0]


def _copy_on(fingerprint: str, line_id: str) -> str:
    return f"{fingerprint}-{hashlib.sha256(f'{fingerprint}|{line_id}'.encode()).hexdigest()[:8]}"


def _which_copy(
    original: str, line_id: str | None, known: list[str], lines: dict[str, frozenset[str]], first_seen: dict[str, str | None]
) -> str:
    """Which mistake this is: one the student knows, or another copy of the same wrong maths.

    `∫ x dx = 2` written again lower down the page is a second mistake, with its own alert and its own
    highlight. Its id comes from its own ink, so an undo brings back the same one. Edited, it keeps the
    id it had, as the client says which lines now hold it. With no way to tell the copies apart,
    such as after a reload, it's the one already known, and there's no second quack.
    """

    own = _copy_on(original, line_id) if line_id else None
    if known:
        if own in known:
            return own  # type: ignore[return-value]
        if line_id is None:
            return known[0]
        holding = next((fingerprint for fingerprint in known if line_id in lines.get(fingerprint, ())), None)
        if holding is not None:
            return holding
        # Out of view, or never tied to a line: this could be it, so it's not a second one.
        untied = next((fingerprint for fingerprint in known if not lines.get(fingerprint)), None)
        return untied if untied is not None else own  # type: ignore[return-value]
    # Written twice in one go. The first line it's on is the mistake, and the other a copy.
    first = first_seen.setdefault(original, line_id)
    return original if line_id is None or first == line_id else own  # type: ignore[return-value]


def _as_copies(
    report: VerificationReport, page: _Page, acknowledged: frozenset[str], lines: dict[str, frozenset[str]]
) -> VerificationReport:
    known: dict[str, list[str]] = {}
    for fingerprint in sorted(acknowledged):
        known.setdefault(_original_of(fingerprint), []).append(fingerprint)
    near = _beside(page, lines)
    originals = frozenset(known)
    first_seen: dict[str, str | None] = {}
    # Pieces of working with a new copy in them. The verifier took the copy for one already known.
    copied: set[int | None] = set()
    steps = []
    for step in report.steps:
        if step.status == "invalid" and step.fingerprint:
            original = _original_of(step.fingerprint)
            # New to the verifier, which was given only the maths of what's known, not its copies.
            new = original not in originals
            which = _which_copy(original, page.line_id_of(step), known.get(original, []), near, first_seen)
            if new and step.problem_line in copied:
                # One slip gets one alert, as `verify_lines` has it for a mistake it knows is new.
                step = replace(step, status="uncertain", reason="follows_a_new_mistake", fingerprint=None)
            else:
                if which not in acknowledged and not new:
                    copied.add(step.problem_line)
                step = replace(step, fingerprint=which)
        steps.append(step)
    return VerificationReport.of(tuple(steps))


def _beside(page: _Page, lines: dict[str, frozenset[str]]) -> dict[str, frozenset[str]]:
    """Each mistake's lines and the ones just above and below. The same wrong step written again
    straight underneath is the same working, and the same mistake, not a copy of it."""

    order = page.line_ids
    near = {}
    for fingerprint, own in lines.items():
        around = {order[index + step] for index, line_id in enumerate(order) if line_id in own for step in (-1, 1)
                  if 0 <= index + step < len(order)}
        near[fingerprint] = frozenset(own | {line_id for line_id in around if line_id is not None}) if own else own
    return near


def _not_called_right(
    report: VerificationReport, page: _Page, contested: dict[str, tuple[str, ...]], overruled: set[str] | None = None
) -> VerificationReport:
    """A line read as two different answers can't be called right on either of them.

    Nor wrong on a remembered reading that this check's own reading of the same ink contradicts (`overruled`).
    A reading two readings agreed on is remembered for its ink and used in place of later ones, so on the iPad on
    2026-11-04 (evening) `7(x+8)-4(x-2)`, agreed once, was still checked three checks later, after both readings of
    each of those checks had said `7(x+6)-4(x-2)`: the student was told about an 8 they hadn't written. A mistake
    still needs two readings that agree, in the check that reports it.
    """

    overruled = overruled or set()
    if not contested and not overruled:
        return report
    steps = tuple(
        replace(step, status="uncertain", reason="reading_contested")
        if (step.status == "valid" and page.line_id_of(step) in contested)
        or (step.status == "invalid" and page.line_id_of(step) in overruled)
        else step
        for step in report.steps
    )
    return VerificationReport.of(steps)


def _still_there(
    page: _Page, acknowledged: frozenset[str], where: frozenset[tuple[str, int]], lines: dict[str, frozenset[str]]
) -> frozenset[str]:
    """Which known mistakes are still on the page. A copy has gone if its own lines no longer have it,
    whatever the others say. Where nothing says which lines are its own, it's there if its maths is."""

    found: dict[str, set[str | None]] = {}
    for original, position in where:
        found.setdefault(original, set()).add(page.line_id_at(position))
    lines = _beside(page, lines)
    present = set()
    for fingerprint in acknowledged:
        on = found.get(_original_of(fingerprint))
        own = lines.get(fingerprint)
        if on is not None and (not own or None in on or on & own):
            present.add(fingerprint)
    return frozenset(present)


def _tracked(step: StepResult) -> TrackedMistake:
    return TrackedMistake(
        id=f"m_{step.fingerprint}",
        category=step.error_category or "unknown",
        error_type=step.error_type or "unknown",
        operation_type=step.operation_type,
        previous_step=step.previous_step or "",
        current_step=step.current_step or "",
        expected=step.expected or "",
        evidence=step.evidence or "",
        parsed_operation=step.parsed_operation or "",
        term_facts=step.term_facts,
        extra_terms=step.extra_terms,
    )


def _abstain_reason(report: VerificationReport) -> str | None:
    return next((s.reason for s in report.steps if s.status in ("uncertain", "unsupported") and s.reason), None)


def _nothing_was_checked(reading: HandwritingReading, report: VerificationReport) -> bool:
    """Only words, or only unsupported notation: no check was made, so nothing can have been fixed."""

    unreadable = sum(s.status == "unsupported" and s.operation_type == "reading" for s in report.steps)
    return sum(line.kind == "math" for line in reading.lines) <= unreadable


def _is_reading_problem(report: VerificationReport) -> bool:
    """Is any line unreliable, so that the absence of a mistake proves nothing?"""

    return any(
        s.reason
        in ("low_legibility", "illegible_line", "alternative_reading_is_valid", "dangling_equals", "reading_contested")
        or s.operation_type == "reading"
        for s in report.steps
        if s.status == "uncertain"
    )


def _position(reading: HandwritingReading, line: ReadLine) -> int:
    # By identity, as two lines can be equal, and `index` would find the first.
    return next(index for index, candidate in enumerate(reading.lines) if candidate is line)


def _region_for(reading: HandwritingReading, step: StepResult) -> Region | None:
    """Where the line is, for placing a marker and for keeping an alert while other lines change."""

    considered = [line for line in reading.lines if line.kind not in ("prose", "crossed_out")]
    if step.line >= len(considered):
        return None
    line = considered[step.line]
    top, bottom = max(0.0, min(line.y_top, 1.0)), max(0.0, min(line.y_bottom, 1.0))
    if bottom <= top:
        return None
    return Region(y_top=top, y_bottom=bottom, line=_position(reading, line), line_count=len(reading.lines))
