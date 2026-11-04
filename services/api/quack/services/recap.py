"""A recap of a session, counted from verified events. There's no model in here, so none can get it wrong.

Every sentence is written from a count, and is only written if the count supports it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from quack.schemas import (
    Recap,
    RecapGlance,
    RecapTopic,
    RecapTopicGroup,
    Recommendation,
)
from quack.services.topics import topic_named
from quack.services.user_store import SESSION_INACTIVE_S, UserStore

# How far back counts as "keeps coming back".
RECURRING_WINDOW_S = 30 * 24 * 3600
GROUP_ORDER = ("Arithmetic", "Algebra", "Calculus", "Other")


@dataclass
class _Mistake:
    id: str
    topic: str
    current_step: str = ""
    detected_here: bool = False
    standing: str = "open"
    invalidated_as: str | None = None
    hints: int = 0
    explained: bool = False
    # Help asked for before it was corrected, whichever session that was in.
    assisted: bool = False


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _is(count: int) -> str:
    return "is" if count == 1 else "are"


def _was(count: int) -> str:
    return "was" if count == 1 else "were"


def _list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" and {items[-1]}"


def _had_help(history: list[dict[str, Any]], corrected_at: int) -> bool:
    """Help since this mistake was last fixed, even in an earlier session, means it wasn't done alone.

    Counted from the last fix and not from when it was detected, because a detection comes through the
    client's queue, and a quick tap on the duck can reach the server before it.
    """

    before = [event for event in history if event["sequence"] < corrected_at]
    fixed = max((event["sequence"] for event in before if event["event_type"] == "correction_verified"), default=0)
    return any(
        event["event_type"] in ("hint_requested", "explanation_requested") and event["sequence"] > fixed
        for event in before
    )


def _gather(store: UserStore, user_id: str, session_id: str) -> tuple[dict[str, _Mistake], list[dict[str, Any]]]:
    events = store.session_events(user_id, session_id)
    mistakes: dict[str, _Mistake] = {}
    for event in events:
        mistake_id = event["mistake_id"]
        if mistake_id is None:
            continue
        mistake = mistakes.setdefault(mistake_id, _Mistake(mistake_id, event["topic"] or "unclassified"))
        kind = event["event_type"]
        if kind == "mistake_detected":
            mistake.detected_here = True
            mistake.standing = "open"
            mistake.invalidated_as = None
        elif kind == "hint_requested":
            mistake.hints += 1
        elif kind == "explanation_requested":
            mistake.explained = True
        elif kind == "correction_verified":
            mistake.standing = "corrected"
            mistake.invalidated_as = None
            mistake.assisted = _had_help(store.mistake_events(user_id, mistake_id), event["sequence"])
        elif kind == "mistake_invalidated":
            mistake.standing = "invalidated"
            mistake.invalidated_as = event["detail"]
    for mistake in mistakes.values():
        fact = store.mistake_fact(user_id, mistake.id)
        mistake.current_step = (fact["written_step"] or fact["current_step"]) if fact else ""
    return mistakes, events


def _recommend(found: list[_Mistake], recurring: dict[str, int]) -> Recommendation | None:
    """In the order the brief asks for, among topics there are questions for."""

    def practicable(mistakes: list[_Mistake]) -> dict[str, list[_Mistake]]:
        topics: dict[str, list[_Mistake]] = {}
        for mistake in mistakes:
            if topic_named(mistake.topic).practice:
                topics.setdefault(mistake.topic, []).append(mistake)
        return topics

    def offer(topic_id: str, reason: str) -> Recommendation:
        topic = topic_named(topic_id)
        return Recommendation(topic=topic.practice or topic_id, label=topic.label, reason=reason)

    topics = practicable(found)
    if not topics:
        return None
    repeated = {
        topic_id: len(mistakes) + recurring.get(topic_id, 0)
        for topic_id, mistakes in topics.items()
        if len(mistakes) + recurring.get(topic_id, 0) >= 2
    }
    if repeated:
        topic_id = max(repeated, key=lambda key: (repeated[key], key))
        here, before = len(topics[topic_id]), recurring.get(topic_id, 0)
        where = f"{_plural(here, 'mistake')} this session" + (f" and {before} in recent sessions" if before else "")
        return offer(topic_id, f"That's {where}, so a little more practice could help.")
    for topic_id, mistakes in topics.items():
        if any(mistake.explained for mistake in mistakes):
            return offer(topic_id, "You asked me to explain one of these, so another go could help.")
    for topic_id, mistakes in topics.items():
        if any(mistake.standing == "open" for mistake in mistakes):
            return offer(topic_id, "One of these is still open, so a similar question could help.")
    for topic_id, mistakes in topics.items():
        if any(mistake.hints for mistake in mistakes):
            return offer(topic_id, "You used a hint on this, so a similar question could help.")
    topic_id = next(iter(topics))
    return offer(topic_id, "It came up once this session, so this is just a suggestion.")


def _a(label: str) -> str:
    return f"an {label}" if label[0] in "aeiou" else f"a {label}"


def _left_open(glance: RecapGlance) -> list[str]:
    lines: list[str] = []
    if glance.unresolved:
        count = glance.unresolved
        lines.append(
            "One mistake is still open. You can come back to it any time."
            if count == 1
            else f"{count} mistakes are still open. You can come back to them any time."
        )
    if glance.erased:
        count = glance.erased
        lines.append(
            "You erased a mistake before it was worked through, so I haven't counted that as corrected."
            if count == 1
            else f"You erased {count} mistakes before they were worked through, so I haven't counted those as corrected."
        )
    if glance.tried_again:
        count = glance.tried_again
        lines.append(
            "You had another go at an answer, and that try needed another look too."
            if count == 1
            else f"You had another go at {count} answers, and those tries needed another look too."
        )
    return lines


def _observations(glance: RecapGlance, assisted_labels: list[str]) -> list[str]:
    lines: list[str] = []
    if glance.independent and glance.independent == glance.found:
        alone = glance.independent
        lines.append("You fixed it on your own, with no hints." if alone == 1 else f"You fixed all {alone} on your own.")
    elif glance.independent:
        lines.append(f"You fixed {_plural(glance.independent, 'mistake')} on your own, with no hints.")
    if glance.assisted:
        labels = [label.lower() for label in assisted_labels]
        if glance.assisted == 1:
            what = f"{_a(labels[0])} mistake" if len(labels) == 1 else "a mistake"
            lines.append(f"You worked through {what} with a little help.")
        else:
            where = f" in {_list(labels)}" if labels else ""
            lines.append(f"You worked through {glance.assisted} mistakes{where} with a little help.")
    if glance.earlier_corrected:
        lines.append(f"You also fixed {_plural(glance.earlier_corrected, 'mistake')} left over from an earlier session.")
    return lines


def _opening(glance: RecapGlance, checks: int) -> str:
    """Says what happened, in a sentence. Nothing here can be more encouraging than the counts allow."""

    found, corrected = glance.found, glance.corrected
    if not found:
        if checks:
            return "I looked over your working and didn't spot anything to fix."
        return "We didn't check any working this time."
    if corrected == found:
        if found == 1:
            return "You worked through the mistake I spotted this session."
        return f"You worked through all {found} mistakes I spotted this session."
    if corrected:
        return f"You worked through {corrected} of the {found} mistakes I spotted this session."
    return "I spotted one mistake this session." if found == 1 else f"I spotted {found} mistakes this session."


def _note(glance: RecapGlance, recommendation: Recommendation | None, checks: int) -> str | None:
    """Only when there's nothing to recommend. What's offered next is real: every topic listed has questions."""

    if recommendation is not None or (not glance.found and not checks):
        return None
    if not glance.found:
        return (
            "No particular trouble spot stood out this session. "
            "Keep practising and I'll help you notice patterns over time."
        )
    return "Want to keep going? Try a quick practice question with me."


def _reworded(recommendation: Recommendation | None) -> Recommendation | None:
    """A recap can be read on any later day, so one written before can't go on saying "today"."""

    if recommendation is None or "today" not in recommendation.reason:
        return recommendation
    reason = recommendation.reason.replace("these today,", "these,").replace("this today,", "this,")
    return recommendation.model_copy(update={"reason": reason.replace(" today", " this session")})


