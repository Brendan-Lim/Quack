"""Learning paths, read from versioned files in quack/curriculum and checked as they load.

The content is data, so a course can change without touching the app. What can't be wrong is checked
here, before any student sees it: every level's skills have questions at each of its tiers, its first
question comes from one of its own skills, every misconception is one the classifier knows, and the
prerequisites form a graph with no loops. A failure stops the server starting.
"""

from __future__ import annotations

import json
import random
import re
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from quack.services.lesson_questions import TEMPLATES, TIERS, Tier, build
from quack.services.misconceptions import TAXONOMY

CURRICULUM = Path(__file__).resolve().parent.parent / "curriculum"


class Skill(BaseModel):
    id: str
    title: str
    objective: str
    prerequisites: list[str]
    misconceptions: list[str]


class Example(BaseModel):
    question: str
    working: list[str]
    note: str = ""


class Lesson(BaseModel):
    intro: str
    steps: list[str] = Field(min_length=2, max_length=5)
    example: Example
    remember: str
    # Every question the lesson works through, in the parser's notation. None of them is ever asked.
    shown: list[str] = []


class Opener(BaseModel):
    template: str
    params: dict[str, object]


class Mastery(BaseModel):
    """Initial heuristics, not a measured estimate of mastery. See docs/QUACK_ADAPTIVE_LEARNING_PHASE3_REPORT.md."""

    # Credit needed: 1 for a question right with no mistake and no help, 0.5 for one put right
    # after a verified mistake or with help, 0 for a revealed answer.
    credits: float
    # Different templates among the questions given credit.
    distinct_templates: int
    # At least one question right with no mistake and no help, at this tier or above.
    independent_at: Tier
    # How many questions right with no mistake and no help, in all.
    independent: int = 1
    # Different skills among the questions given credit, for a mixed challenge.
    distinct_skills: int = 1
    # After this many questions with no mastery, the student may move on for now.
    move_on_after: int = 8


class Level(BaseModel):
    id: str
    number: int
    kind: Literal["level", "challenge"]
    title: str
    skills: list[str] = Field(min_length=1)
    prerequisites: list[str]
    objective: str
    lesson: Lesson
    opener: Opener | None
    tiers: list[Tier] = Field(min_length=1)
    mastery: Mastery


class Course(BaseModel):
    id: str
    version: int
    title: str
    tagline: str
    skills: list[Skill]
    levels: list[Level]

    def level(self, level_id: str) -> Level | None:
        return next((level for level in self.levels if level.id == level_id), None)

    def skill(self, skill_id: str) -> Skill | None:
        return next((skill for skill in self.skills if skill.id == skill_id), None)


class PathEntry(BaseModel):
    id: str
    title: str
    tagline: str
    status: Literal["available", "coming_later"]
    course: str | None = None
    preview: list[str] = []


class Catalog(BaseModel):
    version: int
    paths: list[PathEntry]


class CurriculumError(ValueError):
    pass


def _acyclic(edges: dict[str, list[str]], what: str) -> None:
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(node: str) -> None:
        if node in done:
            return
        if node in visiting:
            raise CurriculumError(f"{what} prerequisites loop through {node}")
        visiting.add(node)
        for before in edges.get(node, []):
            visit(before)
        visiting.discard(node)
        done.add(node)

    for node in edges:
        visit(node)


def validate(course: Course) -> Course:
    skills = {skill.id: skill for skill in course.skills}
    levels = {level.id: level for level in course.levels}
    if len(skills) != len(course.skills) or len(levels) != len(course.levels):
        raise CurriculumError("duplicate skill or level id")
    for skill in course.skills:
        for before in skill.prerequisites:
            if before not in skills:
                raise CurriculumError(f"{skill.id} needs unknown skill {before}")
        for misconception in skill.misconceptions:
            if misconception not in TAXONOMY:
                raise CurriculumError(f"{skill.id} names unknown misconception {misconception}")
    _acyclic({skill.id: skill.prerequisites for skill in course.skills}, "skill")
    _acyclic({level.id: level.prerequisites for level in course.levels}, "level")
    for level in course.levels:
        for before in level.prerequisites:
            if before not in levels:
                raise CurriculumError(f"{level.id} needs unknown level {before}")
        for skill_id in level.skills:
            if skill_id not in skills:
                raise CurriculumError(f"{level.id} teaches unknown skill {skill_id}")
        for tier in level.tiers:
            if not any(t.skill in level.skills and t.tier == tier for t in TEMPLATES.values()):
                raise CurriculumError(f"{level.id} has no {tier} questions")
        if TIERS.index(level.mastery.independent_at) > TIERS.index(level.tiers[-1]):
            raise CurriculumError(f"{level.id} asks for a tier it never sets")
        if level.opener is not None:
            template = TEMPLATES.get(level.opener.template)
            if template is None or template.skill not in level.skills or template.tier != level.tiers[0]:
                raise CurriculumError(f"{level.id} opens with a question from outside its first tier")
            opener = build(template.id, random.Random(0), dict(level.opener.params))
            if opener.question.math in level.lesson.shown or _gives_away(level.lesson, opener.question.expected):
                raise CurriculumError(f"{level.id}'s lesson shows the answer to its first question")
    for template in TEMPLATES.values():
        for misconception in template.targets:
            if misconception not in TAXONOMY:
                raise CurriculumError(f"{template.id} targets unknown misconception {misconception}")
    return course


def _gives_away(lesson: Lesson, answer: str) -> bool:
    """Does the lesson's text say this answer, as a whole term? `5x` is in "= 5x", and not in "15x"."""

    written = " ".join([lesson.intro, *lesson.steps, lesson.example.question, *lesson.example.working, lesson.remember])
    written = written.replace("−", "-")
    # Not part of a longer number or term, and a full stop after it still counts: "= 5x." gives it away.
    return re.search(rf"(?<![\w.]){re.escape(answer)}(?!\w|\.\d)", written) is not None


@cache
def catalog() -> Catalog:
    return Catalog.model_validate(json.loads((CURRICULUM / "catalog.v1.json").read_text()))


@cache
def course(course_id: str) -> Course | None:
    entry = next((path for path in catalog().paths if path.id == course_id), None)
    if entry is None or entry.status != "available" or entry.course is None:
        return None
    return validate(Course.model_validate(json.loads((CURRICULUM / entry.course).read_text())))


def load_all() -> list[Course]:
    """Every course that can be played, checked. Called at start-up, so a broken file is found at once."""

    return [found for path in catalog().paths if (found := course(path.id)) is not None]
