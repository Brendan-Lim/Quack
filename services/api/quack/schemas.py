"""The HTTP contract. Mirrored by hand in apps/web/lib/types.ts."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

AnalysisStatus = Literal["no_error", "confirmed_error", "uncertain", "unsupported"]
MAX_IMAGE_DATA_URL_CHARS = 8_000_000
# Largest integer a browser can count to exactly. Anything above it isn't a real notebook.
MAX_VERSION = 2**53 - 1
MAX_ACKNOWLEDGED = 20
MAX_ATTEMPT = 8
MAX_INK_LINES = 40


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Region(ApiModel):
    """Position in the submitted image, as fractions of its size."""

    y_top: float
    y_bottom: float
    # Its place among all the lines read. The client checks this against the ink, unlike the
    # height, which is only the model's estimate.
    line: int | None = None
    line_count: int | None = None


class Mistake(ApiModel):
    id: str
    category: str
    error_type: str
    operation_type: str
    previous_step: str
    current_step: str
    # True only when the deterministic verifier established the error.
    verified: bool
    region: Region | None = None
    # The written line this mistake is on, as named by the client. Identifies where to mark it.
    line_id: str | None = None
    # The written line that this mistake's piece of working starts on, where that's known.
    problem_id: str | None = None
    # What the line does, from its own operators: "division", "differentiation".
    parsed_operation: str = ""
    # Not yet told to the student. The client alerts once for each of these.
    is_new: bool = False


class Intervention(ApiModel):
    should_quack: bool = False
    show_hint_immediately: bool = False


class ModelIssue(ApiModel):
    category: str
    message: str


class CallTiming(ApiModel):
    """One model reading, as the analysis waited for it."""

    purpose: str
    effort: str
    ms: float
    # From the reading cache, or shared with an identical reading already in progress.
    cached: bool = False
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0
    # How many samples of the student's own handwriting this reading was shown.
    references: int = 0


class Timings(ApiModel):
    interpret_ms: float = 0
    verify_ms: float = 0
    total_ms: float = 0
    model_requests: int = 0
    cost_usd: float = 0
    cache_hit: bool = False
    # Milliseconds from the request arriving to the end of each stage that ran.
    stages: dict[str, float] = {}
    calls: list[CallTiming] = []


class ReadLineSummary(ApiModel):
    text: str
    kind: str
    legibility: str


class InkLine(ApiModel):
    """One written line in the working being checked, in page order."""

    # Identifies the strokes of the line. The same strokes always give the same id.
    id: str = Field(min_length=1, max_length=64)
    # Drawn in the image. A line that isn't drawn must already have been read and agreed on.
    in_image: bool
    # A wide gap above it, so nothing but a written = can say it follows from the line before.
    spaced: bool = False


class LineReadingTrace(ApiModel):
    math: str
    kind: str
    legibility: str
    alternatives: list[str] = []


class LineTrace(ApiModel):
    """Why one line was or wasn't flagged. Development only. See services/line_trace.py."""

    line_id: str | None = None
    position: int
    snapshot_version: int
    in_image: bool
    remembered: bool
    first: LineReadingTrace | None = None
    second: LineReadingTrace | None = None
    # What was actually checked, after laying the readings out with what's remembered.
    used: str
    settled: bool
    verdict: str | None = None
    reason: str | None = None
    parsed_operation: str | None = None
    mistake_id: str | None = None
    disposition: str


# What the student has said they're doing. Auto is the default, and nothing has to be chosen.
TaskContext = Literal["auto", "simplify", "solve"]
LineIntent = Literal["simplify", "solve"]


