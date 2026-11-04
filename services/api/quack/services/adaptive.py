"""Which question comes next in a lesson, and whether a level is mastered. No model is involved.

Both are pure functions of what's on record for the student in that level: each question asked, how it
ended, and the misconceptions named on its verified mistakes. So the same history always gives the
same choice, and every choice carries its reason and the evidence it rested on.

What counts as evidence:

- A question is **right** only when the verifier has positively checked the whole answer, with two
  readings agreeing on every line. A question with no alert isn't right, and a skipped one says nothing.
- A **mistake** counts only when the verifier found it and the student was shown it, and it wasn't
  later found to be a misreading. Unsure readings are never mistakes, so they can't lower anything.
- Only a **confirmed** misconception changes which question comes next. A plausible or unclassified
  one gets an ordinary next question, as there's nothing specific to practise.

Reinforcement is bounded: at most `MAX_TARGETED` targeted questions per misconception in a visit,
one of which goes back over the idea, before the lesson moves on and offers to come back to it. After
`move_on_after` questions without mastery, the student may move on to the next level for now. That
unlocks it, and isn't recorded as mastery.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Literal

from quack.services.curriculum import Level
from quack.services.lesson_questions import TEMPLATES, TIERS, Template, Tier
from quack.services.misconceptions import TAXONOMY

Outcome = Literal["independent", "self_corrected", "assisted", "revealed", "struggled", "no_evidence"]
SelectionKind = Literal[
    "start", "resume_targeted", "targeted", "simpler", "concept_review", "change_of_pace",
    "same_tier", "retry", "step_up", "keep_going",
]
# Selections chosen because of a misconception, which are what the bound counts.
TARGETED_KINDS = ("resume_targeted", "targeted", "simpler", "concept_review")
MAX_TARGETED = 3
CREDIT: dict[Outcome, float] = {
    "independent": 1.0,
    "self_corrected": 0.5,
    "assisted": 0.5,
    "revealed": 0.0,
    "struggled": 0.0,
    "no_evidence": 0.0,
}


@dataclass(frozen=True)
class Attempt:
    """One question asked in a level, as recorded."""

    problem_id: str
    session_id: str
    template: str
    skill: str
    tier: Tier
    # open, attempted, correct, revealed or skipped.
    status: str
    # (misconception, confidence) for each verified mistake the student was shown on it.
    mistakes: tuple[tuple[str, str], ...] = ()
    # A hint, an explanation or a nudge was asked for while answering it.
    assisted: bool = False
    selection_kind: str = "start"
    target: str | None = None

    @property
    def outcome(self) -> Outcome:
        if self.status == "revealed":
            return "revealed"
        if self.status == "correct":
            if self.assisted:
                return "assisted"
            return "self_corrected" if self.mistakes else "independent"
        return "struggled" if self.mistakes else "no_evidence"

    @property
    def credit(self) -> float:
        return CREDIT[self.outcome]

    @property
    def confirmed(self) -> list[str]:
        return [misconception for misconception, confidence in self.mistakes if confidence == "confirmed"]


@dataclass(frozen=True)
class MasteryState:
    mastered: bool
    credits: float
    credits_needed: float
    distinct_templates: int
    distinct_templates_needed: int
    distinct_skills: int
    distinct_skills_needed: int
    independent: int
    independent_needed: int
    independent_at: Tier
    independent_at_met: bool
    # Confirmed misconceptions in this level with no independent success on a question about them since.
    open_misconceptions: tuple[str, ...]
    asked: int
    corrected: int
    assisted: int
    can_move_on: bool


@dataclass(frozen=True)
class Selection:
    kind: SelectionKind
    template: str
    tier: Tier
    # Shown to the student. Friendly, and never more certain than the evidence.
    reason: str
    target: str | None = None
    # What it rested on, for the debug panel and the report. No answers.
    basis: dict[str, Any] = field(default_factory=dict)


def _tier_index(tier: str) -> int:
    return TIERS.index(tier)  # type: ignore[arg-type]


def assess(level: Level, history: list[Attempt]) -> MasteryState:
    """The level's mastery rule, applied to every question asked in it, each counted once."""

    rule = level.mastery
    by_problem = {attempt.problem_id: attempt for attempt in history}
    attempts = list(by_problem.values())
    credited = [attempt for attempt in attempts if attempt.credit > 0]
    independent = [attempt for attempt in attempts if attempt.outcome == "independent"]
    credits = sum(attempt.credit for attempt in credited)
    open_misconceptions = _open_misconceptions(level, attempts)
    at_met = any(_tier_index(attempt.tier) >= _tier_index(rule.independent_at) for attempt in independent)
    mastered = (
        credits >= rule.credits
        and len({attempt.template for attempt in credited}) >= rule.distinct_templates
        and len({attempt.skill for attempt in credited}) >= rule.distinct_skills
        and len(independent) >= rule.independent
        and at_met
        and not open_misconceptions
    )
    return MasteryState(
        mastered=mastered,
        credits=credits,
        credits_needed=rule.credits,
        distinct_templates=len({attempt.template for attempt in credited}),
        distinct_templates_needed=rule.distinct_templates,
        distinct_skills=len({attempt.skill for attempt in credited}),
        distinct_skills_needed=rule.distinct_skills,
        independent=len(independent),
        independent_needed=rule.independent,
        independent_at=rule.independent_at,
        independent_at_met=at_met,
        open_misconceptions=tuple(open_misconceptions),
        asked=len(attempts),
        corrected=sum(attempt.outcome == "self_corrected" for attempt in attempts),
        assisted=sum(attempt.outcome == "assisted" for attempt in attempts),
        can_move_on=not mastered and len(attempts) >= rule.move_on_after,
    )


