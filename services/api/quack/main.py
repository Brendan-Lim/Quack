"""FastAPI application. Holds the API key, so the browser never does."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import sys
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from quack.account_api import AccountServices, build_account_router, build_account_services
from quack.config import Settings, get_settings
from quack.learn_api import build_learn_router
from quack.llm.ledger import Ledger
from quack.llm.postgres_ledger import PostgresLedger
from quack.llm.report import summarise
from quack.llm.router import ModelError, ModelRouter
from quack.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    AskRequest,
    AskResponse,
    AssistanceResponse,
    AuthConfig,
    ClearRequest,
    ExplainRequest,
    Health,
    HintRequest,
    Mistake,
    PracticeCheckResponse,
    ReadLineSummary,
    ResolvedEvent,
    SessionSummary,
    StepExplanation,
    Timings,
    VoiceResponse,
)
from quack.services import curriculum, lessons
from quack.services.analysis import AnalysisService, model_issue
from quack.services.ask import SelectionFinding, help_for
from quack.services.handwriting import ImageRejected
from quack.services.hint_engine import MAX_QUESTION_CHARS, PreparedHelp, generate_help, template_text
from quack.services.identity import Identity, NotAuthenticated
from quack.services.lesson_questions import FORM_OF_METHOD
from quack.services.math_verifier import StepResult, disposition_of
from quack.services.mistake_manager import MistakeManager, TrackedMistake
from quack.services.practice import EQUATION, INTENT_BY_METHOD, answer_lines, states_a_value
from quack.services.topics import topic_of
from quack.services.verifier_pool import shared_pool
from quack.services.voice import VoiceRejected, classify_intent, transcribe_wav
from quack.services.writer_references import select_references

_STANDARD_LOG_FIELDS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}
MAX_REQUEST_BYTES = 10_000_000
MAX_AUDIO_BYTES = 8_000_000
MAX_TRACKED_CLIENTS = 10_000
ACCOUNT_ROUTE_ALLOWANCE = 5
# However long the model takes, a student who asked for an explanation gets one within this. On the iPad
# on 2026-11-03 one took 75 s: two 30 s timeouts, each tried again, before a third answered.
EXPLAIN_DEADLINE_S = 25.0


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"level": record.levelname, "logger": record.name, "event": record.getMessage()}
        payload.update({k: v for k, v in record.__dict__.items() if k not in _STANDARD_LOG_FIELDS})
        return json.dumps(payload, default=str)


def _configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("quack")
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    root.propagate = False


class RateLimiter:
    """Sliding windows per client and overall.

    The per-client limit stops a runaway tab. It can be dodged, because client addresses can be
    forged through a proxy, so the overall limit is what actually bounds the spend rate.
    """

    def __init__(self, per_minute: int, global_per_minute: int) -> None:
        self.per_minute = per_minute
        self.global_per_minute = global_per_minute
        self._hits: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - 60
        while self._all and self._all[0] <= cutoff:
            self._all.popleft()
        if len(self._all) >= self.global_per_minute:
            return False
        if len(self._hits) >= MAX_TRACKED_CLIENTS:
            self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > cutoff}
            if len(self._hits) >= MAX_TRACKED_CLIENTS:
                self._hits.clear()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self.per_minute:
            return False
        hits.append(now)
        self._all.append(now)
        return True


def _is_local_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname == "localhost" or hostname.endswith((".local", ".ts.net")):
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    # is_private leaves out the shared range that Tailscale uses.
    return address.is_private or address in ipaddress.ip_network("100.64.0.0/10")


def _same_origin(request: Request, allowed: list[str]) -> bool:
    """Reject requests a third-party page makes from a visitor's browser.

    Multipart and text bodies skip the CORS preflight, so CORS alone doesn't stop them reaching
    a handler. Browsers always send Origin on these requests and scripts can't forge it there.
    """

    origin = request.headers.get("origin")
    if origin is None or origin in allowed:
        return True
    # Host can be anything under DNS rebinding, where a site's name is pointed at this machine. So
    # beyond the configured origins, a match only counts on a local or private address.
    parts = urlsplit(origin)
    hosts = (request.headers.get("host"), request.headers.get("x-forwarded-host"))
    return _is_local_host(parts.hostname) and parts.netloc in hosts


class BodySizeLimit:
    """Enforce the limit on bytes received, since a chunked body declares no length."""

    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.limit:
            await JSONResponse({"detail": "request too large"}, status_code=413)(scope, receive, send)
            return
        received = 0

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise HTTPException(status_code=413, detail="request too large")
            return message

        await self.app(scope, counted, send)


@dataclass
class Services:
    settings: Settings
    ledger: Ledger
    router: ModelRouter
    mistakes: MistakeManager
    analysis: AnalysisService
    limiter: RateLimiter
    help: PreparedHelp
    # None when accounts are off.
    accounts: AccountServices | None = None


def build_services(settings: Settings, router: ModelRouter | None = None) -> Services:
    limits = (settings.budget_project_usd, settings.budget_session_usd,
              settings.budget_request_usd, settings.budget_hourly_usd)
    if router is not None:
        ledger = router.ledger
    elif settings.ledger_backend == "postgres":
        ledger = PostgresLedger(settings.database_url, *limits)
    else:
        ledger = Ledger(settings.ledger_path, *limits)
    active_router = router or ModelRouter(settings, ledger)
    mistakes = MistakeManager()
    return Services(
        settings,
        ledger,
        active_router,
        mistakes,
        AnalysisService(settings, active_router, mistakes),
        RateLimiter(settings.rate_limit_per_minute, settings.global_rate_limit_per_minute),
        PreparedHelp(active_router),
        build_account_services(settings),
    )


def _public_mistake(mistake: TrackedMistake) -> Mistake:
    return Mistake(
        id=mistake.id,
        category=mistake.category,
        error_type=mistake.error_type,
        operation_type=mistake.operation_type,
        previous_step=mistake.previous_step,
        current_step=mistake.current_step,
        verified=True,
    )


def _as_written(category: str, previous: str, current: str) -> str:
    """As the line reads. An equation step is the new line, with no `=` to join it to the one before."""

    if category == "equation_solving":
        return current
    return f"{previous} = {current}".strip(" =")


def _as_corrected(category: str, error_type: str, previous: str, tracked: TrackedMistake | None) -> str:
    """What the verifier established it should say, or nothing if it's not to hand."""

    if tracked is None or not tracked.expected:
        return ""
    if category == "equation_solving":
        return tracked.expected
    if error_type == "rounding_error":
        return f"{previous} ≈ {tracked.expected}"
    return f"{previous} = {tracked.expected}"


