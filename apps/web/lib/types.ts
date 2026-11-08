// Mirrors services/api/quack/schemas.py. Keep the two in step.

export type Point = { x: number; y: number; pressure?: number; timestamp: number };

export type PenStroke = {
  id: string;
  points: Point[];
  color: string;
  width: number;
  createdAt: number;
};

export type Bounds = { minX: number; minY: number; maxX: number; maxY: number };

export type AnalysisStatus = "no_error" | "confirmed_error" | "uncertain" | "unsupported";

export type DuckState = "idle" | "checking" | "alert" | "hint" | "explaining" | "resolved" | "muted";

export type Mistake = {
  id: string;
  category: string;
  errorType: string;
  operationType: string;
  previousStep: string;
  currentStep: string;
  verified: boolean;
  region: { yTop: number; yBottom: number; line: number | null; lineCount: number | null } | null;
  lineId: string | null;
  problemId: string | null;
  parsedOperation: string;
  isNew: boolean;
};

export type InkLine = { id: string; inImage: boolean; spaced: boolean };

export type ModelIssue = { category: string; message: string };

export type CallTiming = {
  purpose: string;
  effort: string;
  ms: number;
  cached: boolean;
  attempts: number;
  inputTokens: number;
  outputTokens: number;
  reasoningTokens: number;
  costUsd: number;
  references: number;
};

export type Timings = {
  interpretMs: number;
  verifyMs: number;
  totalMs: number;
  modelRequests: number;
  costUsd: number;
  cacheHit: boolean;
  stages: Record<string, number>;
  calls: CallTiming[];
};

// Why one line was or wasn't flagged. Development only, and empty otherwise.
export type LineReadingTrace = { math: string; kind: string; legibility: string; alternatives: string[] };
export type LineTrace = {
  lineId: string | null;
  position: number;
  snapshotVersion: number;
  inImage: boolean;
  remembered: boolean;
  first: LineReadingTrace | null;
  second: LineReadingTrace | null;
  used: string;
  settled: boolean;
  verdict: string | null;
  reason: string | null;
  parsedOperation: string | null;
  mistakeId: string | null;
  disposition: string;
};

export type AnalyzeResponse = {
  // The server validates that alerts have new verified mistakes, unique ids, and no resolved overlap.
  snapshotVersion: number;
  requestId: string | null;
  status: AnalysisStatus;
  mistake: Mistake | null;
  mistakes: Mistake[];
  intervention: { shouldQuack: boolean; showHintImmediately: boolean };
  resolvedMistakeIds: string[];
  reason: string | null;
  stale: boolean;
  modelIssue: ModelIssue | null;
  lines: { text: string; kind: string; legibility: string }[];
  settledLineIds: string[];
  unreadLineIds: string[];
  // Lines two readings agree on and the verifier found right, whatever else is on the page.
  verifiedLineIds: string[];
  // Lines whose own ink has been read as different answers. Nothing calls them right until they change.
  contestedLineIds?: string[];
  // Known mistakes gone from what was read, with too little to go on to call them fixed.
  undecidedMistakeIds: string[];
  timings: Timings;
  trace?: LineTrace[];
  clarification: Clarification | null;
  reminder: string | null;
};

export type AssistanceResponse = {
  level: number;
  text: string;
  source: "model" | "template";
  modelIssue: ModelIssue | null;
  totalMs: number;
  modelRequests: number;
};

export type VoiceResponse = {
  transcript: string;
  intent: "hint" | "explain" | "check" | "other";
  question: string;
  level: number | null;
  text: string | null;
  source: "model" | "template" | null;
  modelIssue: ModelIssue | null;
};

export type Trigger = "auto" | "manual";

// What the student has said they're doing. Auto is the default, and nothing has to be chosen.
export type TaskContext = "auto" | "simplify" | "solve";
export type LineIntent = "simplify" | "solve";
export type CheckContext = { taskContext: TaskContext; lineIntents: Record<string, LineIntent> };
export type Clarification = { lineId: string; step: string; question: string };

// Circle to ask.
export type AskAction = "hint" | "explain" | "check" | "question";
export type AskVerdict = "correct" | "incorrect" | "ambiguous_intent" | "unclear" | "unsupported" | "unchecked" | "not_math";
// The rule behind a step, in a few short numbered steps, with what it means for this one at the end.
export type StepExplanation = { concept: string; title: string; steps: string[]; takeaway: string | null };