def practisable(level: Level, misconception: str) -> bool:
    """Does any question in this level practise it? One that none does can't be cleared here, so it
    mustn't hold the student back here either."""

    return any(t.skill in level.skills and misconception in t.targets for t in TEMPLATES.values())


def _open_misconceptions(level: Level, attempts: list[Attempt]) -> list[str]:
    """A misconception is cleared by a later question about it, right with no mistake and no help."""

    still_open: list[str] = []
    for index, attempt in enumerate(attempts):
        for misconception in attempt.confirmed:
            if not practisable(level, misconception):
                continue
            later = attempts[index + 1 :]
            cleared = any(
                other.outcome == "independent" and misconception in TEMPLATES[other.template].targets for other in later
            )
            if not cleared and misconception not in still_open:
                still_open.append(misconception)
    # Seen again after being cleared counts from the latest sighting, which the loop above already does.
    return still_open


def _candidates(level: Level, tier: Tier, target: str | None, skill: str | None) -> list[Template]:
    """Templates at a tier. With a misconception, only those that can show it, and of those, the ones in
    `skill` if any, else the ones mainly about it: a bracket slip goes back to brackets."""

    pool = [t for t in TEMPLATES.values() if t.skill in level.skills and t.tier == tier]
    if target:
        pool = [t for t in pool if target in t.targets]
        same_skill = [t for t in pool if t.skill == skill]
        primary = [t for t in pool if t.targets[0] == target]
        pool = same_skill or primary or pool
    return pool


def _nearest(level: Level, tier: Tier, target: str | None, skill: str | None) -> tuple[Tier, list[Template]]:
    """Templates at this tier, or the nearest of the level's tiers that has some, easier ones first."""

    order = sorted(level.tiers, key=lambda other: (abs(_tier_index(other) - _tier_index(tier)), _tier_index(other)))
    for other in order:
        found = _candidates(level, other, target, skill)
        if found:
            return other, found
    return tier, []