def _assistance(
    level: int, text: str, source: Literal["model", "template"], error: ModelError | None, started: float
) -> AssistanceResponse:
    total_ms = round((time.perf_counter() - started) * 1000, 1)
    logging.getLogger("quack.help").info("help", extra={"level": level, "source": source, "total_ms": total_ms})
    return AssistanceResponse(
        level=level, text=text, source=source, model_issue=model_issue(error) if error else None, total_ms=total_ms
    )


def create_app(services: Services | None = None) -> FastAPI:
    _configure_logging()
    state = services or build_services(get_settings())
    development = state.settings.is_development
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # In the background, so the server takes requests while SymPy loads for the first check.
        warming = asyncio.ensure_future(asyncio.to_thread(shared_pool.warm))
        yield
        await warming

    app = FastAPI(
        lifespan=lifespan,
        title="Quack API",
        version="0.1.0",
        docs_url="/docs" if development else None,
        redoc_url="/redoc" if development else None,
        openapi_url="/openapi.json" if development else None,
    )
    app.state.services = state
    app.add_middleware(
        CORSMiddleware,
        allow_origins=state.settings.cors_origin_list,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["content-type", "authorization"],
    )

    app.add_middleware(BodySizeLimit, limit=MAX_REQUEST_BYTES)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default echoes the input back, which can be a whole image.
        fields = sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()})
        return JSONResponse({"detail": "invalid request", "fields": fields}, status_code=422)

    @app.middleware("http")
    async def reject_cross_site(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method in ("POST", "PUT", "DELETE") and not _same_origin(request, state.settings.cors_origin_list):
            return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        return await call_next(request)

    def rate_limited(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        if not state.limiter.allow(client):
            raise HTTPException(status_code=429, detail="too many requests")

    async def requester(authorization: str | None = Header(default=None)) -> Identity | None:  # noqa: B008
        """Who's asking. With accounts on, nobody gets to spend credit without saying."""

        accounts = state.accounts
        if accounts is None:
            return None
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="sign in first")
        try:
            return await asyncio.to_thread(accounts.verifier.verify, token.strip())
        except NotAuthenticated as exc:
            raise HTTPException(status_code=401, detail="sign in again") from exc

    def scoped(identity: Identity | None, session_id: str) -> str:
        # What the server remembers per session can't be reached from another account, even knowing the id.
        return f"{identity.user_id}:{session_id}" if identity else session_id

    def find_mistake(session_id: str, mistake_id: str) -> TrackedMistake:
        mistake = state.mistakes.get_mistake(session_id, mistake_id)
        if mistake is None:
            # Hints are only ever about a mistake the verifier reported to this session.
            raise HTTPException(status_code=404, detail="unknown mistake")
        return mistake

    @app.get("/api/health")
    async def health() -> Health:
        return Health(model_configured=state.router.configured, environment=state.settings.env)

    @app.get("/api/ready", dependencies=[Depends(rate_limited)])
    async def readiness() -> JSONResponse:
        database_reachable = False
        if state.accounts is not None:
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(state.accounts.store.database.one, "SELECT 1 AS reachable"), timeout=5,
                )
                database_reachable = bool(result and result["reachable"] == 1)
            except Exception:
                # Readiness never exposes database errors or connection credentials.
                pass
        auth_configured = state.accounts is not None and state.settings.auth_mode == "supabase"
        return JSONResponse({
            "reachable": True,
            "databaseReachable": database_reachable,
            "authConfigured": auth_configured,
            "modelConfigured": state.router.configured,
            "modelStatus": "configured_not_probed" if state.router.configured else "not_configured",
            "inferenceEnabled": state.router.configured and state.settings.budget_project_usd > 0,
        }, status_code=200 if database_reachable and auth_configured else 503)

    def prepare_help(session_id: str, response: AnalyzeResponse) -> None:
        for mistake in response.mistakes:
            # The student may well tap the duck next, and shouldn't wait for it.
            tracked = state.mistakes.get_mistake(session_id, mistake.id) if mistake.is_new else None
            if tracked is not None:
                state.help.prepare(session_id, tracked)

    def remember_what_was_found(
        user_id: str, study_session_id: str | None, response: AnalyzeResponse, check_session_id: str
    ) -> None:
        """What the verifier established, kept for checking what the client later says happened."""

        accounts = state.accounts
        assert accounts is not None
        if response.stale:
            return
        for mistake in response.mistakes:
            topic = topic_of(mistake.category, mistake.error_type, mistake.parsed_operation)
            tracked = state.mistakes.get_mistake(check_session_id, mistake.id)
            accounts.store.record_mistake_fact(
                user_id, mistake.id, mistake.category, mistake.error_type, mistake.parsed_operation, topic,
                f"{mistake.previous_step} = {mistake.current_step}".strip(" ="),
                written_step=_as_written(mistake.category, mistake.previous_step, mistake.current_step),
                corrected_step=_as_corrected(mistake.category, mistake.error_type, mistake.previous_step, tracked),
            )
        accounts.store.mark_resolved(user_id, response.resolved_mistake_ids, "verified")
        if study_session_id and accounts.store.session(user_id, study_session_id):
            accounts.store.touch_session(user_id, study_session_id, checked=not response.model_issue)

    def note_help(
        identity: Identity | None, study_session_id: str | None, mistake_id: str, kind: str, level: int, text: str
    ) -> None:
        if identity and state.accounts:
            state.accounts.learning.help_given(identity.user_id, study_session_id, mistake_id, kind, level, text)

    async def check_practice_answer(user_id: str, problem: dict, body: AnalyzeRequest) -> PracticeCheckResponse:
        """The normal pipeline, with the question placed in front of what was written."""

        accounts = state.accounts
        assert accounts is not None
        request = body.model_copy(update={"session_id": f"{user_id}:practice-{problem['id']}", "study_session_id": None})
        writer = await asyncio.to_thread(accounts.writer_of, user_id) if state.analysis.wants_writer(request) else None
        try:
            method = problem["verification_method"]
            # `If x = 3, work out 5x + 4`: what's written follows from 5x + 4, with x standing for 3.
            values, expression = lessons.question_context(problem)
            response = await state.analysis.analyze(
                request,
                writer,
                answer_lines(expression or problem["prompt_math"], values),
                given=problem["prompt_math"] if method == EQUATION else "",
                intent=INTENT_BY_METHOD.get(method, "auto"),
                values=values,
            )
        except ImageRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        prepare_help(request.session_id, response)
        await asyncio.to_thread(remember_what_was_found, user_id, problem["session_id"], response, request.session_id)

        outcome: Literal["in_progress", "incorrect", "correct"] = "in_progress"
        all_read = {line.id for line in body.ink_lines or []} <= set(response.settled_line_ids)
        if response.mistakes:
            outcome = "incorrect"
        elif response.status == "no_error" and not response.stale and response.lines:
            # Two readings have to agree on all of it, and the last line has to be the finished answer.
            # `x = 3` written last restates the question, and isn't the answer to it.
            letters = {letter for letter, _ in values}
            answer = next(
                (line.text for line in reversed(response.lines)
                 if line.kind == "math" and not (letters and states_a_value(line.text, letters))),
                None,
            )
            # A crossed-out line only stops counting once something has been written under it. Struck out last,
            # with nothing beneath, the line above it isn't thereby the answer.
            last_written = next((line for line in reversed(response.lines) if line.kind != "prose"), None)
            if last_written is not None and last_written.kind == "crossed_out":
                answer = None
            # Factorising asks for a form, not just a value: 6x + 9 written again is true and isn't an answer.
            form = FORM_OF_METHOD.get(method, "value")
            if all_read and answer and await shared_pool.completes(
                problem["prompt_math"], problem["expected_answer"], answer, state.settings.verify_timeout_s, form
            ):
                outcome = "correct"
        # Writing out an answer that's already been shown is fine, and it isn't getting it right.
        if outcome == "correct" and problem["status"] == "revealed":
            outcome = "in_progress"
        misconception = None
        if problem["course_id"] and response.mistakes and not response.stale:
            misconception = await lessons.note_mistakes(
                accounts.store, user_id, problem, response.mistakes, state.settings.verify_timeout_s
            )
        if outcome == "correct":
            await asyncio.to_thread(record_practice_done, user_id, problem)
            if problem["course_id"]:
                await asyncio.to_thread(lessons.after_correct, accounts.store, user_id, problem)
        elif outcome == "incorrect":
            await asyncio.to_thread(accounts.store.note_attempt, user_id, problem["id"])
        return PracticeCheckResponse(
            **response.model_dump(), outcome=outcome, misconception=misconception, all_read=all_read
        )

    def record_practice_done(user_id: str, problem: dict) -> None:
        accounts = state.accounts
        assert accounts is not None
        accounts.store.set_problem_status(user_id, problem["id"], "correct")
        accounts.store.add_event(
            user_id,
            session_id=problem["session_id"],
            event_type="practice_completed",
            source="lesson" if problem["course_id"] else "practice",
            dedupe_key=f"practice_completed:{problem['id']}",
            problem_id=problem["id"],
            topic=problem["topic"],
        )

    if state.accounts is not None:
        state.accounts.run_practice_check = check_practice_answer

    @app.post("/api/analyze", dependencies=[Depends(rate_limited)])
    async def analyze(body: AnalyzeRequest, identity: Identity | None = Depends(requester)) -> AnalyzeResponse:  # noqa: B008
        request = body.model_copy(update={"session_id": scoped(identity, body.session_id)})
        writer = None
        if identity and state.accounts and state.analysis.wants_writer(request):
            writer = await asyncio.to_thread(state.accounts.writer_of, identity.user_id)
        try:
            response = await state.analysis.analyze(request, writer)
        except ImageRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        prepare_help(request.session_id, response)
        if identity and state.accounts:
            await asyncio.to_thread(
                remember_what_was_found, identity.user_id, body.study_session_id, response, request.session_id
            )
        return response

    @app.post("/api/clear", dependencies=[Depends(rate_limited)])
    async def clear(
        body: ClearRequest, identity: Identity | None = Depends(requester)  # noqa: B008
    ) -> dict[str, list[str]]:
        # The ink is gone, so there is nothing to read. Saves a model call, and re-arms the
        # alert in case the student later writes the same mistake again.
        decision = state.mistakes.clear(
            scoped(identity, body.session_id), body.snapshot_version, body.acknowledged_mistake_ids
        )
        if identity and state.accounts:
            await asyncio.to_thread(
                state.accounts.store.mark_resolved, identity.user_id, list(decision.resolved_ids), "erased"
            )
        return {"resolvedMistakeIds": list(decision.resolved_ids)}

    @app.post("/api/hint", dependencies=[Depends(rate_limited)])
    async def hint(body: HintRequest, identity: Identity | None = Depends(requester)) -> AssistanceResponse:  # noqa: B008
        started = time.perf_counter()
        session_id = scoped(identity, body.session_id)
        mistake = find_mistake(session_id, body.mistake_id)
        said = mistake.hints_by_level.get(body.level)
        if said is not None:
            return _assistance(body.level, said[0], said[1], None, started)  # type: ignore[arg-type]
        text, source, error = await state.help.get(session_id, mistake, body.level)
        state.mistakes.add_hint(session_id, body.mistake_id, text, body.level, source)
        await asyncio.to_thread(
            note_help, identity, body.study_session_id, body.mistake_id, "hint_requested", body.level, text
        )
        return _assistance(body.level, text, source, error, started)

    # Explanations being written, so that asking again while waiting doesn't pay for a second one.
    explaining: dict[tuple[str, str], asyncio.Task[tuple[str, Literal["model", "template"], ModelError | None]]] = {}

    @app.post("/api/explain", dependencies=[Depends(rate_limited)])
    async def explain(body: ExplainRequest, identity: Identity | None = Depends(requester)) -> AssistanceResponse:  # noqa: B008
        started = time.perf_counter()
        session_id = scoped(identity, body.session_id)
        mistake = find_mistake(session_id, body.mistake_id)
        # The same mistake explained again says the same thing. A question of the student's own is new.
        said = mistake.hints_by_level.get(3) if body.question is None else None
        if said is not None:
            return _assistance(3, said[0], said[1], None, started)  # type: ignore[arg-type]
        key = (session_id, body.mistake_id)
        task = explaining.get(key) if body.question is None else None
        if task is None:
            task = asyncio.ensure_future(
                generate_help(state.router, 3, mistake, question=body.question, session_id=session_id)
            )
            if body.question is None:
                explaining[key] = task

                def remember(done: asyncio.Task[tuple[str, Literal["model", "template"], ModelError | None]]) -> None:
                    explaining.pop(key, None)
                    if not done.cancelled() and done.exception() is None and done.result()[2] is None:
                        state.mistakes.add_hint(session_id, body.mistake_id, done.result()[0], 3, done.result()[1])

                task.add_done_callback(remember)
        try:
            text, source, error = await asyncio.wait_for(asyncio.shield(task), EXPLAIN_DEADLINE_S)
        except TimeoutError:
            # What the verifier established, worded without a model. The model's may still arrive for next time.
            text, source, error = template_text(3, mistake), "template", ModelError("timeout", "explanation took too long")
        await asyncio.to_thread(
            note_help, identity, body.study_session_id, body.mistake_id, "explanation_requested", 3, text
        )
        return _assistance(3, text, source, error, started)

    @app.post("/api/ask", dependencies=[Depends(rate_limited)])
    async def ask(body: AskRequest, identity: Identity | None = Depends(requester)) -> AskResponse:  # noqa: B008
        """Help with what the student has circled. Records nothing. See services/ask.py."""

        started = time.perf_counter()
        session_id = scoped(identity, body.session_id)
        question = (body.question or "").strip() or None
        # Already known to be wrong, there's nothing new to find, and its facts are on record.
        known = state.mistakes.get_mistake(session_id, body.mistake_id) if body.mistake_id else None
        if known is not None:
            finding = SelectionFinding(
                "incorrect",
                StepResult(
                    "invalid", known.operation_type, 0, error_category=known.category, error_type=known.error_type,
                    previous_step=known.previous_step, current_step=known.current_step, expected=known.expected,
                    evidence=known.evidence, fingerprint=known.id.removeprefix("m_"),
                    parsed_operation=known.parsed_operation, term_facts=known.term_facts, extra_terms=known.extra_terms,
                ),
                (f"{known.previous_step} = {known.current_step}",),
            )
            lines: list[ReadLineSummary] = []
            unread: list[str] = []
            clarification, timer = None, None
        else:
            references: tuple = ()
            if identity and state.accounts and state.settings.personalization != "off" and any(
                line.in_image for line in body.ink_lines
            ):
                writer = await asyncio.to_thread(state.accounts.writer_of, identity.user_id)
                if writer is not None:
                    references = select_references(writer.samples, [], state.settings.references_per_reading)
            try:
                finding, lines, unread, timer, clarification = await state.analysis.inspect_selection(
                    body, session_id, references
                )
            except ImageRejected as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except ModelError as exc:
                return AskResponse(
                    verdict="unclear",
                    disposition="transcription_uncertain",
                    text="I can't read this right now. Your notes are safe, so try again in a moment.",
                    source="template",
                    model_issue=model_issue(exc),
                )
        help_given = await help_for(
            state.router, body.action, finding, question=question, level=body.level, session_id=session_id
        )
        step = finding.step
        disposition = disposition_of(step) if step else {
            "ambiguous_intent": "ambiguous_intent", "unclear": "transcription_uncertain", "unsupported": "unsupported",
        }.get(finding.verdict, "needs_clarification")
        return AskResponse(
            verdict=finding.verdict,
            disposition=disposition,
            text=help_given.text,
            source=help_given.source,
            explanation=StepExplanation(
                concept=help_given.explanation.concept,
                title=help_given.explanation.title,
                steps=list(help_given.explanation.steps),
                takeaway=help_given.explanation.takeaway,
            ) if help_given.explanation else None,
            level=help_given.level,
            lines=lines,
            clarification=clarification,
            unread_line_ids=unread,
            model_issue=model_issue(help_given.error) if help_given.error else None,
            timings=Timings(
                interpret_ms=round(timer.interpret_ms, 1) if timer else 0,
                verify_ms=round(timer.verify_ms, 1) if timer else 0,
                total_ms=round((time.perf_counter() - started) * 1000, 1),
                model_requests=timer.model_requests if timer else 0,
                cost_usd=round(timer.cost_usd, 6) if timer else 0,
                cache_hit=timer.cache_hit if timer else False,
                calls=list(timer.calls) if timer else [],
            ),
        )

    @app.post("/api/voice", dependencies=[Depends(rate_limited)])
    async def voice(
        audio: UploadFile = File(...),  # noqa: B008
        session_id: str = Form(..., alias="sessionId", min_length=1, max_length=64),
        mistake_id: str | None = Form(None, alias="mistakeId", max_length=64),
        study_session_id: str | None = Form(None, alias="studySessionId", max_length=64),
        identity: Identity | None = Depends(requester),  # noqa: B008
    ) -> VoiceResponse:
        session_id = scoped(identity, session_id)
        raw = await audio.read(MAX_AUDIO_BYTES + 1)
        if len(raw) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="recording too long")
        try:
            transcript = await transcribe_wav(state.router, raw, session_id=session_id)
            intent = await classify_intent(state.router, transcript, session_id=session_id)
        except VoiceRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ModelError as exc:
            return VoiceResponse(transcript="", intent="other", question="", model_issue=model_issue(exc))

        question = intent.question[:MAX_QUESTION_CHARS]
        response = VoiceResponse(transcript=transcript, intent=intent.intent, question=question)
        mistake = state.mistakes.get_mistake(session_id, mistake_id) if mistake_id else None
        if mistake is None or intent.intent not in ("hint", "explain"):
            return response
        given = len(mistake.hints_given)
        level = 3 if intent.intent == "explain" else min(given + 1, 2)
        if level == 3:
            text, source, error = await generate_help(state.router, 3, mistake, question=question, session_id=session_id)
        else:
            text, source, error = await state.help.get(session_id, mistake, level)  # type: ignore[arg-type]
            state.mistakes.add_hint(session_id, mistake.id, text, level, source)
        kind = "explanation_requested" if level == 3 else "hint_requested"
        await asyncio.to_thread(note_help, identity, study_session_id, mistake.id, kind, level, text)
        response.level, response.text, response.source = level, text, source
        response.model_issue = model_issue(error) if error else None
        return response

    if state.accounts is not None:
        # Recording what happened mustn't use up what a student's checks are allowed. These spend no
        # credit, and every check can bring an event with it.
        account_limiter = RateLimiter(
            state.settings.rate_limit_per_minute * ACCOUNT_ROUTE_ALLOWANCE,
            state.settings.global_rate_limit_per_minute * ACCOUNT_ROUTE_ALLOWANCE,
        )

        def account_rate_limited(request: Request) -> None:
            client = request.client.host if request.client else "unknown"
            if not account_limiter.allow(client):
                raise HTTPException(status_code=429, detail="too many requests")

        app.include_router(build_account_router(state.accounts, requester, account_rate_limited, rate_limited))
        # A course file that doesn't hold together stops the server here, not in front of a student.
        curriculum.load_all()
        app.include_router(build_learn_router(state.accounts, requester, account_rate_limited))
    else:

        @app.get("/api/auth/config")
        async def auth_config() -> AuthConfig:
            return AuthConfig(mode="off")

    @app.get("/api/session/{session_id}")
    async def session(session_id: str) -> SessionSummary:
        # Development inspection only. The app never calls this, and a session id is the only
        # credential protecting a student's working.
        if not state.settings.is_development:
            raise HTTPException(status_code=404)
        current = state.mistakes.get_session(session_id)
        if current is None:
            raise HTTPException(status_code=404, detail="unknown session")
        return SessionSummary(
            session_id=session_id,
            latest_version=current.latest_version,
            known_mistakes=[_public_mistake(mistake) for mistake in current.known.values()],
            resolved=[ResolvedEvent(mistake_id=r.mistake_id, category=r.category, resolved_at=r.resolved_at)
                      for r in current.resolved],
            alerts=current.alerts,
            spend_usd=round(state.ledger.session_spend(session_id), 6),
        )

    @app.get("/api/dev/trace/{session_id}")
    async def dev_trace(session_id: str) -> list[dict]:
        # Why each line was or wasn't flagged, for the last few checks. In memory only.
        if not state.settings.is_development:
            raise HTTPException(status_code=404)
        return state.analysis.traces(session_id)

    @app.get("/api/dev/usage")
    async def dev_usage() -> dict:
        if not state.settings.is_development:
            raise HTTPException(status_code=404)
        return summarise(state.settings.ledger_path, state.ledger)

    return app
