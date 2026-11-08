"""Routes for accounts, calibration and learning history. All of it belongs to whoever's token it is.

No route takes a user id. The only way to say whose data is meant is to hold that user's token.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from quack.config import Settings
from quack.db.database import Database
from quack.schemas import (
    AnalyzeRequest,
    AuthConfig,
    CalibrationExport,
    CalibrationExportSample,
    CalibrationPromptOut,
    CalibrationState,
    CurrentSession,
    DeleteAccountResult,
    LearningEventBatch,
    LearningEventResult,
    LocalCredentials,
    LocalSession,
    MistakeEntryDetail,
    MistakeNotebook,
    PenStrokeInput,
    PracticeCheckResponse,
    PracticeOptions,
    PracticeProblem,
    PracticeReveal,
    PracticeStart,
    PracticeSummary,
    PracticeTopic,
    PracticeTopicOption,
    Profile,
    Recap,
    RecapDetail,
    RefusedEvent,
    SampleUpload,
    SessionHistory,
    SessionHistoryItem,
    StudySession,
)
from quack.services import mistake_notebook
from quack.services import practice as practice_service
from quack.services import recap as recap_service
from quack.services.calibration import (
    CalibrationSet,
    SampleRejected,
    check_sample,
    load_calibration_set,
    reference_image,
)
from quack.services.handwriting import ImageRejected
from quack.services.identity import (
    AccountRejected,
    Identity,
    LocalAccounts,
    SupabaseVerifier,
    TokenVerifier,
)
from quack.services.learning import ClientEvent, LearningLog
from quack.services.topics import topic_named
from quack.services.user_store import UserStore
from quack.services.writer_references import WriterProfile

log = logging.getLogger("quack.accounts")
MAX_WRITERS_REMEMBERED = 256


@dataclass
class AccountServices:
    settings: Settings
    store: UserStore
    verifier: TokenVerifier
    local: LocalAccounts | None
    calibration: CalibrationSet
    learning: LearningLog
    _writers: OrderedDict[str, WriterProfile | None] = field(default_factory=OrderedDict)
    _writers_lock: threading.Lock = field(default_factory=threading.Lock)

    def writer_of(self, user_id: str) -> WriterProfile | None:
        """The student's references, kept in memory so that a check doesn't wait on the database."""

        with self._writers_lock:
            if user_id in self._writers:
                self._writers.move_to_end(user_id)
                return self._writers[user_id]
        samples = self.store.samples(user_id)
        writer = WriterProfile.from_samples(user_id, samples) if samples else None
        with self._writers_lock:
            self._writers[user_id] = writer
            while len(self._writers) > MAX_WRITERS_REMEMBERED:
                self._writers.popitem(last=False)
        return writer

    def forget_writer(self, user_id: str) -> None:
        with self._writers_lock:
            self._writers.pop(user_id, None)

    # Set by main.py, which owns the pipeline.
    run_practice_check: Callable[..., Awaitable[PracticeCheckResponse]] = None  # type: ignore[assignment]


def build_account_services(settings: Settings) -> AccountServices | None:
    mode = settings.auth_mode
    if mode == "off":
        return None
    database = (
        Database(url=settings.database_url) if settings.database_url else Database(path=settings.sqlite_path)
    )
    local = None
    verifier: TokenVerifier
    if mode == "local":
        local = LocalAccounts(database, settings.sqlite_path.with_name("local_auth_secret"))
        verifier = local
    else:
        verifier = SupabaseVerifier(settings.supabase_url, settings.supabase_jwt_secret)
    store = UserStore(database)
    return AccountServices(settings, store, verifier, local, load_calibration_set(), LearningLog(store))


def _profile_out(accounts: AccountServices, user_id: str) -> Profile:
    row = accounts.store.profile(user_id)
    return Profile(
        user_id=user_id,
        onboarding_status=row["onboarding_status"],
        has_handwriting_samples=bool(accounts.store.samples(user_id)) if row["calibration_version"] else False,
    )


def _calibration_state(accounts: AccountServices, user_id: str) -> CalibrationState:
    current = accounts.calibration
    row = accounts.store.profile(user_id)
    return CalibrationState(
        version=current.version,
        prompts=[
            CalibrationPromptOut(id=p.id, display=p.display, captures=p.captures, min_strokes=p.min_strokes)
            for p in current.prompts
        ],
        completed_prompt_ids=accounts.store.saved_prompt_ids(user_id, current.version),
        onboarding_status=row["onboarding_status"],
    )


def _study_session(row: dict[str, Any]) -> StudySession:
    return StudySession(id=row["id"], started_at=row["started_at"], last_active_at=row["last_active_at"])


def _public_problem(row: dict[str, Any]) -> PracticeProblem:
    # Written out field by field, so that the expected answer can't come along by accident.
    return PracticeProblem(
        id=row["id"],
        session_id=row["session_id"],
        topic=row["topic"],
        topic_label=practice_label(row["topic"]),
        difficulty=row["difficulty"],
        prompt=row["prompt_display"],
        check_session_id=f"practice-{row['id']}",
    )


def practice_label(topic: str) -> str:
    return topic_named(topic).label


def _practice_summary(store: UserStore, user_id: str, session: dict[str, Any]) -> PracticeSummary:
    problems = store.problems_in(user_id, session["id"])
    events = store.session_events(user_id, session["id"])
    correct = sum(problem["status"] == "correct" for problem in problems)
    revealed = sum(problem["status"] == "revealed" for problem in problems)
    hints = sum(event["event_type"] in ("hint_requested", "explanation_requested") for event in events)
    label = practice_label(session["topic"])
    return PracticeSummary(
        session_id=session["id"],
        topic=session["topic"],
        topic_label=label,
        attempted=sum(problem["status"] != "open" for problem in problems),
        correct=correct,
        revealed=revealed,
        hints=hints,
        message=_practice_message(label, correct, len(problems), hints, revealed),
    )


def _practice_message(label: str, correct: int, attempted: int, hints: int, revealed: int) -> str:
    """Says what happened, and nothing it can't know. One right answer isn't mastery."""

    topic = label.lower()
    if correct == 0:
        return f"No {topic} questions finished this time. They'll be here when you want another go."
    if correct == 1:
        qualifier = "with some help" if hints else "with no help"
        return (
            f"You got one {topic} question right, {qualifier}. "
            "One question doesn't say much, so try another when you're ready."
        )
    if hints or revealed:
        return f"You got {correct} {topic} questions right, with some help along the way."
    return f"You got {correct} {topic} questions right with no hints. Try a harder example next time."


def _owned(store: UserStore, user_id: str, session_id: str | None) -> str | None:
    """A practice session only hangs off a session of the student's own."""

    return session_id if session_id and store.session(user_id, session_id) else None


