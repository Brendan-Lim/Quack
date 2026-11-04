-- Portable between SQLite and Postgres. Types are spelled so that both accept them, and JSON is kept
-- as text, since nothing queries inside it. Statements are split on semicolons, so none may contain one.

CREATE TABLE IF NOT EXISTS profiles (
    user_id TEXT PRIMARY KEY,
    onboarding_status TEXT NOT NULL,
    calibration_version INTEGER,
    calibration_completed_at DOUBLE PRECISION,
    created_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS calibration_samples (
    user_id TEXT NOT NULL,
    calibration_version INTEGER NOT NULL,
    prompt_id TEXT NOT NULL,
    reference_expression TEXT NOT NULL,
    strokes_json TEXT NOT NULL,
    canvas_width DOUBLE PRECISION NOT NULL,
    image_data TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (user_id, calibration_version, prompt_id)
);

CREATE TABLE IF NOT EXISTS study_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    parent_session_id TEXT,
    topic TEXT,
    started_at DOUBLE PRECISION NOT NULL,
    last_active_at DOUBLE PRECISION NOT NULL,
    finished_at DOUBLE PRECISION,
    finish_reason TEXT,
    checks INTEGER NOT NULL DEFAULT 0,
    recap_json TEXT
);
CREATE INDEX IF NOT EXISTS study_sessions_by_user ON study_sessions (user_id, kind, started_at);

-- What the verifier established about each mistake. Events are checked against this, and take
-- their topic from it, so that a client can only report mistakes the server really found.
CREATE TABLE IF NOT EXISTS mistake_facts (
    user_id TEXT NOT NULL,
    mistake_id TEXT NOT NULL,
    category TEXT NOT NULL,
    error_type TEXT NOT NULL,
    parsed_operation TEXT NOT NULL,
    topic TEXT NOT NULL,
    current_step TEXT NOT NULL,
    resolved_how TEXT,
    updated_at DOUBLE PRECISION NOT NULL,
    written_step TEXT,
    corrected_step TEXT,
    PRIMARY KEY (user_id, mistake_id)
);

CREATE TABLE IF NOT EXISTS learning_events (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    mistake_id TEXT,
    problem_id TEXT,
    event_type TEXT NOT NULL,
    topic TEXT,
    error_type TEXT,
    source TEXT NOT NULL,
    detail TEXT,
    timestamp DOUBLE PRECISION NOT NULL,
    sequence BIGINT NOT NULL,
    dedupe_key TEXT NOT NULL,
    help_text TEXT,
    UNIQUE (user_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS learning_events_by_session ON learning_events (user_id, session_id, sequence);
CREATE INDEX IF NOT EXISTS learning_events_by_mistake ON learning_events (user_id, mistake_id, sequence);

CREATE TABLE IF NOT EXISTS practice_problems (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    source_mistake_id TEXT,
    topic TEXT NOT NULL,
    difficulty TEXT NOT NULL,
    prompt_math TEXT NOT NULL,
    prompt_display TEXT NOT NULL,
    expected_answer TEXT NOT NULL,
    verification_method TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    completed_at DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS practice_problems_by_session ON practice_problems (user_id, session_id, created_at);

-- Which verified mistakes were found on which lesson question, and the misconception named, if any.
-- A mistake's id is a hash of its maths, so the same id can come up on another question.
CREATE TABLE IF NOT EXISTS problem_mistakes (
    user_id TEXT NOT NULL,
    problem_id TEXT NOT NULL,
    mistake_id TEXT NOT NULL,
    misconception TEXT NOT NULL,
    confidence TEXT NOT NULL,
    evidence TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    -- What the student was shown and given, kept here so that deleting the mistake from My Mistakes
    -- can't change what the question counted for.
    shown INTEGER NOT NULL DEFAULT 0,
    helped INTEGER NOT NULL DEFAULT 0,
    misread INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, problem_id, mistake_id)
);

-- A level's standing that can't be recounted from questions: when it was first mastered, and whether
-- the student chose to move on before mastering it. Mastery itself is counted from the questions.
CREATE TABLE IF NOT EXISTS level_progress (
    user_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    level_id TEXT NOT NULL,
    mastered_at DOUBLE PRECISION,
    moved_on_at DOUBLE PRECISION,
    updated_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (user_id, course_id, level_id)
);
