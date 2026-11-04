"""Lessons: a level's questions, what they've shown, and what comes next. See services/adaptive.py.

A lesson question is a practice problem with its course, level, skill and template recorded, so it's
answered and checked exactly as practice is. What a question showed is read back from records that
already exist: its status (only the verifier sets it to right), the learning events the client sent
about what the student was shown, and the misconception named for each verified mistake on it.
Nothing here trusts the client about whether an answer was right.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import uuid
from dataclasses import dataclass
from typing import Any

from quack.schemas import (
    ContinueOut,
    CourseOut,
    LearnHome,
    LessonContentOut,
    LessonExampleOut,
    LessonQuestionOut,
    LessonRecap,
    LessonState,
    LevelOut,
    LevelRef,
    LevelStatus,
    MasteryOut,
    MisconceptionOut,
    PathOut,
    RecapMisconception,
    SelectionOut,
)
from quack.services import curriculum
from quack.services.adaptive import Attempt, MasteryState, Selection, assess, choose_next
from quack.services.curriculum import Course, Level
from quack.services.lesson_questions import TEMPLATES, LessonQuestion, new_lesson_question, templates_for
from quack.services.misconceptions import TAXONOMY
from quack.services.user_store import UserStore


class LessonRefused(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class LevelView:
    level: Level
    status: LevelStatus
    mastery: MasteryState
    attempts: list[Attempt]


def misconception_out(misconception_id: str) -> MisconceptionOut | None:
    found = TAXONOMY.get(misconception_id)
    return MisconceptionOut(id=found.id, label=found.label, idea=found.idea) if found else None


def mastery_out(state: MasteryState) -> MasteryOut:
    return MasteryOut(
        mastered=state.mastered,
        credits=state.credits,
        credits_needed=state.credits_needed,
        distinct_templates=state.distinct_templates,
        distinct_templates_needed=state.distinct_templates_needed,
        distinct_skills=state.distinct_skills,
        distinct_skills_needed=state.distinct_skills_needed,
        independent=state.independent,
        independent_needed=state.independent_needed,
        independent_at=state.independent_at,
        independent_at_met=state.independent_at_met,
        open_misconceptions=[m for item in state.open_misconceptions if (m := misconception_out(item))],
        asked=state.asked,
        corrected=state.corrected,
        assisted=state.assisted,
        can_move_on=state.can_move_on,
    )


def attempts_of(store: UserStore, user_id: str, problems: list[dict[str, Any]]) -> list[Attempt]:
    """What each question showed, from its own record. The flags on `problem_mistakes` are set as the
    events arrive, so deleting a mistake from My Mistakes, which deletes its events, changes nothing here."""

    links = store.problem_mistake_links(user_id, [problem["id"] for problem in problems])
    attempts = []
    for problem in problems:
        mine = [link for link in links if link["problem_id"] == problem["id"]]
        # Shown to the student, and not since found by two readings to have been a misreading.
        mistakes = tuple(
            (link["misconception"], link["confidence"]) for link in mine if link["shown"] and not link["misread"]
        )
        assisted = bool(problem["assisted"]) or any(link["helped"] for link in mine)
        attempts.append(
            Attempt(
                problem_id=problem["id"],
                session_id=problem["session_id"],
                template=problem["template_id"],
                skill=problem["skill_id"],
                tier=problem["difficulty"],
                status=problem["status"],
                mistakes=mistakes,
                assisted=assisted,
                selection_kind=problem["selection_kind"] or "start",
                target=problem["selection_target"],
            )
        )
    return attempts


def _course(course_id: str) -> Course:
    found = curriculum.course(course_id)
    if found is None:
        raise LessonRefused(404, "unknown course")
    return found


def _level(course: Course, level_id: str) -> Level:
    found = course.level(level_id)
    if found is None:
        raise LessonRefused(404, "unknown level")
    return found


def level_views(store: UserStore, user_id: str, course: Course) -> dict[str, LevelView]:
    problems = store.course_problems(user_id, course.id)
    attempts = attempts_of(store, user_id, problems)
    progress = store.level_progress(user_id, course.id)
    views: dict[str, LevelView] = {}
    for level in course.levels:
        mine = [a for a, p in zip(attempts, problems, strict=True) if p["level_id"] == level.id]
        mastery = assess(level, mine)
        record = progress.get(level.id, {})
        ready = all(views[before].status in ("mastered", "moved_on") for before in level.prerequisites if before in views)
        if record.get("mastered_at") or mastery.mastered:
            status: LevelStatus = "mastered"
        elif record.get("moved_on_at"):
            status = "moved_on"
        elif not ready:
            status = "locked"
        else:
            status = "in_progress" if mine else "available"
        views[level.id] = LevelView(level, status, mastery, mine)
    return views


def _ref(level: Level) -> LevelRef:
    return LevelRef(id=level.id, number=level.number, title=level.title)


def course_out(store: UserStore, user_id: str, course_id: str) -> CourseOut:
    course = _course(course_id)
    views = level_views(store, user_id, course)
    levels = []
    for level in course.levels:
        view = views[level.id]
        lesson = level.lesson
        levels.append(
            LevelOut(
                id=level.id,
                number=level.number,
                kind=level.kind,
                title=level.title,
                objective=level.objective,
                skills=[skill.title for skill_id in level.skills if (skill := course.skill(skill_id))],
                prerequisites=[_ref(before) for before_id in level.prerequisites if (before := course.level(before_id))],
                status=view.status,
                mastery=mastery_out(view.mastery),
                lesson=LessonContentOut(
                    intro=lesson.intro,
                    steps=lesson.steps,
                    example=LessonExampleOut(**lesson.example.model_dump()),
                    remember=lesson.remember,
                ),
                tiers=level.tiers,
            )
        )
    mastered = sum(view.status == "mastered" for view in views.values())
    return CourseOut(
        id=course.id, version=course.version, title=course.title, tagline=course.tagline, levels=levels,
        mastered=mastered, total=len(course.levels),
    )


def home(store: UserStore, user_id: str) -> LearnHome:
    paths = []
    for entry in curriculum.catalog().paths:
        found = curriculum.course(entry.id)
        if found is None:
            paths.append(
                PathOut(id=entry.id, title=entry.title, tagline=entry.tagline, status="coming_later", preview=entry.preview)
            )
            continue
        views = level_views(store, user_id, found)
        paths.append(
            PathOut(
                id=entry.id, title=entry.title, tagline=entry.tagline, status="available", preview=entry.preview,
                mastered=sum(view.status == "mastered" for view in views.values()), total=len(found.levels),
            )
        )
    return LearnHome(paths=paths, continue_learning=_continue(store, user_id))


def _continue(store: UserStore, user_id: str) -> ContinueOut | None:
    """The level of the lesson last used. If it's been mastered since, the next one that's open."""

    latest = store.latest_lesson_session(user_id)
    if latest is None or not latest["topic"] or "/" not in latest["topic"]:
        return None
    course_id, level_id = latest["topic"].split("/", 1)
    course = curriculum.course(course_id)
    if course is None or course.level(level_id) is None:
        return None
    views = level_views(store, user_id, course)
    level = course.level(level_id)
    assert level is not None
    if views[level_id].status == "mastered":
        upcoming = next(
            (other for other in course.levels if views[other.id].status in ("available", "in_progress")), None
        )
        if upcoming is not None:
            level = upcoming
    return ContinueOut(
        course_id=course.id, course_title=course.title, level_id=level.id, level_title=level.title,
        level_number=level.number, status=views[level.id].status,
    )


def _question_out(course: Course, problem: dict[str, Any], number: int) -> LessonQuestionOut:
    # Written out field by field, so that the expected answer can't come along by accident.
    skill = course.skill(problem["skill_id"])
    return LessonQuestionOut(
        id=problem["id"],
        session_id=problem["session_id"],
        check_session_id=f"practice-{problem['id']}",
        instruction=problem["instruction"] or "",
        prompt=problem["prompt_display"],
        skill=problem["skill_id"],
        skill_label=skill.title if skill else problem["skill_id"],
        tier=problem["difficulty"],
        number=number,
        status=problem["status"],
        nudges=len(TEMPLATES[problem["template_id"]].hints),
    )


def _selection_out(problem: dict[str, Any], development: bool) -> SelectionOut | None:
    if not problem["selection_kind"]:
        return None
    basis = json.loads(problem["selection_json"] or "{}") if development else {}
    target = misconception_out(problem["selection_target"]) if problem["selection_target"] else None
    return SelectionOut(kind=problem["selection_kind"], reason=problem["selection_reason"] or "", target=target, basis=basis)


def _next_level(course: Course, views: dict[str, LevelView], level: Level) -> LevelRef | None:
    for other in course.levels:
        if level.id in other.prerequisites and views[other.id].status in ("available", "in_progress"):
            return _ref(other)
    return None


def record_mastery(store: UserStore, user_id: str, course: Course, level: Level, session_id: str | None) -> bool:
    """Marks a level mastered the first time its rule is met. True only that once."""

    views = level_views(store, user_id, course)
    if not views[level.id].mastery.mastered:
        return False
    first = store.mark_level(user_id, course.id, level.id, "mastered_at")
    if first and session_id:
        store.add_event(
            user_id, session_id=session_id, event_type="level_mastered", source="lesson",
            dedupe_key=f"level_mastered:{course.id}:{level.id}", topic=level.id,
        )
    return first


def state(
    store: UserStore, user_id: str, course: Course, level: Level, session: dict[str, Any], development: bool
) -> LessonState:
    views = level_views(store, user_id, course)
    view = views[level.id]
    in_visit = [problem for problem in store.problems_in(user_id, session["id"]) if problem["level_id"] == level.id]
    current = in_visit[-1] if in_visit else None
    return LessonState(
        course_id=course.id,
        level_id=level.id,
        level_number=level.number,
        level_title=level.title,
        session_id=session["id"],
        question=_question_out(course, current, len(in_visit)) if current else None,
        selection=_selection_out(current, development) if current else None,
        mastery=mastery_out(view.mastery),
        status=view.status,
        next_level=_next_level(course, views, level) if view.status in ("mastered", "moved_on") else None,
    )


def open_session(store: UserStore, user_id: str, course_id: str, level_id: str) -> tuple[Course, Level, dict[str, Any]]:
    """The visit to this level that's under way, or a new one. A level whose prerequisites aren't done is refused."""

    course = _course(course_id)
    level = _level(course, level_id)
    if level_views(store, user_id, course)[level.id].status == "locked":
        raise LessonRefused(403, "level locked")
    with store.database.transaction():
        session = store.open_lesson_session(user_id, course.id, level.id) or store.new_lesson_session(
            user_id, course.id, level.id
        )
    return course, level, session


def lesson_session(store: UserStore, user_id: str, session_id: str) -> tuple[Course, Level, dict[str, Any]]:
    session = store.session(user_id, session_id)
    if session is None or session["kind"] != "lesson" or "/" not in (session["topic"] or ""):
        raise LessonRefused(404, "unknown session")
    course_id, level_id = session["topic"].split("/", 1)
    course = _course(course_id)
    return course, _level(course, level_id), session


def plan_next(
    store: UserStore, user_id: str, course: Course, level: Level, session: dict[str, Any]
) -> tuple[Selection, set[str], random.Random, int]:
    problems = store.level_problems(user_id, course.id, level.id)
    history = attempts_of(store, user_id, problems)
    in_visit = [problem for problem in problems if problem["session_id"] == session["id"]]
    # The same visit and place in it always give the same choice, which is what makes it testable.
    rng = random.Random(f"{session['id']}:{len(in_visit)}")
    selection = choose_next(level, history, session["id"], rng)
    # Never what's asked already, and never what the lesson itself works through.
    avoid = {problem["prompt_math"] for problem in problems} | set(level.lesson.shown)
    return selection, avoid, rng, len(in_visit)


async def generate(
    level: Level, selection: Selection, avoid: set[str], rng: random.Random, timeout_s: float
) -> tuple[Selection, LessonQuestion]:
    """A checkable question for the selection, or for another template at its tier if that one has run dry."""

    opener = level.opener
    fixed = dict(opener.params) if selection.kind == "start" and opener and selection.template == opener.template else None
    question = await new_lesson_question(selection.template, rng, avoid, timeout_s, fixed)
    if question is not None:
        return selection, question
    for template in templates_for(level.skills, selection.tier):
        if template.id == selection.template:
            continue
        question = await new_lesson_question(template.id, rng, avoid, timeout_s)
        if question is not None:
            basis = selection.basis | {"fallback_from": selection.template}
            instead = Selection(selection.kind, template.id, selection.tier, selection.reason, selection.target, basis)
            return instead, question
    raise LessonRefused(503, "no question available")


def question_columns(question: LessonQuestion) -> dict[str, Any]:
    """What a stored problem keeps of the question itself, apart from where and why it was asked."""

    context = {"values": [list(pair) for pair in question.values], "expression": question.expression}
    return {
        "topic": question.question.topic,
        "difficulty": question.tier,
        "prompt_math": question.question.math,
        "prompt_display": question.question.display,
        "expected_answer": question.question.expected,
        "verification_method": question.method,
        "skill_id": question.skill,
        "template_id": question.template,
        "instruction": question.instruction,
        "probes_json": json.dumps(question.probes),
        "context_json": json.dumps(context) if question.values else None,
    }


def question_context(problem: dict[str, Any]) -> tuple[tuple[tuple[str, str], ...], str]:
    """The values a question gives its letters, and the expression they go into, or nothing.

    Quack's own data, never a model's. A substitution question asked before these were stored has them
    only in its wording, `If x = −4, work out x² + 6x`, so they're rebuilt from the template, which
    is the one place that wording comes from.
    """

    stored = problem.get("context_json")
    if stored:
        context = json.loads(stored)
        return tuple((letter, value) for letter, value in context["values"]), context["expression"]
    template = problem.get("template_id") or ""
    if not template.startswith("substitution."):
        return (), ""
    match = _SUBSTITUTION_WORDING.fullmatch(problem.get("prompt_display") or "")
    if match is None:
        return (), ""
    plain = str.maketrans({"−": "-", "²": "^2"})
    values = tuple(
        (letter, number.translate(plain)) for letter, number in _GIVEN_VALUE.findall(match["given"])
    )
    return values, match["expression"].translate(plain)


_SUBSTITUTION_WORDING = re.compile(r"If (?P<given>.+?), work out (?P<expression>.+)")
_GIVEN_VALUE = re.compile(r"([a-z]) = (−?\d+)")


def save_question(
    store: UserStore, user_id: str, course: Course, level: Level, session: dict[str, Any], selection: Selection,
    question: LessonQuestion, after: str | None,
) -> bool:
    """Adds it, unless another request has already moved this visit on. False if so."""

    with store.database.transaction():
        store.lock_session(user_id, session["id"])
        in_visit = [p for p in store.problems_in(user_id, session["id"]) if p["level_id"] == level.id]
        latest = in_visit[-1]["id"] if in_visit else None
        if latest != after:
            return False
        if latest is not None:
            store.skip_problem(user_id, latest)
        store.add_problem(
            user_id,
            {
                "id": str(uuid.uuid4()),
                "session_id": session["id"],
                **question_columns(question),
                "course_id": course.id,
                "level_id": level.id,
                "selection_kind": selection.kind,
                "selection_reason": selection.reason,
                "selection_target": selection.target,
                "selection_json": json.dumps(
                    {"kind": selection.kind, "template": question.template, "tier": question.tier} | selection.basis
                ),
            },
        )
        store.touch_session(user_id, session["id"])
    return True


def nudge(store: UserStore, user_id: str, problem_id: str, level: int) -> tuple[str, bool]:
    problem = store.problem(user_id, problem_id)
    if problem is None or not problem["template_id"]:
        raise LessonRefused(404, "unknown problem")
    hints = TEMPLATES[problem["template_id"]].hints
    index = min(level, len(hints)) - 1
    # A nudge is help, so the question counts as answered with help. Asking can only lower its credit.
    store.mark_assisted(user_id, problem_id)
    return hints[index], index == len(hints) - 1


def move_on(store: UserStore, user_id: str, course_id: str, level_id: str) -> None:
    course = _course(course_id)
    level = _level(course, level_id)
    view = level_views(store, user_id, course)[level.id]
    if view.status in ("mastered", "moved_on"):
        return
    if not view.mastery.can_move_on:
        raise LessonRefused(409, "not yet")
    store.mark_level(user_id, course.id, level.id, "moved_on_at")


def recap(store: UserStore, user_id: str, course: Course, level: Level, session: dict[str, Any]) -> LessonRecap:
    """Counted from this visit's questions and events, and nothing else. Says no more than they show."""

    problems = [p for p in store.problems_in(user_id, session["id"]) if p["level_id"] == level.id]
    attempts = attempts_of(store, user_id, problems)
    events = store.lesson_events(user_id, [session["id"]])
    view = level_views(store, user_id, course)[level.id]
    asked = [a for a in attempts if a.status != "skipped" or a.mistakes]
    right_alone = sum(a.outcome == "independent" for a in attempts)
    corrected = sum(a.outcome == "self_corrected" for a in attempts)
    assisted = sum(a.outcome == "assisted" for a in attempts)
    revealed = sum(a.outcome == "revealed" for a in attempts)
    mistakes = sum(len(a.mistakes) for a in attempts)
    hints = sum(event["event_type"] in ("hint_requested", "explanation_requested") for event in events)
    hints += sum(bool(p["assisted"]) for p in problems)
    seen: list[str] = []
    for attempt in attempts:
        for misconception in attempt.confirmed:
            if misconception not in seen:
                seen.append(misconception)
    misconceptions = [
        RecapMisconception(label=TAXONOMY[m].label, cleared=m not in view.mastery.open_misconceptions) for m in seen
    ]
    skills = sorted({skill.title for a in attempts if (skill := course.skill(a.skill))})
    return LessonRecap(
        session_id=session["id"],
        level_title=level.title,
        skills=skills,
        asked=len(asked),
        right_alone=right_alone,
        corrected=corrected,
        assisted=assisted,
        revealed=revealed,
        mistakes=mistakes,
        hints=hints,
        misconceptions=misconceptions,
        mastery=mastery_out(view.mastery),
        status=view.status,
        message=_recap_message(level, view, right_alone, corrected, assisted),
    )