def phrased(recap: Recap, checks: int) -> Recap:
    """What's said, written fresh from what's counted, so that a recap from before reads the same as one from today.

    The counts are what was seen, and stay as they were.
    """

    return recap.model_copy(
        update={
            "opening": _opening(recap.glance, checks),
            "observations": _observations(recap.glance, recap.assisted_labels),
            "left_open": _left_open(recap.glance),
            "note": _note(recap.glance, recap.recommendation, checks),
            "recommendation": _reworded(recap.recommendation),
        }
    )


def build_recap(store: UserStore, user_id: str, session: dict[str, Any], finished_at: float, reason: str) -> Recap:
    session_id = session["id"]
    mistakes, _ = _gather(store, user_id, session_id)
    # Two readings have since said a misread line never was what it was taken for.
    found = [mistake for mistake in mistakes.values() if mistake.detected_here and mistake.invalidated_as != "misread"]
    corrected = [mistake for mistake in found if mistake.standing == "corrected"]
    independent = [mistake for mistake in corrected if not mistake.assisted]
    earlier = [m for m in mistakes.values() if not m.detected_here and m.standing == "corrected"]
    # "Other mistakes" isn't something to say someone worked on.
    assisted_labels = sorted(
        {topic_named(m.topic).label for m in corrected if m.assisted and topic_named(m.topic).id != "unclassified"}
    )
    glance = RecapGlance(
        found=len(found),
        corrected=len(corrected),
        unresolved=sum(mistake.standing == "open" for mistake in found),
        independent=len(independent),
        assisted=len(corrected) - len(independent),
        hints=sum(mistake.hints for mistake in mistakes.values()),
        explanations=sum(mistake.explained for mistake in mistakes.values()),
        erased=sum(mistake.invalidated_as == "erased" for mistake in found),
        tried_again=sum(mistake.invalidated_as == "replaced" for mistake in found),
        earlier_corrected=len(earlier),
    )

    groups: dict[str, dict[str, RecapTopic]] = {}
    for mistake in found:
        topic = topic_named(mistake.topic)
        entry = groups.setdefault(topic.group, {}).setdefault(
            topic.id, RecapTopic(topic=topic.id, label=topic.label, mistakes=0, corrected=0, examples=[])
        )
        entry.mistakes += 1
        entry.corrected += mistake.standing == "corrected"
        if mistake.current_step and len(entry.examples) < 3:
            entry.examples.append(mistake.current_step)

    since = finished_at - RECURRING_WINDOW_S
    recurring: dict[str, int] = {}
    for row in store.notebook_mistakes_before(user_id, session_id, since):
        if row["topic"]:
            recurring[row["topic"]] = recurring.get(row["topic"], 0) + 1

    recommendation = _recommend(found, recurring)
    recap = Recap(
        session_id=session_id,
        started_at=session["started_at"],
        finished_at=finished_at,
        finish_reason=reason,
        glance=glance,
        topics=[
            RecapTopicGroup(group=group, topics=list(groups[group].values())) for group in GROUP_ORDER if group in groups
        ],
        observations=[],
        recommendation=recommendation,
        assisted_labels=assisted_labels,
    )
    return phrased(recap, session["checks"])


