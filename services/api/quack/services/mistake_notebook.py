"""A student's own mistakes to look back on, counted from the same verified events as a recap.

There's nothing of its own to go stale or disagree with a recap. A mistake's id is a hash of what was
wrong, so writing it again is that entry again, and only an event the client says it showed counts.
Practice mistakes stay out: they're answers to Quack's questions, and they're counted with practice.
Lesson mistakes are in, with the level and skill they were made in, since a lesson is the student's own
working on the same canvas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from quack.schemas import MistakeEntry, MistakeEntryDetail, MistakeHint, MistakeLesson, MistakeNotebook, MistakeStatus
from quack.services import curriculum
from quack.services.misconceptions import TAXONOMY
from quack.services.topics import topic_named
from quack.services.user_store import UserStore

MAX_ENTRIES = 200
KIND_LABELS = {
    "lost_variable": "Lost the variable",
    "combining_like_terms": "Combining like terms",
    "incorrect_distribution": "Multiplying out a bracket",
    "power_of_sum_expanded_termwise": "A power of a bracket",
    "sign_error": "A sign",
    "fraction_addition": "Adding fractions",
    "power_rule_coefficient": "The number in front",
    "power_rule_exponent": "The power",
    "derivative_sign": "A sign in the derivative",
    "constant_not_dropped": "The constant",
    "constant_added_to_derivative": "A constant in the derivative",
    "missing_term": "A missing term",
    "derivative_of_sine": "The derivative of sine",
    "derivative_of_cosine": "The derivative of cosine",
    "derivative_of_tangent": "The derivative of tangent",
    "derivative_of_exponential": "The derivative of e^x",
    "derivative_of_logarithm": "The derivative of ln",
    "chain_rule_inner_missing": "The inside function",
    "product_rule_not_applied": "A product",
    "quotient_rule_not_applied": "A quotient",
    "integral_without_variable": "The variable in the answer",
    "integral_coefficient": "The number in front",
    "integral_exponent": "The power",
    "derivative_instead_of_integral": "Which way to go",
    "incorrect_antiderivative": "The antiderivative",
    "incorrect_definite_integral": "Using the limits",
    "sign_when_moving_term": "Moving a term across",
    "inverse_of_multiplication": "Undoing a multiplication",
    "inverse_of_division": "Undoing a division",
    "solution_set_changed": "Keeping the equation balanced",
    "rounding_error": "Rounding",
    "incorrect_factorisation": "The factors",
    "dividing_powers": "Dividing powers",
    "power_of_a_power": "A power of a power",
    "exponent_rule": "Multiplying powers",
    "variable_changed": "A letter that changed",
    "term_letter_changed": "A term copied with another letter",
    "terms_left_out": "Terms left out",
    "sign_lost_in_substitution": "The sign of a value put in",
    "sign_lost_value_unchanged": "The sign of a value put in",
    "wrong_value_substituted": "The value put in",
    "given_value_changed": "The value the question gives",
    "incorrect_evaluation": "Working out the value",
}


@dataclass
class _Gathered:
    mistake_id: str
    events: list[dict[str, Any]] = field(default_factory=list)


def _status_of(events: list[dict[str, Any]]) -> tuple[MistakeStatus, bool, str | None]:
    """Where it stands, by the last thing that happened to it, and whether it was fixed with no help."""

    status: MistakeStatus = "open"
    invalidated_as = None
    helped = False
    alone = False
    for event in events:
        kind = event["event_type"]
        if kind == "mistake_detected":
            status, invalidated_as = "open", None
        elif kind == "correction_verified":
            status, invalidated_as, alone = "corrected", None, not helped
            helped = False
        elif kind == "mistake_invalidated":
            invalidated_as = event["detail"]
            status = "erased" if invalidated_as == "erased" else "tried_again"
        elif kind in ("hint_requested", "explanation_requested"):
            helped = True
    return status, alone, invalidated_as


def _lessons(store: UserStore, user_id: str, mistake_ids: list[str]) -> dict[str, MistakeLesson]:
    """Where each mistake made in a lesson was last made. A confirmed misconception is named, nothing less."""

    found: dict[str, MistakeLesson] = {}
    for link in store.mistake_links(user_id, mistake_ids):
        problem = store.problem(user_id, link["problem_id"])
        course = curriculum.course(problem["course_id"]) if problem and problem["course_id"] else None
        level = course.level(problem["level_id"]) if course and problem else None
        if course is None or level is None or problem is None:
            continue
        skill = course.skill(problem["skill_id"])
        named = TAXONOMY.get(link["misconception"]) if link["confidence"] == "confirmed" else None
        found[link["mistake_id"]] = MistakeLesson(
            course_id=course.id,
            level_id=level.id,
            level_title=level.title,
            skill_label=skill.title if skill else level.title,
            misconception=named.label if named else None,
        )
    return found


def _entry(fact: dict[str, Any], events: list[dict[str, Any]], problems: list[dict[str, Any]]) -> MistakeEntry | None:
    detected = [event for event in events if event["event_type"] == "mistake_detected"]
    if not detected:
        # Reported by the verifier, and never shown, as the reply was out of date by the time it arrived.
        return None
    status, alone, invalidated_as = _status_of(events)
    if invalidated_as == "misread":
        # Two readings have since said it never was what it was taken for.
        return None
    topic = topic_named(fact["topic"])
    return MistakeEntry(
        id=fact["mistake_id"],
        topic=topic.id,
        topic_label=topic.label,
        topic_group=topic.group,
        kind_label=KIND_LABELS.get(fact["error_type"], topic.label),
        written=fact["written_step"] or fact["current_step"],
        status=status,
        independent=status == "corrected" and alone,
        first_seen_at=detected[0]["timestamp"],
        last_seen_at=detected[-1]["timestamp"],
        session_id=detected[-1]["session_id"],
        session_ids=list(dict.fromkeys(event["session_id"] for event in detected)),
        hints_used=sum(event["event_type"] == "hint_requested" for event in events),
        explained=any(event["event_type"] == "explanation_requested" for event in events),
        practice_topic=topic.practice,  # type: ignore[arg-type]
        practised=sum(problem["status"] == "correct" for problem in problems),
    )


def _gather(store: UserStore, user_id: str) -> list[MistakeEntry]:
    by_mistake: dict[str, list[dict[str, Any]]] = {}
    for event in store.notebook_mistake_events(user_id):
        by_mistake.setdefault(event["mistake_id"], []).append(event)
    facts = store.mistake_facts(user_id, list(by_mistake))
    problems: dict[str, list[dict[str, Any]]] = {}
    for problem in store.problems_from_mistakes(user_id):
        problems.setdefault(problem["source_mistake_id"], []).append(problem)
    from_lessons = [mistake for mistake, events in by_mistake.items() if any(e["source"] == "lesson" for e in events)]
    lessons = _lessons(store, user_id, from_lessons)
    entries = []
    for mistake_id, events in by_mistake.items():
        fact = facts.get(mistake_id)
        built = _entry(fact, events, problems.get(mistake_id, [])) if fact else None
        if built is not None:
            entries.append(built.model_copy(update={"lesson": lessons.get(mistake_id)}))
    return sorted(entries, key=lambda entry: entry.last_seen_at, reverse=True)[:MAX_ENTRIES]


def notebook(store: UserStore, user_id: str, session_id: str | None = None) -> MistakeNotebook:
    entries = _gather(store, user_id)
    if session_id:
        entries = [entry for entry in entries if session_id in entry.session_ids]
    return MistakeNotebook(entries=entries)


def detail(store: UserStore, user_id: str, mistake_id: str) -> MistakeEntryDetail | None:
    fact = store.mistake_fact(user_id, mistake_id)
    if fact is None:
        return None
    events = [event for event in store.mistake_events(user_id, mistake_id) if event["source"] in ("notebook", "lesson")]
    problems = [problem for problem in store.problems_from_mistakes(user_id) if problem["source_mistake_id"] == mistake_id]
    entry = _entry(fact, events, problems)
    if entry is None:
        return None
    hints = [
        MistakeHint(level=int(event["detail"] or 0), text=event["help_text"])
        for event in events
        if event["event_type"] in ("hint_requested", "explanation_requested") and event["help_text"]
    ]
    lesson = _lessons(store, user_id, [mistake_id]).get(mistake_id) if any(e["source"] == "lesson" for e in events) else None
    corrected = fact["corrected_step"] or None
    if _answers_an_open_question(store, user_id, mistake_id):
        # It would be that question's answer, which a lesson only gives by revealing it.
        corrected = None
    return MistakeEntryDetail(**entry.model_dump(exclude={"lesson"}), lesson=lesson, corrected_step=corrected, hints=hints)


def _answers_an_open_question(store: UserStore, user_id: str, mistake_id: str) -> bool:
    for link in store.mistake_links(user_id, [mistake_id]):
        problem = store.problem(user_id, link["problem_id"])
        if problem is not None and problem["status"] not in ("correct", "revealed"):
            return True
    return False