def _choose(
    level: Level, tier: Tier, history: list[Attempt], rng: random.Random, *, target: str | None = None,
    avoid: str | None = None, skill: str | None = None,
) -> tuple[Tier, str] | None:
    chosen_tier, pool = _nearest(level, tier, target, skill)
    if not pool:
        return None
    if avoid and len(pool) > 1:
        pool = [t for t in pool if t.id != avoid]
    used = {t.id: sum(attempt.template == t.id for attempt in history) for t in pool}
    if len(level.skills) > 1 and not (target and skill):
        # A mixed challenge spreads itself across skills: least-practised skill first.
        skill_use = {t.skill: sum(attempt.skill == t.skill for attempt in history) for t in pool}
        fewest_skill = min(skill_use.values())
        pool = [t for t in pool if skill_use[t.skill] == fewest_skill]
    fewest = min(used[t.id] for t in pool)
    least_used = sorted(t.id for t in pool if used[t.id] == fewest)
    return chosen_tier, rng.choice(least_used)


def _clamp(level: Level, tier: Tier) -> Tier:
    if tier in level.tiers:
        return tier
    if _tier_index(tier) < _tier_index(level.tiers[0]):
        return level.tiers[0]
    return level.tiers[-1]


def _step(level: Level, tier: Tier, by: int) -> Tier:
    position = level.tiers.index(_clamp(level, tier))
    return level.tiers[max(0, min(len(level.tiers) - 1, position + by))]


def _start_tier(level: Level, history: list[Attempt]) -> Tier:
    """Going back to a level starts just above the hardest question done there on their own."""

    done = [attempt.tier for attempt in history if attempt.outcome == "independent"]
    if not done:
        return level.tiers[0]
    return _step(level, max(done, key=_tier_index), 1)