def _recap_message(level: Level, view: LevelView, right_alone: int, corrected: int, assisted: int) -> str:
    parts = []
    if right_alone:
        parts.append(f"{right_alone} right on your own")
    if corrected:
        parts.append(f"{corrected} put right after a slip")
    if assisted:
        parts.append(f"{assisted} with a little help")
    done = "You got " + ", ".join(parts) + "." if parts else "No questions finished this time."
    if view.status == "mastered":
        return f"{done} {level.title} is mastered."
    mastery = view.mastery
    progress = f"{mastery.credits:g} of {mastery.credits_needed:g} points towards mastering {level.title}."
    return f"{done} {progress}"


async def note_mistakes(
    store: UserStore, user_id: str, problem: dict[str, Any], mistakes: list[Any], timeout_s: float
) -> MisconceptionOut | None:
    """Names the misconception behind each verified mistake on a lesson question, once, in the verifier's sandbox.

    Returns the first confirmed one, for the student to see. The step's text came from a model's reading,
    so it's parsed with the safe parser in a process that can be killed, like any other verification.
    """

    from quack.services.misconceptions import Classification, classify
    from quack.services.verifier_pool import shared_pool

    links = await asyncio.to_thread(store.problem_mistake_links, user_id, [problem["id"]])
    known = {link["mistake_id"]: link for link in links}
    probes = tuple(tuple(probe) for probe in json.loads(problem["probes_json"] or "[]"))
    shown: MisconceptionOut | None = None
    for mistake in mistakes:
        link = known.get(mistake.id)
        if link is None:
            fallback = Classification("verified_error_unclassified", "unclassified", "classifier_failed")
            found = await shared_pool.call(
                classify,
                (
                    problem["skill_id"], problem["prompt_math"], problem["verification_method"], probes,
                    mistake.category, mistake.error_type, mistake.previous_step, mistake.current_step,
                ),
                timeout_s,
                fallback,
            )
            assert isinstance(found, Classification)
            await asyncio.to_thread(
                store.link_problem_mistake, user_id, problem["id"], mistake.id, found.misconception, found.confidence,
                found.evidence,
            )
            link = {"misconception": found.misconception, "confidence": found.confidence}
        if shown is None and link["confidence"] == "confirmed":
            shown = misconception_out(link["misconception"])
    return shown


def after_correct(store: UserStore, user_id: str, problem: dict[str, Any]) -> None:
    course = curriculum.course(problem["course_id"])
    level = course.level(problem["level_id"]) if course else None
    if course is not None and level is not None:
        record_mastery(store, user_id, course, level, problem["session_id"])
