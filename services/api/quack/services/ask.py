"""Help with working the student has circled, which is a question and never a verdict of its own.

Circling a line can't make it a mistake. What's said comes from what the verifier found about those
lines. Nothing here records a mistake or an event, so asking can't change what a recap counts. The
model only words an answer to a free-text question, and what it's allowed to say depends on that finding.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from quack import prompts
from quack.llm.router import ModelError, ModelRouter, Purpose
from quack.schemas import AskAction, AskVerdict
from quack.services.concepts import Explanation, explain
from quack.services.hint_engine import (
    CLAIMS_WHAT_THE_STUDENT_DID,
    FILLER,
    MAX_QUESTION_CHARS,
    find_problems,
    generate_help,
    has_prepared_hints,
    template_text,
)
from quack.services.math_verifier import StepResult
from quack.services.mistake_manager import TrackedMistake

log = logging.getLogger("quack.ask")

MAX_ANSWER_WORDS = 50
SAYS_IT_IS_WRONG = re.compile(
    r"\b(?:is|looks|seems|that'?s)\s+(?:wrong|incorrect|a mistake)\b|\bnot\s+(?:right|correct)\b|\bmistake\b|\berror\b",
    re.IGNORECASE,
)
SAYS_IT_IS_RIGHT = re.compile(
    r"\b(?:is|looks|seems|that'?s)\s+(?:right|correct|fine|good)\b|\bchecks out\b|\bwell done\b", re.IGNORECASE
)
CANT_READ = "I can't read this clearly enough to be sure."
CANT_CHECK = "That's beyond what I can check so far, so I can't say whether it's right."
NOTHING_CLAIMED = "There's nothing to check here yet, because this doesn't say what anything equals."
WHERE_IT_STARTS = "That's where this working starts, so there's nothing to check yet. I'll look at the next step."
PART_OF_A_LINE = "That's only part of a line, so I can't say about it yet. Circle the whole line and I'll check it."
WORDS = "That looks like words rather than maths, so there's nothing here for me to check."
CHECKS_OUT = "I checked this and it holds up."
LOOKS_WRONG = "Something in this step doesn't hold up. Want a hint?"
ASK_INTENT = "This could be two things. Are you simplifying it, or solving it?"
NO_ANSWER = "I can't answer that right now. A hint or a check still works."
NO_RULE = "I can't tell which rule this is from what I can read, so I won't guess. Try circling the whole step."


class GeneratedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


@dataclass(frozen=True)
class SelectionFinding:
    """What the checker found about the circled lines."""

    verdict: AskVerdict
    step: StepResult | None = None
    # What each circled line was read as, and any other reading of it.
    working: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    # Only part of a line was circled, and `3 * 4 = 1` could be all but the end of `3 * 4 = 12`.
    partial: bool = False


def tracked_from(step: StepResult) -> TrackedMistake:
    """For wording help. It goes no further, so the mistake it describes isn't recorded anywhere."""

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


def how_it_was_checked(step: StepResult | None) -> str:
    """Says how the line was checked, which is known, without guessing at how the student thinks."""

    operation = step.operation_type if step else ""
    if operation == "integration":
        return "I differentiated the answer, and it gives back what's inside the integral."
    if operation == "differentiation":
        return "I worked out the derivative of the left side, and it matches the right."
    if operation == "arithmetic":
        return "I worked out the left side exactly, and it matches the right."
    if operation == "equation_transformation":
        return "This step keeps the same solutions as the line before it."
    if operation == "approximation":
        return "The value rounds to what's written here."
    return "Both sides come to the same thing."


def _with_assumption(step: StepResult | None) -> str:
    if step is not None and step.assumption:
        return f"{CHECKS_OUT[:-1]}, {step.assumption}."
    return CHECKS_OUT


def _nothing_to_check(finding: SelectionFinding) -> str:
    """`3x + 2x = 5`, once it's known to be an equation to solve, does say what something equals."""

    if finding.partial:
        return PART_OF_A_LINE
    return WHERE_IT_STARTS if any("=" in line for line in finding.working) else NOTHING_CLAIMED


def _unclear_text(finding: SelectionFinding) -> str:
    if len(finding.alternatives) == 1:
        return f"{CANT_READ} It could also be {finding.alternatives[0]}. Could you write it once more?"
    return f"{CANT_READ} Could you write it once more?"


async def _hint_for(
    router: ModelRouter, mistake: TrackedMistake, level: Literal[1, 2], session_id: str
) -> tuple[str, Literal["model", "template"]]:
    if has_prepared_hints(mistake):
        return template_text(level, mistake), "template"
    text, source, _ = await generate_help(router, level, mistake, session_id=session_id)
    return text, source