export type AskResponse = {
  verdict: AskVerdict;
  disposition: string;
  text: string;
  source: "model" | "template";
  // `text` as numbered steps, when it's the rule behind the step. Shown in its place.
  explanation?: StepExplanation | null;
  level: number | null;
  lines: { text: string; kind: string; legibility: string }[];
  clarification: Clarification | null;
  unreadLineIds: string[];
  modelIssue: ModelIssue | null;
  timings: Timings;
};

// Accounts and onboarding.

export type OnboardingStatus = "account_created" | "calibration_in_progress" | "calibration_completed";

export type AuthConfig = { mode: "off" | "local" | "supabase"; supabaseUrl: string | null; supabaseAnonKey: string | null };

export type LocalSession = { userId: string; accessToken: string };

export type Profile = { userId: string; onboardingStatus: OnboardingStatus; hasHandwritingSamples: boolean };

export type CalibrationPrompt = { id: string; display: string; captures: string; minStrokes: number };

export type CalibrationState = {
  version: number;
  prompts: CalibrationPrompt[];
  completedPromptIds: string[];
  onboardingStatus: OnboardingStatus;
};

export type CalibrationExportSample = {
  promptId: string;
  referenceExpression: string;
  image: string;
  strokes: PenStroke[];
  canvasWidth: number;
  createdAt: number;
  calibrationVersion: number;
};

export type CalibrationExport = { samples: CalibrationExportSample[] };

export type DeleteAccountResult = { signInDeleted: boolean };

// Learning history.

export type ClientEventType = "mistake_detected" | "correction_verified" | "mistake_dismissed" | "mistake_invalidated";

export type LearningEventIn = {
  id: string;
  eventType: ClientEventType;
  mistakeId: string;
  sessionId: string | null;
  reason?: "erased" | "replaced" | "misread" | null;
  rewritten?: boolean;
};

export type LearningEventResult = { refused: { id: string; reason: string }[] };

export type StudySession = { id: string; startedAt: number; lastActiveAt: number };

export type RecapGlance = {
  found: number;
  corrected: number;
  unresolved: number;
  independent: number;
  assisted: number;
  hints: number;
  explanations: number;
  erased: number;
  triedAgain: number;
  earlierCorrected: number;
};

export type RecapTopic = { topic: string; label: string; mistakes: number; corrected: number; examples: string[] };
export type RecapTopicGroup = { group: string; topics: RecapTopic[] };
export type Recommendation = { topic: PracticeTopic; label: string; reason: string };

export type Recap = {
  sessionId: string;
  startedAt: number;
  finishedAt: number;
  finishReason: string;
  glance: RecapGlance;
  topics: RecapTopicGroup[];
  observations: string[];
  leftOpen: string[];
  recommendation: Recommendation | null;
  note: string | null;
  opening: string;
  assistedLabels: string[];
};

export type CurrentSession = { session: StudySession; wrappedUp: Recap | null };

export type SessionHistoryItem = {
  sessionId: string;
  startedAt: number;
  finishedAt: number;
  found: number;
  corrected: number;
  topicLabels: string[];
};

export type SessionHistory = { sessions: SessionHistoryItem[] };

// Practice. Nothing but PracticeReveal carries the answer.

export type PracticeTopic = "addition_subtraction" | "multiplication" | "division" | "expanding_brackets" | "combining_like_terms" | "solving_equations" | "power_rule" | "sum_and_constant_rules" | "integration";
export type PracticeOutcome = "in_progress" | "incorrect" | "correct";

export type PracticeProblem = {
  id: string;
  sessionId: string;
  topic: PracticeTopic;
  topicLabel: string;
  difficulty: "basic" | "intermediate";
  prompt: string;
  checkSessionId: string;
};

export type PracticeOptions = { topics: { topic: PracticeTopic; label: string }[] };
export type MisconceptionOut = { id: string; label: string; idea: string };
// `misconception` is only for a lesson question, and only when the maths confirms it.
// `allRead`: both readings agreed on every line, with no doubt. Absent from an API older than round 4.
export type PracticeCheckResponse = AnalyzeResponse & { outcome: PracticeOutcome; misconception?: MisconceptionOut | null; allRead?: boolean };
export type PracticeReveal = { expectedAnswer: string };