def _general_topic(store: UserStore, user_id: str, origin: str | None) -> PracticeTopic:
    """What the recap recommended, if it did, and otherwise simply the first one on the list."""

    session = store.session(user_id, origin) if origin else None
    if session and session["recap_json"]:
        recommendation = Recap.model_validate_json(session["recap_json"]).recommendation
        if recommendation:
            for topic in practice_service.SUPPORTED:
                if topic == recommendation.topic:
                    return topic
    return practice_service.SUPPORTED[0]


def _delete_supabase_sign_in(settings: Settings, user_id: str) -> bool:
    if not settings.supabase_service_role_key:
        return False
    key = settings.supabase_service_role_key
    try:
        response = httpx.delete(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}",
            headers={"apikey": key, "authorization": f"Bearer {key}"},
            timeout=10,
        )
    except httpx.HTTPError:
        log.warning("sign_in_delete_failed")
        return False
    return response.is_success


def build_account_router(
    accounts: AccountServices,
    requester: Callable[..., Awaitable[Identity | None]],
    rate_limited: Callable[..., Any],
    spending_limited: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(prefix="/api", dependencies=[Depends(rate_limited)])
    settings = accounts.settings
    store = accounts.store

    async def signed_in(identity: Identity | None = Depends(requester)) -> Identity:  # noqa: B008
        assert identity is not None
        return identity

    you = Depends(signed_in)

    @router.get("/auth/config")
    async def auth_config() -> AuthConfig:
        if settings.auth_mode == "supabase":
            return AuthConfig(
                mode="supabase", supabase_url=settings.supabase_url, supabase_anon_key=settings.supabase_anon_key
            )
        return AuthConfig(mode="local")

    if accounts.local is not None:
        local = accounts.local

        @router.post("/auth/local/register")
        async def local_register(body: LocalCredentials) -> LocalSession:
            try:
                user_id, token = await asyncio.to_thread(local.register, body.email, body.password)
            except AccountRejected as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return LocalSession(user_id=user_id, access_token=token)

        @router.post("/auth/local/login")
        async def local_login(body: LocalCredentials) -> LocalSession:
            try:
                user_id, token = await asyncio.to_thread(local.sign_in, body.email, body.password)
            except AccountRejected as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
            return LocalSession(user_id=user_id, access_token=token)

    @router.get("/profile")
    async def profile(identity: Identity = you) -> Profile:
        return await asyncio.to_thread(_profile_out, accounts, identity.user_id)

    @router.get("/calibration")
    async def calibration(identity: Identity = you) -> CalibrationState:
        return await asyncio.to_thread(_calibration_state, accounts, identity.user_id)

    @router.put("/calibration/samples/{prompt_id}")
    async def save_sample(prompt_id: str, body: SampleUpload, identity: Identity = you) -> CalibrationState:
        prompt = accounts.calibration.prompt(prompt_id)
        if prompt is None:
            raise HTTPException(status_code=404, detail="unknown prompt")
        try:
            check_sample(prompt, body.strokes)
            image = await asyncio.to_thread(reference_image, body.image)
        except SampleRejected as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ImageRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        def save() -> CalibrationState:
            store.save_sample(
                identity.user_id,
                version=accounts.calibration.version,
                prompt_id=prompt.id,
                # The label is the server's own, whatever the upload says. It's the ground truth of the copy.
                reference_expression=prompt.reference,
                strokes_json=json.dumps([stroke.model_dump(by_alias=True) for stroke in body.strokes]),
                canvas_width=body.canvas_width,
                image_data=image,
                required=[p.id for p in accounts.calibration.prompts],
            )
            accounts.forget_writer(identity.user_id)
            return _calibration_state(accounts, identity.user_id)

        return await asyncio.to_thread(save)

    @router.get("/calibration/export")
    async def export_calibration(identity: Identity = you) -> CalibrationExport:
        samples = await asyncio.to_thread(store.samples, identity.user_id)
        return CalibrationExport(
            samples=[
                CalibrationExportSample(
                    prompt_id=sample.prompt_id,
                    reference_expression=sample.reference_expression,
                    image=sample.image_data,
                    strokes=[PenStrokeInput.model_validate(stroke) for stroke in json.loads(sample.strokes_json)],
                    canvas_width=sample.canvas_width,
                    created_at=sample.created_at,
                    calibration_version=sample.calibration_version,
                )
                for sample in samples
            ]
        )

    @router.delete("/calibration")
    async def delete_calibration(identity: Identity = you) -> Profile:
        def delete() -> Profile:
            store.delete_calibration(identity.user_id)
            accounts.forget_writer(identity.user_id)
            return _profile_out(accounts, identity.user_id)

        return await asyncio.to_thread(delete)

    @router.post("/calibration/restart")
    async def restart_calibration(identity: Identity = you) -> CalibrationState:
        def restart() -> CalibrationState:
            store.restart_calibration(identity.user_id)
            accounts.forget_writer(identity.user_id)
            return _calibration_state(accounts, identity.user_id)

        return await asyncio.to_thread(restart)

    # Sessions and recaps

    @router.post("/sessions/current")
    async def current_session(identity: Identity = you) -> CurrentSession:
        session, wrapped = await asyncio.to_thread(recap_service.current_session, store, identity.user_id)
        return CurrentSession(session=_study_session(session), wrapped_up=wrapped)

    # My Mistakes

    @router.get("/mistakes")
    async def list_mistakes(session: str | None = None, identity: Identity = you) -> MistakeNotebook:
        return await asyncio.to_thread(mistake_notebook.notebook, store, identity.user_id, session)

    @router.get("/mistakes/{mistake_id}")
    async def one_mistake(mistake_id: str, identity: Identity = you) -> MistakeEntryDetail:
        found = await asyncio.to_thread(mistake_notebook.detail, store, identity.user_id, mistake_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown mistake")
        return found

    @router.delete("/mistakes/{mistake_id}")
    async def delete_mistake(mistake_id: str, identity: Identity = you) -> dict[str, bool]:
        await asyncio.to_thread(store.delete_mistake, identity.user_id, mistake_id)
        return {"deleted": True}

    @router.post("/sessions/{session_id}/finish")
    async def finish_session(session_id: str, identity: Identity = you) -> Recap:
        def finish() -> Recap:
            session = store.session(identity.user_id, session_id)
            if session is None or session["kind"] != "notebook":
                raise HTTPException(status_code=404, detail="unknown session")
            recap = recap_service.finish(store, identity.user_id, session, "finished")
            return recap_service.phrased(recap, session["checks"])

        return await asyncio.to_thread(finish)

    @router.get("/sessions")
    async def list_sessions(identity: Identity = you) -> SessionHistory:
        def history() -> SessionHistory:
            items = []
            for row in store.finished_sessions(identity.user_id):
                recap = Recap.model_validate_json(row["recap_json"])
                items.append(
                    SessionHistoryItem(
                        session_id=row["id"],
                        started_at=row["started_at"],
                        finished_at=row["finished_at"],
                        found=recap.glance.found,
                        corrected=recap.glance.corrected,
                        topic_labels=[topic.label for group in recap.topics for topic in group.topics],
                    )
                )
            return SessionHistory(sessions=items)

        return await asyncio.to_thread(history)

    @router.get("/sessions/{session_id}/recap")
    async def session_recap(session_id: str, identity: Identity = you) -> RecapDetail:
        def detail() -> RecapDetail:
            session = store.session(identity.user_id, session_id)
            if session is None or not session["recap_json"]:
                raise HTTPException(status_code=404, detail="unknown session")
            return RecapDetail(
                recap=recap_service.phrased(Recap.model_validate_json(session["recap_json"]), session["checks"]),
                practice=[
                    _practice_summary(store, identity.user_id, practice)
                    for practice in store.practice_sessions_of(identity.user_id, session_id)
                ],
            )

        return await asyncio.to_thread(detail)

    @router.post("/learning/events")
    async def learning_events(body: LearningEventBatch, identity: Identity = you) -> LearningEventResult:
        def record() -> LearningEventResult:
            refused = []
            for event in body.events:
                reason = accounts.learning.record(
                    identity.user_id,
                    ClientEvent(
                        event.id, event.event_type, event.mistake_id, event.session_id, event.reason, event.rewritten
                    ),
                )
                if reason:
                    refused.append(RefusedEvent(id=event.id, reason=reason))
            return LearningEventResult(refused=refused)

        return await asyncio.to_thread(record)

    # Practice

    async def _new_problem(
        user_id: str, session: dict[str, Any], difficulty: str, source_mistake_id: str | None = None
    ) -> PracticeProblem:
        topic = session["topic"]
        asked = await asyncio.to_thread(store.problems_in, user_id, session["id"])
        question = await practice_service.new_question(
            topic, difficulty, {row["prompt_math"] for row in asked}, settings.verify_timeout_s
        )
        if question is None:
            # Nothing passed the verifier, so nothing is shown that couldn't be checked.
            raise HTTPException(status_code=503, detail="no question available")
        row = {
            "id": str(uuid.uuid4()),
            "session_id": session["id"],
            "topic": question.topic,
            "difficulty": question.difficulty,
            "prompt_math": question.math,
            "prompt_display": question.display,
            "expected_answer": question.expected,
            "verification_method": question.method,
            "source_mistake_id": source_mistake_id,
        }
        await asyncio.to_thread(store.add_problem, user_id, row)
        await asyncio.to_thread(
            lambda: store.add_event(
                user_id,
                session_id=session["id"],
                event_type="practice_started",
                source="practice",
                dedupe_key=f"practice_started:{row['id']}",
                problem_id=row["id"],
                topic=question.topic,
            )
        )
        return _public_problem(row)

    @router.get("/practice/topics")
    async def practice_topics() -> PracticeOptions:
        return PracticeOptions(
            topics=[PracticeTopicOption(topic=topic, label=practice_label(topic)) for topic in practice_service.SUPPORTED]
        )

    @router.post("/practice/sessions")
    async def start_practice(body: PracticeStart, identity: Identity = you) -> PracticeProblem:
        user_id = identity.user_id
        topic: PracticeTopic
        if body.topic is not None:
            topic = body.topic
        else:
            topic = await asyncio.to_thread(_general_topic, store, user_id, body.origin_session_id)
        origin = await asyncio.to_thread(_owned, store, user_id, body.origin_session_id)
        session = await asyncio.to_thread(store.new_practice_session, user_id, origin, topic)
        # Only one of the student's own. Anything else simply isn't linked.
        source = body.source_mistake_id
        if source and await asyncio.to_thread(store.mistake_fact, user_id, source) is None:
            source = None
        return await _new_problem(user_id, session, "basic", source)

    @router.post("/practice/sessions/{session_id}/next")
    async def next_problem(session_id: str, identity: Identity = you) -> PracticeProblem:
        user_id = identity.user_id
        session = await asyncio.to_thread(store.session, user_id, session_id)
        if session is None or session["kind"] != "practice":
            raise HTTPException(status_code=404, detail="unknown session")
        asked = await asyncio.to_thread(store.problems_in, user_id, session_id)
        # Only a little harder, and only after one that went well. There's no adaptive model here.
        last = asked[-1] if asked else None
        events = await asyncio.to_thread(store.session_events, user_id, session_id)
        went_well = last is not None and last["status"] == "correct" and not any(
            event["event_type"] in ("hint_requested", "explanation_requested") for event in events
        )
        source = last["source_mistake_id"] if last else None
        return await _new_problem(user_id, session, "intermediate" if went_well else "basic", source)

    @router.post("/practice/sessions/{session_id}/finish")
    async def finish_practice(session_id: str, identity: Identity = you) -> PracticeSummary:
        def finish() -> PracticeSummary:
            session = store.session(identity.user_id, session_id)
            if session is None or session["kind"] != "practice":
                raise HTTPException(status_code=404, detail="unknown session")
            if session["finished_at"] is None:
                store.database.execute(
                    "UPDATE study_sessions SET finished_at = ?, finish_reason = 'finished' "
                    "WHERE id = ? AND user_id = ?",
                    (time.time(), session_id, identity.user_id),
                )
            return _practice_summary(store, identity.user_id, session)

        return await asyncio.to_thread(finish)

    @router.post("/practice/problems/{problem_id}/check", dependencies=[Depends(spending_limited)])
    async def check_answer(problem_id: str, body: AnalyzeRequest, identity: Identity = you) -> PracticeCheckResponse:
        user_id = identity.user_id
        problem = await asyncio.to_thread(store.problem, user_id, problem_id)
        if problem is None:
            raise HTTPException(status_code=404, detail="unknown problem")
        return await accounts.run_practice_check(user_id, problem, body)

    @router.post("/practice/problems/{problem_id}/reveal")
    async def reveal_answer(problem_id: str, identity: Identity = you) -> PracticeReveal:
        def reveal() -> PracticeReveal:
            problem = store.problem(identity.user_id, problem_id)
            if problem is None:
                raise HTTPException(status_code=404, detail="unknown problem")
            store.set_problem_status(identity.user_id, problem_id, "revealed")
            return PracticeReveal(expected_answer=problem["expected_answer"])

        return await asyncio.to_thread(reveal)

    @router.delete("/account/learning")
    async def delete_learning(identity: Identity = you) -> dict[str, bool]:
        await asyncio.to_thread(store.delete_learning, identity.user_id)
        return {"deleted": True}

    @router.delete("/account")
    async def delete_account(identity: Identity = you) -> DeleteAccountResult:
        def delete() -> DeleteAccountResult:
            store.delete_everything(identity.user_id)
            accounts.forget_writer(identity.user_id)
            if accounts.local is not None:
                accounts.local.delete(identity.user_id)
                return DeleteAccountResult(sign_in_deleted=True)
            return DeleteAccountResult(sign_in_deleted=_delete_supabase_sign_in(settings, identity.user_id))

        return await asyncio.to_thread(delete)

    return router