class AnalyzeRequest(ApiModel):
    session_id: str = Field(min_length=1, max_length=64)
    snapshot_version: int = Field(ge=0, le=MAX_VERSION)
    image: str = Field(min_length=1, max_length=MAX_IMAGE_DATA_URL_CHARS)
    trigger: Literal["auto", "manual"] = "auto"
    # Mistakes the student has been told about and hasn't fixed. See MistakeManager.
    acknowledged_mistake_ids: list[Annotated[str, Field(max_length=64)]] = Field(default=[], max_length=MAX_ACKNOWLEDGED)
    # Known mistakes whose lines aren't in this image. Not seeing them says nothing about them.
    out_of_view_mistake_ids: list[Annotated[str, Field(max_length=64)]] = Field(default=[], max_length=MAX_ACKNOWLEDGED)
    # Chosen by the client to match its own timings with the server's. Never used for anything else.
    request_id: str | None = Field(default=None, max_length=64)
    # The working being checked, line by line. Absent for an imported image, whose lines are unknown.
    ink_lines: list[InkLine] | None = Field(default=None, max_length=MAX_INK_LINES)
    # Above zero when the client is looking again at lines it couldn't settle. The same image then
    # gets fresh readings, rather than the ones it already has. The client stops after a few tries.
    attempt: int = Field(default=0, ge=0, le=MAX_ATTEMPT)
    # The study session this check belongs to, for a signed-in student.
    study_session_id: str | None = Field(default=None, max_length=64)
    # Overrides QUACK_PERSONALIZATION for one request, for comparing the two. Development only.
    personalization: Literal["off", "on_doubt", "always"] | None = None
    # Whether the student has said this page is for simplifying or for solving.
    task_context: TaskContext = "auto"
    # The answer to a clarifying question, by written line. It outranks everything but what's written.
    line_intents: dict[Annotated[str, Field(max_length=64)], LineIntent] = Field(default={}, max_length=MAX_INK_LINES)
    # The lines now holding the strokes each known mistake was found on. Whether it's been fixed then
    # depends on what's on those lines, and an unsure line elsewhere on the page doesn't come into it.
    mistake_line_ids: dict[
        Annotated[str, Field(max_length=64)],
        Annotated[list[Annotated[str, Field(max_length=64)]], Field(max_length=MAX_INK_LINES)],
    ] = Field(default={}, max_length=MAX_ACKNOWLEDGED)


class Clarification(ApiModel):
    """A short question about a line that's fine one way and wrong another. It's never a verdict."""

    line_id: str
    step: str
    question: str


class AnalyzeResponse(ApiModel):
    snapshot_version: int
    request_id: str | None = None
    status: AnalysisStatus
    # The mistake to show first. `mistakes` holds all those found, each with its own record.
    mistake: Mistake | None = None
    mistakes: list[Mistake] = []
    intervention: Intervention = Intervention()
    resolved_mistake_ids: list[str] = []
    # Why Quack abstained, as a stable code.
    reason: str | None = None
    stale: bool = False
    model_issue: ModelIssue | None = None
    lines: list[ReadLineSummary] = []
    # Lines now read and agreed on, so they needn't be drawn in the next image.
    settled_line_ids: list[str] = []
    # Lines that weren't drawn and aren't remembered, such as after a server restart. Nothing was read.
    unread_line_ids: list[str] = []
    # Lines two readings agree on and the verifier found right, whatever else is on the page.
    verified_line_ids: list[str] = []
    # Lines whose own ink has been read as different answers. Nothing calls them right until they change.
    contested_line_ids: list[str] = []
    # Known mistakes gone from what was read, with too little to go on to call them fixed.
    undecided_mistake_ids: list[str] = []
    timings: Timings = Timings()
    # Why each line was or wasn't flagged. Empty unless QUACK_ENV=development.
    trace: list[LineTrace] = []
    clarification: Clarification | None = None
    # Right as far as it goes, and with something asked for still to add: "missing_constant".
    reminder: str | None = None


class ClearRequest(ApiModel):
    session_id: str = Field(min_length=1, max_length=64)
    snapshot_version: int = Field(ge=0, le=MAX_VERSION)
    acknowledged_mistake_ids: list[Annotated[str, Field(max_length=64)]] = Field(default=[], max_length=MAX_ACKNOWLEDGED)