export type PracticeSummary = {
  sessionId: string;
  topic: PracticeTopic;
  topicLabel: string;
  attempted: number;
  correct: number;
  revealed: number;
  hints: number;
  message: string;
};

export type RecapDetail = { recap: Recap; practice: PracticeSummary[] };

// My Mistakes, counted from the same events as a recap.

export type MistakeStatus = "open" | "corrected" | "erased" | "tried_again";

export type MistakeEntry = {
  id: string;
  topic: string;
  topicLabel: string;
  topicGroup: string;
  kindLabel: string;
  written: string;
  status: MistakeStatus;
  independent: boolean;
  firstSeenAt: number;
  lastSeenAt: number;
  sessionId: string;
  sessionIds: string[];
  hintsUsed: number;
  explained: boolean;
  practiceTopic: PracticeTopic | null;
  practised: number;
  lesson?: MistakeLesson | null;
};

// Where a mistake made in a lesson was made, and the misconception it showed, if confirmed.
export type MistakeLesson = { courseId: string; levelId: string; levelTitle: string; skillLabel: string; misconception: string | null };

export type MistakeEntryDetail = MistakeEntry & { correctedStep: string | null; hints: { level: number; text: string }[] };

export type MistakeNotebook = { entries: MistakeEntry[] };

// Learning paths and lessons. Mirrors the end of schemas.py. No answer is in any of these.

export type Tier = "learn" | "apply" | "challenge" | "demonstrate";
export type LevelStatus = "locked" | "available" | "in_progress" | "mastered" | "moved_on";

export type LessonContent = {
  intro: string;
  steps: string[];
  example: { question: string; working: string[]; note: string };
  remember: string;
};

export type Mastery = {
  mastered: boolean;
  credits: number;
  creditsNeeded: number;
  distinctTemplates: number;
  distinctTemplatesNeeded: number;
  distinctSkills: number;
  distinctSkillsNeeded: number;
  independent: number;
  independentNeeded: number;
  independentAt: Tier;
  independentAtMet: boolean;
  openMisconceptions: MisconceptionOut[];
  asked: number;
  corrected: number;
  assisted: number;
  canMoveOn: boolean;
};

export type LevelRef = { id: string; number: number; title: string };

export type LevelOut = {
  id: string;
  number: number;
  kind: "level" | "challenge";
  title: string;
  objective: string;
  skills: string[];
  prerequisites: LevelRef[];
  status: LevelStatus;
  mastery: Mastery;
  lesson: LessonContent;
  tiers: Tier[];
};

export type CourseOut = { id: string; version: number; title: string; tagline: string; levels: LevelOut[]; mastered: number; total: number };

export type PathOut = {
  id: string;
  title: string;
  tagline: string;
  status: "available" | "coming_later";
  preview: string[];
  mastered: number | null;
  total: number | null;
};

export type ContinueOut = { courseId: string; courseTitle: string; levelId: string; levelTitle: string; levelNumber: number; status: LevelStatus };
export type LearnHome = { paths: PathOut[]; continueLearning: ContinueOut | null };

export type LessonQuestion = {
  id: string;
  sessionId: string;
  checkSessionId: string;
  instruction: string;
  prompt: string;
  skill: string;
  skillLabel: string;
  tier: Tier;
  number: number;
  status: "open" | "attempted" | "correct" | "revealed" | "skipped";
  nudges: number;
};

export type Selection = { kind: string; reason: string; target: MisconceptionOut | null; basis: Record<string, unknown> };

export type LessonState = {
  courseId: string;
  levelId: string;
  levelNumber: number;
  levelTitle: string;
  sessionId: string;
  question: LessonQuestion | null;
  selection: Selection | null;
  mastery: Mastery;
  status: LevelStatus;
  nextLevel: LevelRef | null;
};

export type Nudge = { level: number; text: string; last: boolean };

export type LessonRecap = {
  sessionId: string;
  levelTitle: string;
  skills: string[];
  asked: number;
  rightAlone: number;
  corrected: number;
  assisted: number;
  revealed: number;
  mistakes: number;
  hints: number;
  misconceptions: { label: string; cleared: boolean }[];
  mastery: Mastery;
  status: LevelStatus;
  message: string;
};
