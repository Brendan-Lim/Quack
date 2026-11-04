"""Routes for learning paths and lessons. Everything belongs to whoever's token it is.

Nothing here can mark a question right or a level mastered: only the verifier, through the practice
check, can make an answer right, and mastery is counted from that. A client can ask for the next
question, a nudge, or to move on when the rules allow it, and that's all.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from quack.account_api import AccountServices
from quack.schemas import CourseOut, LearnHome, LessonNext, LessonRecap, LessonState, NudgeOut, NudgeRequest
from quack.services import lessons
from quack.services.identity import Identity


def build_learn_router(
    accounts: AccountServices,
    requester: Callable[..., Awaitable[Identity | None]],
    rate_limited: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(prefix="/api/learn", dependencies=[Depends(rate_limited)])
    store = accounts.store
    settings = accounts.settings
    development = settings.env == "development"

    async def signed_in(identity: Identity | None = Depends(requester)) -> Identity:  # noqa: B008
        assert identity is not None
        return identity

    you = Depends(signed_in)

    async def run(work: Callable[..., Any], *args: Any) -> Any:
        try:
            return await asyncio.to_thread(work, *args)
        except lessons.LessonRefused as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from exc

    async def advance(user_id: str, course: Any, level: Any, session: dict[str, Any], after: str | None) -> LessonState:
        """Moves the visit on from `after`, once. Two requests at once both build a question, and
        `save_question` keeps only the first, inside a transaction, so this holds across processes too."""

        asked = await asyncio.to_thread(store.problems_in, user_id, session["id"])
        in_visit = [problem for problem in asked if problem["level_id"] == level.id]
        latest = in_visit[-1]["id"] if in_visit else None
        if latest == after:
            selection, avoid, rng, _ = await run(lessons.plan_next, store, user_id, course, level, session)
            try:
                selection, question = await lessons.generate(level, selection, avoid, rng, settings.verify_timeout_s)
            except lessons.LessonRefused as exc:
                raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
            await run(lessons.save_question, store, user_id, course, level, session, selection, question, after)
        return await run(lessons.state, store, user_id, course, level, session, development)

    @router.get("")
    async def learn_home(identity: Identity = you) -> LearnHome:
        return await run(lessons.home, store, identity.user_id)

    @router.get("/courses/{course_id}")
    async def course(course_id: str, identity: Identity = you) -> CourseOut:
        return await run(lessons.course_out, store, identity.user_id, course_id)

    @router.post("/courses/{course_id}/levels/{level_id}/start")
    async def start(course_id: str, level_id: str, identity: Identity = you) -> LessonState:
        """The visit under way, where it was left, or a new one with its first question."""

        user_id = identity.user_id
        found, level, session = await run(lessons.open_session, store, user_id, course_id, level_id)
        current = await run(lessons.state, store, user_id, found, level, session, development)
        if current.question is not None:
            return current
        return await advance(user_id, found, level, session, None)

    @router.get("/sessions/{session_id}")
    async def lesson_state(session_id: str, identity: Identity = you) -> LessonState:
        found, level, session = await run(lessons.lesson_session, store, identity.user_id, session_id)
        return await run(lessons.state, store, identity.user_id, found, level, session, development)

    @router.post("/sessions/{session_id}/next")
    async def next_question(session_id: str, body: LessonNext, identity: Identity = you) -> LessonState:
        user_id = identity.user_id
        found, level, session = await run(lessons.lesson_session, store, user_id, session_id)
        if session["finished_at"] is not None:
            raise HTTPException(status_code=409, detail="lesson finished")
        return await advance(user_id, found, level, session, body.current_problem_id)

    @router.post("/sessions/{session_id}/finish")
    async def finish(session_id: str, identity: Identity = you) -> LessonRecap:
        def finish_it() -> LessonRecap:
            found, level, session = lessons.lesson_session(store, identity.user_id, session_id)
            store.finish_lesson_session(identity.user_id, session_id)
            return lessons.recap(store, identity.user_id, found, level, session)

        return await run(finish_it)

    @router.post("/problems/{problem_id}/nudge")
    async def nudge(problem_id: str, body: NudgeRequest, identity: Identity = you) -> NudgeOut:
        text, last = await run(lessons.nudge, store, identity.user_id, problem_id, body.level)
        return NudgeOut(level=body.level, text=text, last=last)

    @router.post("/problems/{problem_id}/assisted")
    async def assisted(problem_id: str, identity: Identity = you) -> dict[str, bool]:
        """Help from somewhere Quack doesn't record by itself, such as circling. It can only lower credit."""

        def mark() -> None:
            if store.problem(identity.user_id, problem_id) is None:
                raise lessons.LessonRefused(404, "unknown problem")
            store.mark_assisted(identity.user_id, problem_id)

        await run(mark)
        return {"assisted": True}

    @router.post("/courses/{course_id}/levels/{level_id}/move-on")
    async def move_on(course_id: str, level_id: str, identity: Identity = you) -> CourseOut:
        await run(lessons.move_on, store, identity.user_id, course_id, level_id)
        return await run(lessons.course_out, store, identity.user_id, course_id)

    if development and accounts.local is not None:
        # For browser tests and preparing a demo on local test accounts only. It never exists with real
        # accounts or outside development, like the debug trace. Moving on isn't mastery.
        @router.post("/dev/courses/{course_id}/levels/{level_id}/unlock")
        async def unlock_for_testing(course_id: str, level_id: str, identity: Identity = you) -> CourseOut:
            def unlock() -> None:
                found = lessons.curriculum.course(course_id)
                level = found.level(level_id) if found else None
                if found is None or level is None:
                    raise lessons.LessonRefused(404, "unknown level")
                waiting = list(level.prerequisites)
                while waiting:
                    before = found.level(waiting.pop())
                    if before is not None:
                        store.mark_level(identity.user_id, found.id, before.id, "moved_on_at")
                        waiting.extend(before.prerequisites)

            await run(unlock)
            return await run(lessons.course_out, store, identity.user_id, course_id)

    return router