class HintRequest(ApiModel):
    session_id: str = Field(min_length=1, max_length=64)
    mistake_id: str = Field(min_length=1, max_length=64)
    level: Literal[1, 2]
    study_session_id: str | None = Field(default=None, max_length=64)


class ExplainRequest(ApiModel):
    session_id: str = Field(min_length=1, max_length=64)
    mistake_id: str = Field(min_length=1, max_length=64)
    question: str | None = Field(default=None, max_length=300)
    study_session_id: str | None = Field(default=None, max_length=64)


class AssistanceResponse(ApiModel):
    level: int
    text: str
    # "model" came from Muse Spark. "template" is a fixed fallback used when the model
    # failed or its answer did not pass validation.
    source: Literal["model", "template"]
    model_issue: ModelIssue | None = None
    total_ms: float = 0
    model_requests: int = 0


class VoiceResponse(ApiModel):
    transcript: str
    intent: Literal["hint", "explain", "check", "other"]
    question: str
    level: int | None = None
    text: str | None = None
    source: Literal["model", "template"] | None = None
    model_issue: ModelIssue | None = None


class ResolvedEvent(ApiModel):
    mistake_id: str
    category: str
    resolved_at: float


class SessionSummary(ApiModel):
    session_id: str
    latest_version: int
    known_mistakes: list[Mistake]
    resolved: list[ResolvedEvent]
    alerts: int
    spend_usd: float


class Health(ApiModel):
    status: Literal["ok"] = "ok"
    model_configured: bool
    environment: str


# Accounts and onboarding. See docs/AUTH_AND_PROFILES.md.

OnboardingStatus = Literal["account_created", "calibration_in_progress", "calibration_completed"]
MAX_STROKES = 400
MAX_POINTS_PER_STROKE = 4000


class AuthConfig(ApiModel):
    """How the browser should sign in. Nothing here is secret."""

    mode: Literal["off", "local", "supabase"]
    supabase_url: str | None = None
    supabase_anon_key: str | None = None