async def _answer(
    router: ModelRouter,
    finding: SelectionFinding,
    question: str,
    fallback: str,
    session_id: str,
) -> tuple[str, Literal["model", "template"], ModelError | None]:
    """A free-text question is the one place the model words what's said. Checked before it's shown."""

    label = {"incorrect": "incorrect", "correct": "correct"}.get(finding.verdict, "not_checked")
    facts: dict[str, object] = {
        "working": list(finding.working), "finding": label, "question": question[:MAX_QUESTION_CHARS],
    }
    mistake = tracked_from(finding.step) if finding.verdict == "incorrect" and finding.step else None
    if mistake is not None:
        facts |= {"operation": mistake.parsed_operation or mistake.operation_type, "error_type": mistake.error_type}
    system = prompts.load_prompt(prompts.ASK)
    corrections: list[str] = []
    for _ in range(2):
        message = json.dumps(facts, ensure_ascii=False)
        if corrections:
            message += "\n\nYour previous attempt was rejected because " + "; ".join(corrections) + ". Try again."
        try:
            result = await router.complete_json(
                Purpose.EXPLAIN,
                [{"role": "developer", "content": system}, {"role": "user", "content": message}],
                GeneratedAnswer,
                session_id=session_id,
            )
        except ModelError as exc:
            return fallback, "template", exc
        corrections = problems_with_answer(result.value.text, finding.verdict, mistake)
        if not corrections:
            return result.value.text.strip(), "model", None
    return fallback, "template", None


def problems_with_answer(text: str, verdict: AskVerdict, mistake: TrackedMistake | None) -> list[str]:
    """Only the verifier can call a step right or wrong, so the model can't, unless it did."""

    clean = text.strip()
    if not clean:
        return ["the text is empty"]
    problems: list[str] = []
    if len(clean.split()) > MAX_ANSWER_WORDS:
        problems.append(f"it is longer than {MAX_ANSWER_WORDS} words")
    if FILLER.search(clean) or "!" in clean:
        problems.append("it contains filler")
    if CLAIMS_WHAT_THE_STUDENT_DID.search(clean):
        problems.append("it says what the student did, which nothing has established")
    if verdict != "incorrect" and SAYS_IT_IS_WRONG.search(clean):
        problems.append("it says the working is wrong, which the checker hasn't found")
    if verdict != "correct" and SAYS_IT_IS_RIGHT.search(clean):
        problems.append("it says the working is right, which the checker hasn't found")
    if mistake is not None:
        problems.extend(find_problems(2, clean, mistake))
    return problems


@dataclass(frozen=True)
class AskHelp:
    text: str
    source: Literal["model", "template"]
    level: int | None = None
    error: ModelError | None = None
    # The same as `text`, in short numbered steps, when it's the rule behind a step.
    explanation: Explanation | None = None


def _explained(explanation: Explanation, level: int | None = None) -> AskHelp:
    return AskHelp(explanation.text, "template", level, explanation=explanation)


async def help_for(
    router: ModelRouter,
    action: AskAction,
    finding: SelectionFinding,
    *,
    question: str | None = None,
    level: Literal[1, 2] = 1,
    session_id: str = "",
) -> AskHelp:
    verdict = finding.verdict
    if action == "question" and question:
        fallbacks = {
            "correct": _with_assumption(finding.step),
            "incorrect": LOOKS_WRONG,
            "unclear": _unclear_text(finding),
            "unsupported": CANT_CHECK,
            "ambiguous_intent": ASK_INTENT,
            "unchecked": _nothing_to_check(finding),
            "not_math": WORDS,
        }
        # With nothing readable to talk about, asking the model would only be guessing what's there.
        if verdict in ("unclear", "not_math"):
            return AskHelp(fallbacks[verdict], "template")
        text, source, error = await _answer(router, finding, question, fallbacks[verdict], session_id)
        return AskHelp(text, source, None, error)
    if verdict == "correct":
        # Explaining is about the idea that makes it work. Whether it holds is what checking says.
        if action == "explain":
            idea = explain(finding.step, finding.working, reveal=True)
            if idea is not None:
                return _explained(idea)
            return AskHelp(f"{_with_assumption(finding.step)} {how_it_was_checked(finding.step)}", "template")
        if action == "check":
            return AskHelp(f"{_with_assumption(finding.step)} {how_it_was_checked(finding.step)}", "template")
        # Asked for a hint on right working, there's no mistake to hint at, and none is made up.
        return AskHelp(f"{_with_assumption(finding.step)} There's nothing to fix here.", "template")
    if verdict == "incorrect" and finding.step is not None:
        mistake = tracked_from(finding.step)
        if action == "explain":
            # The rule, then what it means for this step. Nothing in it is the student's answer.
            idea = explain(finding.step, finding.working)
            said = template_text(3, mistake)
            if idea is not None:
                # The fixed wording ends by saying what it should be, and the steps already give the rule.
                verdict_on_step = re.split(r"(?<=\.)\s+", said.strip())[-1]
                return _explained(Explanation(idea.concept, idea.title, idea.steps, verdict_on_step), 3)
            return AskHelp(said, "template", 3)
        if action == "check":
            return AskHelp(LOOKS_WRONG, "template")
        text, source = await _hint_for(router, mistake, level, session_id)
        return AskHelp(text, source, level)  # type: ignore[arg-type]
    if verdict == "unclear":
        return AskHelp(_unclear_text(finding), "template")
    if verdict == "ambiguous_intent":
        return AskHelp(ASK_INTENT, "template")
    if verdict == "not_math":
        return AskHelp(WORDS, "template")
    # Nothing's been said about whether it's right, so the rule can be given, and nothing about this answer.
    if action == "explain" and verdict in ("unchecked", "unsupported") and not finding.partial:
        idea = explain(None, finding.working)
        # Never some other rule, just to have something to say.
        return _explained(idea) if idea is not None else AskHelp(NO_RULE, "template")
    if verdict == "unchecked":
        return AskHelp(_nothing_to_check(finding), "template")
    return AskHelp(CANT_CHECK, "template")