def finish(store: UserStore, user_id: str, session: dict[str, Any], reason: str, now: float | None = None) -> Recap:
    """Only the first finish writes a recap. After that, the one written is the one there is."""

    if session["recap_json"]:
        return Recap.model_validate_json(session["recap_json"])
    finished_at = now if now is not None else time.time()
    recap = build_recap(store, user_id, session, finished_at, reason)
    if store.finish_session(user_id, session["id"], recap.model_dump(by_alias=True), reason, finished_at):
        return recap
    stored = store.session(user_id, session["id"])
    assert stored is not None
    return Recap.model_validate_json(stored["recap_json"])


def current_session(store: UserStore, user_id: str, now: float | None = None) -> tuple[dict[str, Any], Recap | None]:
    """The session to carry on with. One left alone too long is wrapped up here, on the next visit,
    since a closing iPad can't be relied on to say so. It's timed from its last activity, not from now."""

    clock = now if now is not None else time.time()
    session = store.open_notebook_session(user_id)
    wrapped = None
    if session is not None and clock - session["last_active_at"] > SESSION_INACTIVE_S:
        # Opened and never used, it isn't worth a line in anyone's history. It just carries on.
        if session["checks"] or store.session_events(user_id, session["id"]):
            wrapped = phrased(finish(store, user_id, session, "inactive", session["last_active_at"]), session["checks"])
            session = None
        else:
            store.touch_session(user_id, session["id"])
    return session or store.current_notebook_session(user_id), wrapped