class LocalCredentials(ApiModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class LocalSession(ApiModel):
    user_id: str
    access_token: str


class Profile(ApiModel):
    user_id: str
    onboarding_status: OnboardingStatus
    # False once the samples have been deleted. The account still isn't asked to calibrate again.
    has_handwriting_samples: bool


class PointInput(ApiModel):
    x: float = Field(ge=-1e6, le=1e6, allow_inf_nan=False)
    y: float = Field(ge=-1e6, le=1e6, allow_inf_nan=False)
    pressure: float | None = Field(default=None, allow_inf_nan=False)
    timestamp: float = Field(allow_inf_nan=False)


class PenStrokeInput(ApiModel):
    id: str = Field(min_length=1, max_length=64)
    points: list[PointInput] = Field(max_length=MAX_POINTS_PER_STROKE)
    color: str = Field(max_length=32)
    width: float = Field(gt=0, le=100, allow_inf_nan=False)
    created_at: float = Field(allow_inf_nan=False)


class CalibrationPromptOut(ApiModel):
    id: str
    display: str
    captures: str
    min_strokes: int


class CalibrationState(ApiModel):
    version: int
    prompts: list[CalibrationPromptOut]
    # What's saved already, so that onboarding carries on from there on any device.
    completed_prompt_ids: list[str]
    onboarding_status: OnboardingStatus


class SampleUpload(ApiModel):
    strokes: list[PenStrokeInput] = Field(min_length=1, max_length=MAX_STROKES)
    canvas_width: float = Field(gt=0, le=1e5, allow_inf_nan=False)
    image: str = Field(min_length=1, max_length=MAX_IMAGE_DATA_URL_CHARS)


class CalibrationExportSample(ApiModel):
    prompt_id: str
    reference_expression: str
    image: str
    strokes: list[PenStrokeInput]
    canvas_width: float
    created_at: float
    calibration_version: int


class CalibrationExport(ApiModel):
    samples: list[CalibrationExportSample]


class DeleteAccountResult(ApiModel):
    # False if the sign-in itself couldn't be removed from here, and still has to be.
    sign_in_deleted: bool


# Learning history. See docs/LEARNING_EVENTS.md and docs/SESSION_RECAP.md.

ClientEventType = Literal["mistake_detected", "correction_verified", "mistake_dismissed", "mistake_invalidated"]
# `misread`: two readings of ink that hadn't changed now say it was never there, so it wasn't a mistake.
InvalidatedReason = Literal["erased", "replaced", "misread"]


class LearningEventIn(ApiModel):
    # Chosen by the client when it happens, so that sending it again changes nothing.
    id: str = Field(min_length=8, max_length=64)
    event_type: ClientEventType
    mistake_id: str = Field(min_length=1, max_length=64)
    session_id: str | None = Field(default=None, max_length=64)
    reason: InvalidatedReason | None = None
    rewritten: bool = False


class LearningEventBatch(ApiModel):
    events: list[LearningEventIn] = Field(max_length=100)


class RefusedEvent(ApiModel):
    id: str
    reason: str


class LearningEventResult(ApiModel):
    refused: list[RefusedEvent] = []


class StudySession(ApiModel):
    id: str
    started_at: float
    last_active_at: float


class RecapGlance(ApiModel):
    found: int
    corrected: int
    unresolved: int
    # Corrected with no hint or explanation asked for first.
    independent: int
    assisted: int
    hints: int
    explanations: int
    # Gone from the page without two readings saying it's right, so not counted as corrected.
    erased: int = 0
    tried_again: int = 0
    earlier_corrected: int = 0


class RecapTopic(ApiModel):
    topic: str
    label: str
    mistakes: int
    corrected: int
    examples: list[str]


class RecapTopicGroup(ApiModel):
    group: str
    topics: list[RecapTopic]


class Recommendation(ApiModel):
    # A topic that has practice questions.
    topic: str
    label: str
    reason: str


class Recap(ApiModel):
    session_id: str
    started_at: float
    finished_at: float
    finish_reason: str
    glance: RecapGlance
    topics: list[RecapTopicGroup]
    observations: list[str]
    left_open: list[str] = []
    recommendation: Recommendation | None = None
    # Said instead of a recommendation, when there's no evidence for one.
    note: str | None = None
    # A sentence to open with, written from the counts.
    opening: str = ""
    # The topics of what was fixed with help, so the wording can say so without any recount.
    assisted_labels: list[str] = []


class CurrentSession(ApiModel):
    session: StudySession
    # The last session, if this visit is what wrapped it up.
    wrapped_up: Recap | None = None


class SessionHistoryItem(ApiModel):
    session_id: str
    started_at: float
    finished_at: float
    found: int
    corrected: int
    topic_labels: list[str]


class SessionHistory(ApiModel):
    sessions: list[SessionHistoryItem]


# Practice. See docs/TARGETED_PRACTICE.md. Nothing here but PracticeReveal carries the answer.

PracticeTopic = Literal[
    "addition_subtraction", "multiplication", "division", "expanding_brackets", "combining_like_terms",
    "solving_equations", "power_rule", "sum_and_constant_rules", "integration",
]
PracticeOutcome = Literal["in_progress", "incorrect", "correct"]


class PracticeProblem(ApiModel):
    id: str
    session_id: str
    topic: PracticeTopic
    topic_label: str
    difficulty: Literal["basic", "intermediate"]
    prompt: str
    # What checks and hints for this problem go under, so its mistakes stay apart from the notebook's.
    check_session_id: str


class PracticeStart(ApiModel):
    topic: PracticeTopic | None = None
    origin_session_id: str | None = Field(default=None, max_length=64)
    # Started from an entry in My Mistakes. Only ever says where it came from.
    source_mistake_id: str | None = Field(default=None, max_length=64)


class PracticeTopicOption(ApiModel):
    topic: PracticeTopic
    label: str


class PracticeOptions(ApiModel):
    topics: list[PracticeTopicOption]


class MisconceptionOut(ApiModel):
    id: str
    label: str
    # The idea that fixes it, in one line.
    idea: str


class PracticeCheckResponse(AnalyzeResponse):
    outcome: PracticeOutcome = "in_progress"
    # A lesson question only: the misconception a verified mistake here shows, when the maths confirms it.
    misconception: MisconceptionOut | None = None
    # Every line on the page was read the same way by both readings, with no doubt from either. When it's
    # false and nothing's wrong, Quack can't read some of it yet, which isn't the same as nothing being wrong.
    all_read: bool = False


class PracticeReveal(ApiModel):
    expected_answer: str


class PracticeSummary(ApiModel):
    session_id: str
    topic: PracticeTopic
    topic_label: str
    attempted: int
    correct: int
    revealed: int
    hints: int
    message: str


class RecapDetail(ApiModel):
    recap: Recap
    practice: list[PracticeSummary] = []


# My Mistakes. See docs/MISTAKE_NOTEBOOK.md. Counted from the same events as a recap.

MistakeStatus = Literal["open", "corrected", "erased", "tried_again"]


class MistakeEntry(ApiModel):
    id: str
    topic: str
    topic_label: str
    topic_group: str
    kind_label: str
    # What the student wrote. Only ever what two readings agreed the line said.
    written: str
    status: MistakeStatus
    independent: bool
    first_seen_at: float
    last_seen_at: float
    session_id: str
    session_ids: list[str]
    hints_used: int
    explained: bool
    practice_topic: PracticeTopic | None = None
    practised: int = 0
    lesson: MistakeLesson | None = None


class MistakeLesson(ApiModel):
    """Where a mistake made in a lesson was made, and what it showed, if that was confirmed."""

    course_id: str
    level_id: str
    level_title: str
    skill_label: str
    misconception: str | None = None


class MistakeHint(ApiModel):
    level: int
    text: str


class MistakeEntryDetail(MistakeEntry):
    # What it should have said. Only in the detail, and the app asks first if it's still open.
    corrected_step: str | None = None
    hints: list[MistakeHint] = []


class MistakeNotebook(ApiModel):
    entries: list[MistakeEntry]


# Circle to ask. See docs/CIRCLE_TO_ASK.md.

AskAction = Literal["hint", "explain", "check", "question"]
AskVerdict = Literal["correct", "incorrect", "ambiguous_intent", "unclear", "unsupported", "unchecked", "not_math"]
MAX_SELECTED_LINES = 8


class AskRequest(ApiModel):
    session_id: str = Field(min_length=1, max_length=64)
    action: AskAction
    question: str | None = Field(default=None, max_length=300)
    # The second hint knows the first. Asking again about the same selection asks for this.
    level: Literal[1, 2] = 1
    # The lines drawn, if any aren't remembered yet. With everything remembered, there's no image at all.
    image: str | None = Field(default=None, min_length=1, max_length=MAX_IMAGE_DATA_URL_CHARS)
    # The circled lines, and the lines above them that they may follow from, in page order.
    ink_lines: list[InkLine] = Field(min_length=1, max_length=MAX_SELECTED_LINES + 3)
    selected_line_ids: list[Annotated[str, Field(max_length=64)]] = Field(min_length=1, max_length=MAX_SELECTED_LINES)
    # Circled lines where the loop only went round part of what's written.
    partial_line_ids: list[Annotated[str, Field(max_length=64)]] = Field(default=[], max_length=MAX_SELECTED_LINES)
    # A mistake already on one of these lines. The answer then comes from what's on record about it.
    mistake_id: str | None = Field(default=None, max_length=64)
    task_context: TaskContext = "auto"
    line_intents: dict[Annotated[str, Field(max_length=64)], LineIntent] = Field(default={}, max_length=MAX_INK_LINES)


class StepExplanation(ApiModel):
    """The rule behind a step, in a few short numbered steps, with what it means for this one at the end."""

    concept: str
    title: str
    steps: list[str]
    takeaway: str | None = None


class AskResponse(ApiModel):
    verdict: AskVerdict
    disposition: str
    text: str
    source: Literal["model", "template"]
    # `text` as numbered steps, when it's the rule behind the step. Shown in its place.
    explanation: StepExplanation | None = None
    # Set when this was a hint at a level, so that the next ask can go further.
    level: int | None = None
    lines: list[ReadLineSummary] = []
    clarification: Clarification | None = None
    # Not remembered after all, as after a restart. Nothing was read, so ask again with them drawn.
    unread_line_ids: list[str] = []
    model_issue: ModelIssue | None = None
    timings: Timings = Timings()


# Learning paths and lessons. See docs/QUACK_ADAPTIVE_LEARNING_PHASE3_REPORT.md. No answer is in any of these.

Tier = Literal["learn", "apply", "challenge", "demonstrate"]
LevelStatus = Literal["locked", "available", "in_progress", "mastered", "moved_on"]


class LessonExampleOut(ApiModel):
    question: str
    working: list[str]
    note: str = ""


class LessonContentOut(ApiModel):
    intro: str
    steps: list[str]
    example: LessonExampleOut
    remember: str


class MasteryOut(ApiModel):
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
    open_misconceptions: list[MisconceptionOut] = []
    asked: int
    corrected: int
    assisted: int
    can_move_on: bool


class LevelRef(ApiModel):
    id: str
    number: int
    title: str


class LevelOut(ApiModel):
    id: str
    number: int
    kind: Literal["level", "challenge"]
    title: str
    objective: str
    skills: list[str]
    prerequisites: list[LevelRef]
    status: LevelStatus
    mastery: MasteryOut
    lesson: LessonContentOut
    tiers: list[Tier]


class CourseOut(ApiModel):
    id: str
    version: int
    title: str
    tagline: str
    levels: list[LevelOut]
    mastered: int
    total: int


class PathOut(ApiModel):
    id: str
    title: str
    tagline: str
    status: Literal["available", "coming_later"]
    preview: list[str] = []
    # Only for a path that can be played, and only from the student's own questions.
    mastered: int | None = None
    total: int | None = None


class ContinueOut(ApiModel):
    course_id: str
    course_title: str
    level_id: str
    level_title: str
    level_number: int
    status: LevelStatus


class LearnHome(ApiModel):
    paths: list[PathOut]
    continue_learning: ContinueOut | None = None


class LessonQuestionOut(ApiModel):
    id: str
    session_id: str
    # What checks and hints for this question go under. See PracticeProblem.
    check_session_id: str
    instruction: str
    prompt: str
    skill: str
    skill_label: str
    tier: Tier
    # This question's place in the visit, from 1.
    number: int
    status: Literal["open", "attempted", "correct", "revealed", "skipped"]
    nudges: int


class SelectionOut(ApiModel):
    kind: str
    reason: str
    target: MisconceptionOut | None = None
    # What the choice rested on: the last question's outcome and misconceptions, and the rule used.
    basis: dict[str, Any] = {}


class LessonState(ApiModel):
    course_id: str
    level_id: str
    level_number: int
    level_title: str
    session_id: str
    question: LessonQuestionOut | None = None
    selection: SelectionOut | None = None
    mastery: MasteryOut
    status: LevelStatus
    next_level: LevelRef | None = None


class LessonNext(ApiModel):
    # The question the student is moving on from. Asking twice for the same one gives the same next one.
    current_problem_id: str | None = Field(default=None, max_length=64)


class NudgeRequest(ApiModel):
    level: int = Field(ge=1, le=3)


class NudgeOut(ApiModel):
    level: int
    text: str
    last: bool


class RecapMisconception(ApiModel):
    label: str
    cleared: bool


class LessonRecap(ApiModel):
    session_id: str
    level_title: str
    skills: list[str]
    asked: int
    right_alone: int
    corrected: int
    assisted: int
    revealed: int
    mistakes: int
    hints: int
    misconceptions: list[RecapMisconception]
    mastery: MasteryOut
    status: LevelStatus
    message: str