def choose_next(
    level: Level, history: list[Attempt], session_id: str, rng: random.Random, *, opener: bool = True
) -> Selection:
    """The next question in this visit to the level, from everything asked in the level so far.

    `history` is in the order asked, across all visits. Only `session_id`'s own questions decide what
    to reinforce now, so going back to a level starts a fresh allowance.
    """

    session = [attempt for attempt in history if attempt.session_id == session_id]
    last = session[-1] if session else None
    mastery = assess(level, history)

    def selection(kind: SelectionKind, tier: Tier, reason: str, target: str | None = None, avoid: str | None = None,
                  skill: str | None = None, **basis: Any) -> Selection:
        chosen = _choose(level, tier, history, rng, target=target, avoid=avoid, skill=skill)
        if chosen is None and target is not None:
            # Nothing here practises that misconception. Say so, rather than pretend.
            chosen = _choose(level, tier, history, rng, avoid=avoid)
            kind, target, reason = "same_tier", None, "Here's another one to try."
        assert chosen is not None, f"{level.id} has no questions at all"
        chosen_tier, template = chosen
        if kind == "simpler" and last is not None and _tier_index(chosen_tier) >= _tier_index(last.tier):
            # There's nothing easier on this idea here, so it isn't called simpler.
            reason = reason.replace("Let's try a simpler one.", "Let's try another one like it.")
        record = {
            "previous_problem_id": last.problem_id if last else None,
            "previous_outcome": last.outcome if last else None,
            "previous_template": last.template if last else None,
            "previous_misconceptions": [list(item) for item in last.mistakes] if last else [],
            "credits": mastery.credits,
            "open_misconceptions": list(mastery.open_misconceptions),
        } | basis
        return Selection(kind, template, chosen_tier, reason, target, record)

    if last is None:
        earlier_open = [m for m in mastery.open_misconceptions if m in TAXONOMY]
        if earlier_open:
            target = earlier_open[0]
            return selection(
                "resume_targeted", level.tiers[0],
                f"Last time, {TAXONOMY[target].happened}. Let's start with one like that.",
                target, rule="open_misconception_from_an_earlier_visit",
            )
        if opener and level.opener is not None and not history:
            return Selection(
                "start", level.opener.template, TEMPLATES[level.opener.template].tier,
                "Let's start with one like the example.", None, {"rule": "first_question_of_the_level"},
            )
        tier = _start_tier(level, history)
        reason = "Let's pick up where you left off." if history else "Let's start with one like the example."
        return selection("start", tier, reason, rule="start_above_hardest_done_alone")

    confirmed = [misconception for misconception in last.confirmed if practisable(level, misconception)]
    if confirmed:
        target = confirmed[0]
        idea = TAXONOMY[target]
        targeted = [a for a in session if a.target == target and a.selection_kind in TARGETED_KINDS]
        seen = sum(target in a.confirmed for a in session)
        reviewed = any(a.selection_kind == "concept_review" and a.target == target for a in session)
        if len(targeted) >= MAX_TARGETED:
            return selection(
                "change_of_pace", _clamp(level, last.tier),
                "Let's try a different kind of question, and come back to this idea later.",
                avoid=last.template, rule="reinforcement_limit_reached", target_was=target, targeted_so_far=len(targeted),
            )
        if seen >= 2 and not reviewed:
            return selection(
                "concept_review", level.tiers[0], f"This one keeps catching people out. {idea.idea} Here's an easier one.",
                target, avoid=last.template, rule="same_misconception_twice_this_visit", seen=seen,
            )
        if last.status == "correct":
            # Put right alone: the same kind of question again, to see it hold.
            return selection(
                "targeted", _clamp(level, last.tier),
                "Nice correction! Here's a slightly different one to check your understanding.",
                target, avoid=last.template, skill=last.skill, rule="corrected_confirmed_misconception",
            )
        return selection(
            "simpler", _step(level, last.tier, -1), f"Let's try a simpler one. {idea.idea}",
            target, avoid=last.template, rule="confirmed_misconception_not_yet_corrected",
        )

    if last.mistakes:
        # A real mistake that can't be named. Practising a guess would be pretending.
        return selection(
            "same_tier", _clamp(level, last.tier), "Here's another one like it.", avoid=last.template,
            rule="mistake_without_a_confirmed_misconception",
        )
    if last.outcome in ("no_evidence", "revealed"):
        return selection(
            "retry", _clamp(level, last.tier), "Here's another one to try.", avoid=last.template,
            rule="no_verified_answer" if last.outcome == "no_evidence" else "answer_was_shown",
        )
    if last.outcome == "assisted":
        return selection(
            "same_tier", _clamp(level, last.tier), "Good. Now try one like it on your own.", avoid=last.template,
            rule="right_with_help",
        )

    # Right, with no mistake and no help.
    still_open = [m for m in mastery.open_misconceptions if m not in last.confirmed]
    budget = {m: sum(a.target == m and a.selection_kind in TARGETED_KINDS for a in session) for m in still_open}
    for misconception in still_open:
        if budget[misconception] < MAX_TARGETED:
            return selection(
                "targeted", _clamp(level, last.tier), f"Nice! One more to be sure: {TAXONOMY[misconception].idea}",
                misconception, avoid=last.template, rule="open_misconception_still_to_clear",
            )
    if last.tier == level.tiers[-1]:
        return selection(
            "keep_going", last.tier, "Another challenge to show what you can do.", avoid=last.template,
            rule="already_at_the_hardest_tier",
        )
    return selection(
        "step_up", _step(level, last.tier, 1), "Nice work! Here's a step up.", avoid=last.template,
        rule="right_alone_so_harder",
    )


def normal_progression(level: Level, history: list[Attempt], session_id: str, rng: random.Random) -> Selection:
    """What a mistake-blind progression would choose: it knows only whether each answer was right.

    The baseline the evaluation compares with. Right moves up a tier, anything else stays, with no idea
    what went wrong. That's what a fixed sequence of questions, made a little responsive, would do.
    """

    blind = [
        Attempt(a.problem_id, a.session_id, a.template, a.skill, a.tier, a.status, (), a.assisted, a.selection_kind, None)
        for a in history
    ]
    return choose_next(level, blind, session_id, rng, opener=False)

