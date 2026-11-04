"use client";

import { ArrowLeft, ArrowRight, BookOpen, Check, Eraser, Flag, Info, LockOpen, Lightbulb, Pen, PenLine, RotateCcw, ScanEye, Search, Sparkles, Undo2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { checkPracticeAnswer, revealAnswer } from "@/lib/account";
import { setStudySession } from "@/lib/api";
import { askNudge, fetchCourse, finishLesson, lessonState, masteredMessage, masteryChecklist, masteryRule, masteryShare, nextQuestion, notYetRight, POINTS_RULES, progressLabel, startLevel, stillNeeded, TIER_LABELS } from "@/lib/learn";
import type { NotYetRight } from "@/lib/learn";
import { groupIntoLines } from "@/lib/lines";
import type { Marker } from "@/lib/mistakes";
import { lineId } from "@/lib/snapshot";
import type { LearningQueue } from "@/lib/learning";
import { prettyMath } from "@/lib/mistakes";
import { playSuccess } from "@/lib/sound";
import type { AnalyzeResponse, LessonContent, LessonRecap, LessonState, LevelOut, MisconceptionOut, PracticeCheckResponse, Trigger } from "@/lib/types";
import { useQuack, type QuackOptions } from "@/hooks/useQuack";
import { Duck } from "../duck/Duck";
import { DuckCompanion } from "../duck/DuckCompanion";
import { ModeSwitch } from "../nav/ModeSwitch";
import { NotebookCanvas, type Tool } from "../notebook/NotebookCanvas";
import { toolClass } from "@/lib/toolClass";

type Stage = "intro" | "question" | "recap";

type Props = {
  courseId: string;
  levelId: string;
  queue: LearningQueue;
  // Shown, not kept hidden under the notebook.
  active?: boolean;
  onNotebook?: () => void;
  onRoadmap: (focus?: string) => void;
  onLevel: (levelId: string) => void;
};

// One level: learn the idea, see it worked, then answer questions in handwriting. Which question comes
// next, and whether the level is mastered, is decided on the server from what the verifier found.
export function LessonView({ courseId, levelId, queue, active = true, onNotebook, onRoadmap, onLevel }: Props) {
  const [level, setLevel] = useState<LevelOut | null>(null);
  const [lesson, setLesson] = useState<LessonState | null>(null);
  const [stage, setStage] = useState<Stage>("intro");
  const [recap, setRecap] = useState<LessonRecap | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [celebrating, setCelebrating] = useState(false);
  const [moving, setMoving] = useState(false);
  // Mastered before this visit began, so finishing a question here isn't a new achievement.
  const masteredBefore = useRef<boolean | null>(null);
  const started = useRef(false);

  useEffect(() => {
    // Once, even though development runs effects twice. A second start would race the first.
    if (started.current) return;
    started.current = true;
    Promise.all([fetchCourse(courseId), startLevel(courseId, levelId)]).then(
      ([course, state]) => {
        setLevel(course.levels.find((item) => item.id === levelId) ?? null);
        masteredBefore.current = state.status === "mastered";
        setLesson(state);
        // Coming back to a question already under way goes straight to it.
        setStage(state.question && (state.question.number > 1 || state.question.status !== "open") ? "question" : "intro");
      },
      () => setProblem("Quack can't open this level right now. Check the connection and try again."),
    );
  }, [courseId, levelId]);

  useEffect(() => {
    if (!lesson || !active) return;
    // Hints asked for here belong to this lesson, not to the notebook's session. Again on coming back from the
    // notebook, which set its own while it was open.
    setStudySession(() => lesson.sessionId);
  }, [lesson, active]);

  const refresh = useCallback(async () => {
    if (!lesson) return;
    // The events about what was shown go first, so the server counts what the student saw.
    await queue.flush();
    const state = await lessonState(lesson.sessionId);
    setLesson(state);
    if (state.status === "mastered" && masteredBefore.current === false) {
      masteredBefore.current = true;
      setCelebrating(true);
    }
  }, [lesson, queue]);

  const next = useCallback(async () => {
    if (!lesson || moving) return;
    setMoving(true);
    setProblem(null);
    try {
      await queue.flush();
      setLesson(await nextQuestion(lesson.sessionId, lesson.question?.id ?? null));
      setStage("question");
    } catch {
      setProblem("Quack can't find the next question right now. Try again in a moment.");
    } finally {
      setMoving(false);
    }
  }, [lesson, moving, queue]);

  const finish = useCallback(async () => {
    if (!lesson) return onRoadmap(levelId);
    try {
      await queue.flush();
      setRecap(await finishLesson(lesson.sessionId));
      setStage("recap");
    } catch {
      onRoadmap(levelId);
    }
  }, [lesson, queue, onRoadmap, levelId]);

  if (!lesson || !level) {
    return (
      <main className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
        <Duck state={problem ? "idle" : "checking"} size={72} />
        <p data-testid="lesson-loading" className="max-w-sm text-[15px] text-muted">{problem ?? "Getting your lesson ready..."}</p>
        {problem && <button type="button" onClick={() => onRoadmap(levelId)} className="min-h-11 rounded-xl bg-ink px-4 font-semibold text-paper">Back to the path</button>}
      </main>
    );
  }

  if (stage === "recap" && recap) return <RecapScreen recap={recap} lesson={lesson} onRoadmap={() => onRoadmap(levelId)} onLevel={onLevel} />;

  if (stage === "intro") {
    return <Intro level={level} lesson={lesson} onBack={() => onRoadmap(levelId)} onStart={() => setStage("question")} onNotebook={onNotebook} />;
  }

  return (
    <>
      {lesson.question && (
        <Sheet
          key={lesson.question.id}
          lesson={lesson}
          level={level}
          queue={queue}
          moving={moving}
          problem={problem}
          onCorrect={() => void refresh().catch(() => {})}
          onNext={() => void next()}
          onFinish={() => void finish()}
          onBack={() => onRoadmap(levelId)}
          onNotebook={onNotebook}
          active={active}
        />
      )}
      {celebrating && (
        <Celebration
          lesson={lesson}
          onNextLevel={lesson.nextLevel ? () => onLevel(lesson.nextLevel!.id) : null}
          onRoadmap={() => onRoadmap(levelId)}
          onKeepGoing={() => setCelebrating(false)}
        />
      )}
    </>
  );
}

// How far through mastering the level, as a bar and in words, with a flag for the question on your own that
// every level asks for. On the iPad it was a 12px label beside a thin bar, and on phones it wasn't shown at all.
function MasteryMeter({ lesson }: { lesson: LessonState }) {
  const share = masteryShare(lesson.mastery);
  const [explaining, setExplaining] = useState(false);
  const label = progressLabel(lesson.mastery, lesson.status);
  const flagged = lesson.mastery.independentAtMet || lesson.mastery.mastered;
  return (
    <div className="relative order-last flex w-full items-center gap-2.5 pb-2 md:order-none md:w-40 lg:w-56 md:flex-col md:items-stretch md:gap-1 md:pb-0" title="Progress towards mastering this level">
      <div className="flex min-w-0 flex-1 items-center gap-2 md:flex-none">
        <div className="h-3 flex-1 overflow-hidden rounded-full bg-ink/[0.07]" role="progressbar" aria-label="Progress towards mastery" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(share * 100)}>
          <div key={share} className="progress-fill h-full rounded-full bg-duck-deep" style={{ width: `${Math.max(share * 100, 4)}%` }} />
        </div>
        <span
          aria-hidden
          title={flagged ? "Challenge on your own: done" : "Still needs a challenge on your own"}
          className={`flex size-7 shrink-0 items-center justify-center rounded-full ${flagged ? "bg-duck text-ink" : "bg-ink/[0.06] text-ink/40"}`}
        >
          <Flag size={14} strokeWidth={2.5} />
        </span>
      </div>
      {/* Points aren't questions: on the iPad, "0.5 of 3 points" after three questions read as questions lost. */}
      <button
        type="button"
        data-testid="lesson-progress"
        aria-expanded={explaining}
        aria-label={`${label}. How points work`}
        onClick={() => setExplaining((open) => !open)}
        className="flex min-h-9 shrink-0 items-center gap-1 rounded-lg px-1 text-[14px] font-semibold whitespace-nowrap text-ink-soft hover:text-ink md:min-h-6 md:self-start md:px-0"
      >
        {label}
        <Info size={14} aria-hidden className="text-muted" />
      </button>
      {explaining && (
        <div data-testid="points-explained" className="pop-in absolute top-full right-0 z-30 mt-2 w-[min(20rem,calc(100vw-1.5rem))] rounded-2xl bg-surface p-4 text-left text-[14px] leading-snug shadow-pop ring-1 ring-ink/10 md:left-0">
          <div className="flex items-start justify-between gap-2">
            <p className="font-display text-[17px] font-bold">Points aren&apos;t questions</p>
            <button type="button" aria-label="Close" onClick={() => setExplaining(false)} className="-mt-2 -mr-2 flex size-11 items-center justify-center rounded-full hover:bg-ink/5"><X size={16} aria-hidden /></button>
          </div>
          <ul className="mt-2 space-y-1.5">
            {POINTS_RULES.map((rule) => (
              <li key={rule} className="flex gap-2"><span aria-hidden className="mt-[7px] size-1.5 shrink-0 rounded-full bg-duck-deep" />{rule}</li>
            ))}
          </ul>
          <p className="mt-3 rounded-xl bg-paper-deep/60 px-3 py-2 text-[13.5px] text-ink-soft">{masteryRule(lesson.mastery)}</p>
        </div>
      )}
    </div>
  );
}

function TopBar({ lesson, level, onBack, onNotebook, children }: { lesson: LessonState; level: LevelOut; onBack: () => void; onNotebook?: () => void; children?: React.ReactNode }) {
  return (
    <header className="flex shrink-0 flex-wrap items-center gap-x-2 border-b border-line bg-paper/95 px-2 pt-1.5 sm:px-4 md:h-16 md:flex-nowrap md:gap-3 md:pt-0">
      <button type="button" data-testid="lesson-back" onClick={onBack} aria-label="Back to the path" className={toolClass(false)}>
        <ArrowLeft size={22} aria-hidden />
      </button>
      <div className="min-w-0 flex-1">
        <p className="eyebrow truncate !text-[12px] sm:!text-[12.5px]">{level.kind === "challenge" ? "Final challenge" : `Level ${level.number}`}</p>
        <p className="truncate font-display text-[18px] leading-tight font-bold sm:text-[19px]">{level.title}</p>
      </div>
      {/* Below the iPad's width the meter and the switch share a row of their own, so the title keeps its room.
          From there, one row, with the switch at the end. */}
      <div className="order-last flex w-full min-w-0 items-center gap-2 md:contents">
        <MasteryMeter lesson={lesson} />
        {onNotebook && (
          <div className="shrink-0 pb-2 md:order-last md:pb-0">
            <ModeSwitch mode="learn" compact onNotebook={onNotebook} onLearn={() => {}} />
          </div>
        )}
      </div>
      {children}
    </header>
  );
}

function Steps({ content }: { content: LessonContent }) {
  return (
    <ol className="space-y-3">
      {content.steps.map((step, index) => (
        <li key={step} className="flex gap-3 text-[16px] leading-snug">
          <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-duck font-display text-[14px] font-bold shadow-[inset_0_-2px_0_var(--color-duck-edge)]">{index + 1}</span>
          <span className="pt-0.5">{prettyMath(step)}</span>
        </li>
      ))}
    </ol>
  );
}

function Example({ content }: { content: LessonContent }) {
  return (
    <div>
      <p className="font-display text-xl font-bold">{prettyMath(content.example.question)}</p>
      <div className="mt-3 space-y-1.5 rounded-2xl bg-paper p-4 font-display text-[22px] leading-[38px] ring-1 ring-line" style={{ backgroundImage: "linear-gradient(var(--color-line) 1px, transparent 1px)", backgroundSize: "100% 38px", backgroundPosition: "0 37px" }}>
        {content.example.working.map((line, index) => (
          <p key={line} className="lesson-line" style={{ animationDelay: `${300 + index * 450}ms` }}>{prettyMath(line)}</p>
        ))}
      </div>
      {content.example.note && <p className="mt-2 text-[14px] text-muted">{content.example.note}</p>}
    </div>
  );
}

function Intro({ level, lesson, onBack, onStart, onNotebook }: { level: LevelOut; lesson: LessonState; onBack: () => void; onStart: () => void; onNotebook?: () => void }) {
  return (
    <div className="flex h-full flex-col">
      <TopBar lesson={lesson} level={level} onBack={onBack} onNotebook={onNotebook} />
      <main data-testid="lesson-intro" className="notebook-scroll flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-5xl flex-col gap-5 px-4 py-6 sm:px-6 sm:py-8">
          <div className="question-in flex items-center gap-4">
            <span className="flex size-[76px] shrink-0 items-center justify-center rounded-full bg-duck-soft"><Duck state="hint" size={60} /></span>
            <div className="min-w-0">
              <p className="eyebrow">The idea</p>
              <p className="font-display text-[22px] leading-tight font-bold sm:text-[26px]">{level.lesson.intro}</p>
            </div>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <section aria-labelledby="learn-title" className="card p-5 sm:p-6">
              <h2 id="learn-title" className="eyebrow mb-4 flex items-center gap-2"><BookOpen size={16} aria-hidden />Learn</h2>
              <Steps content={level.lesson} />
              <p className="mt-5 flex gap-2 rounded-2xl bg-duck-soft px-3.5 py-2.5 text-[15px] font-medium"><Lightbulb size={18} aria-hidden className="mt-0.5 shrink-0 text-duck-ink" />{prettyMath(level.lesson.remember)}</p>
            </section>
            <section aria-labelledby="example-title" className="card p-5 sm:p-6">
              <h2 id="example-title" className="eyebrow mb-4 flex items-center gap-2"><Sparkles size={16} aria-hidden />See an example</h2>
              <Example content={level.lesson} />
            </section>
          </div>
          <button type="button" data-testid="lesson-try" onClick={onStart} className="btn btn-primary btn-lg self-center px-10 font-display text-lg">
            <Pen size={20} aria-hidden />
            Try it yourself
          </button>
        </div>
      </main>
    </div>
  );
}

type SheetProps = {
  lesson: LessonState;
  level: LevelOut;
  queue: LearningQueue;
  moving: boolean;
  problem: string | null;
  onCorrect: () => void;
  onNext: () => void;
  onFinish: () => void;
  onBack: () => void;
  onNotebook?: () => void;
  active?: boolean;
};

// One question, on the same notebook and duck as everywhere else. Its own store and check session, so
// nothing from the question before (hints, highlights, mistakes or replies) can come along.
function Sheet({ lesson, level, queue, moving, problem, onCorrect, onNext, onFinish, onBack, onNotebook, active = true }: SheetProps) {
  const question = lesson.question!;
  const [done, setDone] = useState(question.status === "correct");
  const [noticed, setNoticed] = useState<MisconceptionOut | null>(null);
  const [answer, setAnswer] = useState<string | null>(null);
  const [nudges, setNudges] = useState<string[]>([]);
  const [waitingForCheck, setWaitingForCheck] = useState(false);
  // Asked to check, nothing wrong, and no finished answer: because some of it can't be read yet, or because
  // the answer isn't written. On the iPad, `= 3x` read two ways got "Nothing looks wrong", which wasn't so.
  // It's about the ink as it was checked, so it remembers which version that was and which lines were read.
  // On 2026-11-04 "can't quite read some of your writing" stayed up beside "checking your working" through
  // several rewrites, as if it were about each of them.
  const [notYet, setNotYet] = useState<{ kind: NotYetRight; version: number; settled: string[] } | null>(null);
  const [showIdea, setShowIdea] = useState(lesson.selection?.kind === "concept_review");
  const [tool, setTool] = useState<Tool>("pen");
  const [cheer, setCheer] = useState<string | null>(null);
  // Answered right before this page opened, as after a reload, isn't a new success to celebrate.
  const celebrated = useRef(question.status === "correct");

  const options = useMemo<QuackOptions>(
    () => ({
      persist: false,
      sessionId: question.checkSessionId,
      analyzeWith: checkPracticeAnswer(question.id) as QuackOptions["analyzeWith"],
      readAllLines: true,
      alwaysWatching: true,
      learning: queue.forSession(lesson.sessionId),
      onApplied: (result: AnalyzeResponse, trigger: Trigger) => {
        const checked = result as PracticeCheckResponse;
        // What the mistakes on the page now show, not what an earlier one did: on the iPad, `= 3x` kept the
        // "letter went missing" of the `= 3` before it.
        if (!checked.stale) setNoticed(checked.mistakes.length ? (checked.misconception ?? null) : null);
        if (checked.outcome === "correct") {
          setDone(true);
          setNotYet(null);
          // Once, when two readings agree on every line and the last one finishes it. Not again for a
          // later check of the same page, and never for "nothing wrong yet", which isn't right.
          if (!celebrated.current) {
            celebrated.current = true;
            setCheer(CHEERS[question.number % CHEERS.length]);
          }
          onCorrect();
        } else if (trigger === "manual" && checked.outcome === "in_progress" && !checked.mistakes.length) {
          // Asked to check, and nothing wrong, but no finished answer either. Absence of a mistake isn't
          // right. Only in answer to being asked: a check Quack started may be from mid-sentence.
          setNotYet({ kind: notYetRight(checked), version: checked.snapshotVersion, settled: checked.settledLineIds });
        }
        setWaitingForCheck(false);
      },
      onFailed: () => setWaitingForCheck(false),
    }),
    [question, queue, lesson.sessionId, onCorrect],
  );
  const quack = useQuack(options);
  const notebook = useSyncExternalStore(quack.store.subscribe, quack.store.getState, quack.store.getState);

  const muted = quack.muted;
  const activeRef = useRef(active);
  useEffect(() => {
    activeRef.current = active;
  }, [active]);
  useEffect(() => {
    if (!cheer) return;
    // Quiet, short, and only if sound is on. Safari may not have unlocked audio yet, and that's fine. Not from a
    // lesson waiting behind the notebook: the student is writing something else, and would hear it for nothing.
    if (!muted && activeRef.current) playSuccess();
    const timer = setTimeout(() => setCheer(null), CHEER_MS);
    return () => clearTimeout(timer);
    // The sound is for the moment it's earned, not for turning sound on afterwards.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cheer]);

  // A check under way, said quietly once it has taken a moment, so a short pause doesn't flicker. Readings
  // take several seconds, and on the iPad nothing showed that Quack was looking. It says nothing about the answer.
  // This is the scheduler's own busy state: the duck's "checking" only shows from idle, so while a flagged line's
  // fix was checked on the iPad on 2026-11-04, nothing said so for 14 to 16 seconds.
  const checking = quack.checking && !done;
  const [showChecking, setShowChecking] = useState(false);
  // Every check waits for two independent readings, which on the iPad took 5 to 18 seconds. Said once it's long.
  const [checkingLong, setCheckingLong] = useState(false);
  useEffect(() => {
    const shown = setTimeout(() => setShowChecking(checking), checking ? CHECKING_SHOWN_AFTER_MS : 0);
    const long = setTimeout(() => setCheckingLong(checking), checking ? CHECKING_LONG_AFTER_MS : 0);
    return () => {
      clearTimeout(shown);
      clearTimeout(long);
    };
  }, [checking]);

  const written = notebook.strokes.length > 0;
  const checkAnswer = () => {
    // With nothing written there's nothing to check, and no reply would come to end the wait.
    if (!written) return;
    setWaitingForCheck(true);
    setNotYet(null);
    quack.checkNow();
  };

  const nudge = async () => {
    const given = await askNudge(question.id, nudges.length + 1);
    setNudges((before) => [...before, given.text]);
  };

  const reveal = async () => {
    const { expectedAnswer } = await revealAnswer(question.id);
    setAnswer(expectedAnswer);
  };

  // Only while it's still about what's on the page, and never beside a newer check that's under way.
  const current = notYet && notYet.version === notebook.version && !checking ? notYet : null;
  const unfinished = current?.kind ?? null;
  // Which lines it couldn't read, outlined, when it's some of them and not all: "the marked line" says where.
  const unreadLines = useMemo(() => {
    if (current?.kind !== "unread") return [];
    const settled = new Set(current.settled);
    const lines = groupIntoLines(notebook.strokes);
    const unread = lines.filter((line) => !settled.has(lineId(line.strokes)));
    return unread.length && unread.length < lines.length ? unread : [];
  }, [current, notebook.strokes]);
  const markers = useMemo<Marker[]>(
    () => [
      ...quack.markers,
      ...unreadLines.map((line, index) => ({
        id: `unread-${index}`,
        kind: "unread" as const,
        bounds: { minX: line.bounds.minX - 6, minY: line.bounds.minY - 6, maxX: line.bounds.maxX + 6, maxY: line.bounds.maxY + 6 },
      })),
    ],
    [quack.markers, unreadLines],
  );

  const selection = lesson.selection;
  const adaptive = selection && selection.kind !== "start";
  const left = stillNeeded(lesson.mastery);
  // Nothing written and nothing to say yet: say what to do, once, quietly.
  const quiet = !done && !written && !noticed && !unfinished && !nudges.length && !answer && !problem;

  return (
    <div data-testid="lesson" data-question={question.id} className="flex h-full flex-col">
      <TopBar lesson={lesson} level={level} onBack={onBack} onNotebook={onNotebook}>
        <div className="flex shrink-0 items-center gap-1">
          <button type="button" data-testid="lesson-idea" onClick={() => setShowIdea((open) => !open)} aria-pressed={showIdea} aria-label="The idea" className={`btn btn-ghost px-3 ${showIdea ? "bg-duck-soft" : ""}`}>
            <BookOpen size={18} aria-hidden />
            <span className="hidden lg:inline">The idea</span>
          </button>
          <button
            type="button"
            data-testid="lesson-finish"
            onClick={onFinish}
            aria-label={lesson.status === "mastered" ? "Finish this lesson" : "Stop for now. The level stays in progress"}
            className="btn btn-secondary px-3"
          >
            <Flag size={16} aria-hidden />
            <span className="hidden sm:inline">{lesson.status === "mastered" ? "Finish" : "Stop for now"}</span>
          </button>
        </div>
      </TopBar>

      <section className="shrink-0 px-2.5 pt-2.5 sm:px-4 sm:pt-3">
        <div className="card question-in px-3.5 pt-3 pb-2.5 sm:px-5 sm:pt-3.5">
          {left.length > 0 && (
            <p data-testid="lesson-still-needed" className="mb-2.5 flex items-start gap-2 rounded-xl bg-duck-soft px-3 py-2 text-[14px] leading-snug">
              <Flag size={16} aria-hidden className="mt-0.5 shrink-0 text-duck-ink" />
              <span>
                <span className="font-semibold">Points done.</span> To master this level, Quack still needs to see {left.map((item) => item.charAt(0).toLowerCase() + item.slice(1)).join(", and ")}.
              </span>
            </p>
          )}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2.5">
            <div className="min-w-0 flex-1 basis-56">
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13.5px] text-muted">
                <span className="font-semibold text-ink-soft">Question {question.number}</span>
                <span data-testid="lesson-tier" className={`rounded-full px-2.5 py-0.5 text-[12.5px] font-bold ${question.tier === "challenge" || question.tier === "demonstrate" ? "bg-duck text-ink" : "bg-ink/[0.06] text-ink-soft"}`}>{TIER_LABELS[question.tier]}</span>
                <span className="hidden sm:inline">{question.skillLabel}</span>
              </p>
              <p data-testid="lesson-prompt" className={`mt-0.5 font-display leading-tight font-bold tracking-tight ${question.prompt.length > 60 ? "text-[19px] sm:text-[23px]" : "text-[26px] sm:text-[32px]"}`}>{prettyMath(question.prompt)}</p>
            </div>
            <div className="flex w-full shrink-0 flex-wrap items-center gap-2 sm:w-auto sm:flex-nowrap sm:justify-end">
              <div role="toolbar" aria-label="Writing tools" className="flex items-center gap-0.5 rounded-2xl bg-paper-deep/70 p-1">
                <button type="button" aria-label="Pen" aria-pressed={tool === "pen"} onClick={() => setTool("pen")} className={toolClass(tool === "pen")}><Pen size={19} aria-hidden /></button>
                <button type="button" aria-label="Eraser" aria-pressed={tool === "eraser"} onClick={() => setTool("eraser")} className={toolClass(tool === "eraser")}><Eraser size={19} aria-hidden /></button>
                <button type="button" aria-label="Undo" disabled={!notebook.undoStack.length} onClick={quack.store.undo} className={toolClass(false)}><Undo2 size={19} aria-hidden /></button>
                <button type="button" data-testid="lesson-clear" aria-label="Start this page again" onClick={quack.store.clear} className={toolClass(false)}><RotateCcw size={19} aria-hidden /></button>
              </div>
              <div className="ml-auto flex items-center gap-1">
                {!done && question.nudges > nudges.length && (
                  <button type="button" data-testid="lesson-nudge" aria-label={nudges.length ? "Another nudge" : "Need a nudge?"} title={nudges.length ? "Another nudge" : "Need a nudge?"} onClick={() => void nudge().catch(() => {})} className="btn btn-ghost px-2.5 lg:px-3">
                    <Lightbulb size={19} aria-hidden className="text-duck-ink" />
                    <span className="hidden lg:inline">{nudges.length ? "Another nudge" : "Need a nudge?"}</span>
                  </button>
                )}
                {!done && (
                  <button type="button" data-testid="lesson-check" onClick={checkAnswer} disabled={waitingForCheck || !written} className="btn btn-primary px-4">
                    <Check size={17} strokeWidth={2.5} aria-hidden />
                    {waitingForCheck ? "Checking..." : "Check my answer"}
                  </button>
                )}
              </div>
            </div>
          </div>

          <div aria-live="polite" className="mt-2.5 flex min-h-11 flex-wrap items-center justify-between gap-2 border-t border-line pt-2.5">
            <div className="flex min-w-0 flex-1 basis-64 flex-col gap-1.5 text-[15px]">
              {done && (
                <p data-testid="lesson-correct" className="strip-success flex items-center gap-2.5 rounded-xl bg-success-soft px-3 py-2 font-semibold text-success-ink">
                  <span aria-hidden className="flex size-7 shrink-0 items-center justify-center rounded-full bg-success text-white">
                    <svg viewBox="0 0 24 24" width="16" height="16" className="tick-draw" fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
                  </span>
                  That&apos;s right, and Quack checked every line.
                </p>
              )}
              {!done && noticed && (
                <p data-testid="lesson-noticed" data-misconception={noticed.id} className="strip-in flex items-start gap-2.5 rounded-xl bg-alert-soft px-3 py-2 text-[14.5px] leading-snug text-ink">
                  <Search size={17} aria-hidden className="mt-0.5 shrink-0 text-alert-ink" />
                  <span><span className="font-semibold text-alert-ink">It looks like {noticed.label.toLowerCase()}.</span> {prettyMath(noticed.idea)}</span>
                </p>
              )}
              {checking && showChecking && (
                <p data-testid="lesson-checking" className="strip-in flex items-center gap-2.5 px-1 text-[14.5px] text-ink-soft">
                  <span aria-hidden className="checking-dots flex items-center gap-1"><span /><span /><span /></span>
                  <span>
                    {noticed ? "Quack is checking your change…" : "Quack is checking your working…"}
                    {checkingLong && <span className="text-muted"> It reads every line twice, to be sure.</span>}
                  </span>
                </p>
              )}
              {!done && unfinished === "unread" && (
                <p data-testid="lesson-unread" data-lines={unreadLines.length} className="strip-in flex items-start gap-2.5 rounded-xl bg-unsure-soft px-3 py-2 text-[14.5px] leading-snug text-ink">
                  <ScanEye size={17} aria-hidden className="mt-0.5 shrink-0 text-unsure" />
                  {unreadLines.length ? (
                    <span>
                      Quack can&apos;t be sure what {unreadLines.length === 1 ? "the marked line says" : "the marked lines say"}, so it can&apos;t say it&apos;s right yet.{" "}
                      If you changed something there, rub out the old writing and write it once.
                    </span>
                  ) : (
                    <span>Quack can&apos;t quite read some of your writing yet, so it can&apos;t say it&apos;s right. Try writing it a little more clearly.</span>
                  )}
                </p>
              )}
              {!done && unfinished === "unsure" && (
                <p data-testid="lesson-unsure" className="strip-in flex items-start gap-2.5 rounded-xl bg-unsure-soft px-3 py-2 text-[14.5px] leading-snug text-ink">
                  <PenLine size={17} aria-hidden className="mt-0.5 shrink-0 text-unsure" />
                  <span>Quack can&apos;t tell yet whether your last line is finished, so it can&apos;t say it&apos;s right. Finish it, or check it against the line above.</span>
                </p>
              )}
              {!done && unfinished === "unfinished" && (
                <p data-testid="lesson-unfinished" className="strip-in flex items-start gap-2.5 rounded-xl bg-paper-deep/70 px-3 py-2 text-[14.5px] leading-snug text-ink">
                  <PenLine size={17} aria-hidden className="mt-0.5 shrink-0 text-muted" />
                  <span>Nothing looks wrong, but Quack can&apos;t see a finished answer yet. Write your final answer on its own line.</span>
                </p>
              )}
              {nudges.map((text, index) => (
                <p key={text} data-testid="lesson-nudge-text" className="strip-in flex items-start gap-2.5 rounded-xl bg-duck-soft px-3 py-2 text-[14.5px] leading-snug"><Lightbulb size={17} aria-hidden className="mt-0.5 shrink-0 text-duck-ink" /><span><span className="sr-only">Nudge {index + 1}: </span>{prettyMath(text)}</span></p>
              ))}
              {!done && answer && <p data-testid="lesson-answer" className="strip-in rounded-xl bg-paper-deep/70 px-3 py-2 text-[14.5px] text-ink-soft">One answer is <span className="font-semibold text-ink">{prettyMath(answer)}</span>. This one won&apos;t count towards mastery, so try the next on your own.</p>}
              {problem && <p role="alert" className="rounded-xl bg-alert-soft px-3 py-2 text-[14.5px] text-alert-ink">{problem}</p>}
              {/* Why this question, until the student starts writing: the adaptive step is worth seeing, then it
                  makes way. It stays for screen readers. */}
              {quiet && !(checking && showChecking) && selection && adaptive && (
                <p data-testid="selection-reason" data-kind={selection.kind} className="strip-in flex items-start gap-2.5 rounded-xl bg-duck-soft px-3 py-2 text-[14.5px] leading-snug">
                  <Sparkles size={17} aria-hidden className="mt-0.5 shrink-0 text-duck-ink" />
                  <span>
                    {prettyMath(selection.reason)}
                    {selection.target && <span data-testid="selection-target" className="ml-1 font-semibold">Practising: {selection.target.label.toLowerCase()}.</span>}
                  </span>
                </p>
              )}
              {quiet && !(checking && showChecking) && !(selection && adaptive) && (
                <p data-testid="lesson-quiet" className="flex items-center gap-2 px-1 text-[14.5px] text-muted">
                  <PenLine size={16} aria-hidden />
                  Write your working below. Quack checks each line as you go.
                </p>
              )}
              {selection && !(quiet && !(checking && showChecking) && adaptive) && (
                <p data-testid="selection-reason" data-kind={selection.kind} className="sr-only">
                  {prettyMath(selection.reason)}
                  {selection.target && <span data-testid="selection-target"> Practising: {selection.target.label.toLowerCase()}.</span>}
                </p>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-1">
              {!done && !answer && (nudges.length > 0 || noticed) && (
                <button type="button" data-testid="lesson-reveal" onClick={() => void reveal().catch(() => {})} className="btn btn-ghost px-3 text-muted">Show the answer</button>
              )}
              <button
                type="button"
                data-testid="lesson-next"
                onClick={onNext}
                disabled={moving}
                className={done ? "btn btn-brand nudge-once px-5" : "btn btn-ghost px-3 text-muted"}
              >
                {done ? "Next question" : "Skip"}
                <ArrowRight size={17} aria-hidden />
              </button>
            </div>
          </div>
        </div>
      </section>

      <div className="relative mt-1 flex min-h-0 flex-1 flex-col">
        <NotebookCanvas store={quack.store} tool={tool} color="#262626" width={3} paper="grid" markers={markers} onDrawingChange={quack.setDrawing} />
        {showIdea && (
          <div data-testid="lesson-idea-panel" className="pop-in absolute top-3 left-3 z-10 w-[min(26rem,calc(100%-1.5rem))] rounded-3xl bg-surface/97 p-5 shadow-pop ring-1 ring-ink/10">
            <div className="mb-3 flex items-center justify-between">
              <p className="eyebrow flex items-center gap-2"><BookOpen size={16} aria-hidden />The idea</p>
              <button type="button" aria-label="Close the idea" onClick={() => setShowIdea(false)} className="-mt-2 -mr-2 flex size-11 items-center justify-center rounded-full hover:bg-ink/5"><X size={16} aria-hidden /></button>
            </div>
            <Steps content={level.lesson} />
            <p className="mt-4 rounded-xl bg-paper px-3 py-2 font-display text-[18px] ring-1 ring-line">{prettyMath(level.lesson.example.question)}: {level.lesson.example.working.map(prettyMath).join("  ")}</p>
          </div>
        )}
      </div>
      {quack.duck.issue && <p data-testid="issue" className="fade-in fixed bottom-4 left-3 z-20 max-w-[min(340px,calc(100vw-9rem))] rounded-2xl bg-surface px-3.5 py-2 text-[14px] text-alert-ink shadow-raised ring-1 ring-ink/5">{quack.duck.issue.message}</p>}
      <DuckCompanion
        duck={quack.duck}
        appearance={cheer ? "resolved" : quack.duckAppearance}
        cheer={cheer}
        canTalk={false}
        recording={quack.recording}
        transcript={quack.lastTranscript}
        onTap={quack.tapDuck}
        onSelect={quack.selectMistake}
        onHint={() => void quack.askForHelp()}
        onExplain={(asked) => void quack.explain(asked)}
        onDismiss={quack.dismiss}
        onTalkStart={() => {}}
        onTalkStop={() => {}}
      />
    </div>
  );
}

// Short and varied, so the tenth right answer doesn't read like a form letter.
const CHEERS = ["Good job!", "You got it!", "Nicely done!"];
const CHEER_MS = 2200;
// Soon enough that a pen lifted for a second sees Quack looking, late enough that a quick check doesn't flicker.
const CHECKING_SHOWN_AFTER_MS = 600;
// The median second reading on the iPad took 8 s.
const CHECKING_LONG_AFTER_MS = 7000;

// Confetti for mastering a level: a fixed set of pieces, so it looks the same each time and costs little.
const CONFETTI_COLOURS = ["#FFD95A", "#F4A340", "#2B7D5A", "#4F86C6", "#E07A5F", "#FFF1BF"];
const CONFETTI = Array.from({ length: 34 }, (_, index) => {
  const spread = (index * 37) % 100;
  return {
    left: `${spread}%`,
    colour: CONFETTI_COLOURS[index % CONFETTI_COLOURS.length],
    delay: `${((index * 53) % 60) / 100}s`,
    fall: `${1.8 + ((index * 29) % 9) / 10}s`,
    drift: `${((index * 41) % 120) - 60}px`,
    spin: `${360 + ((index * 67) % 360)}deg`,
    round: index % 4 === 0,
  };
});

function Celebration({ lesson, onNextLevel, onRoadmap, onKeepGoing }: { lesson: LessonState; onNextLevel: (() => void) | null; onRoadmap: () => void; onKeepGoing: () => void }) {
  const message = masteredMessage(lesson.mastery, lesson.levelId, Boolean(lesson.nextLevel));
  const stats: [number, string][] = [[lesson.mastery.independent, "right on your own"]];
  if (lesson.mastery.corrected) stats.push([lesson.mastery.corrected, "put right after a slip"]);
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="mastered-title" data-testid="level-mastered" className="fade-in fixed inset-0 z-50 flex items-center justify-center bg-ink/35 p-4">
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-full overflow-hidden">
        {CONFETTI.map((piece, index) => (
          <span
            key={index}
            className="confetti-piece"
            style={{ left: piece.left, background: piece.colour, borderRadius: piece.round ? 999 : 2, height: piece.round ? 9 : 14, ["--delay" as string]: piece.delay, ["--fall" as string]: piece.fall, ["--drift" as string]: piece.drift, ["--spin" as string]: piece.spin }}
          />
        ))}
      </div>
      <div className="pop-in relative flex max-h-[calc(100dvh-2rem)] w-full max-w-md flex-col overflow-hidden rounded-[var(--radius-sheet)] bg-paper text-center shadow-pop">
        <div className="relative flex h-40 shrink-0 items-end justify-center overflow-hidden bg-duck-soft sm:h-44">
          <div aria-hidden className="sunburst absolute top-1/2 left-1/2 size-[520px] -translate-x-1/2 -translate-y-1/2" />
          <div className="relative -mb-1 flex items-end">
            <Duck state="resolved" size={124} />
          </div>
        </div>
        <div className="notebook-scroll overflow-y-auto px-6 pt-4 pb-6">
          <p className="eyebrow flex items-center justify-center gap-1.5 !text-duck-ink"><Sparkles size={15} aria-hidden />Level mastered</p>
          <h2 id="mastered-title" className="mt-1 font-display text-[30px] leading-tight font-bold tracking-tight">{lesson.levelTitle}</h2>
          <p data-testid="mastered-cheer" className="mt-3 font-display text-[22px] leading-tight font-bold">{message.cheer}</p>
          <p data-testid="mastered-line" className="mx-auto mt-1.5 max-w-sm text-[16px] leading-snug text-ink-soft">{message.line}</p>
          <ul className="mt-4 flex flex-wrap justify-center gap-2">
            {stats.map(([value, label]) => (
              <li key={label} className="flex items-center gap-1.5 rounded-full bg-surface px-3 py-1.5 text-[14px] text-ink-soft ring-1 ring-line">
                <span className="font-display text-[17px] font-bold text-ink">{value}</span>
                {label}
              </li>
            ))}
          </ul>
          {lesson.nextLevel && (
            <p data-testid="unlocked" className="mt-4 flex items-center justify-center gap-2 rounded-2xl bg-duck-soft px-3 py-2.5 text-[15px] font-semibold">
              <LockOpen size={17} aria-hidden className="shrink-0 text-duck-ink" />
              Unlocked: {lesson.nextLevel.title}
            </p>
          )}
          <div className="mt-5 flex flex-col gap-2">
            {onNextLevel && (
              <button type="button" data-testid="mastered-next" onClick={onNextLevel} className="btn btn-brand btn-lg w-full">
                Next level
                <ArrowRight size={19} aria-hidden />
              </button>
            )}
            <button type="button" data-testid="mastered-roadmap" onClick={onRoadmap} className={`btn btn-lg w-full ${onNextLevel ? "btn-secondary" : "btn-brand"}`}>Back to the path</button>
            <button type="button" data-testid="mastered-keep-going" onClick={onKeepGoing} className="btn btn-ghost w-full text-[14.5px] text-muted">Keep practising here</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function RecapScreen({ recap, lesson, onRoadmap, onLevel }: { recap: LessonRecap; lesson: LessonState; onRoadmap: () => void; onLevel: (levelId: string) => void }) {
  const stats: [string, number, string][] = [
    ["On your own", recap.rightAlone, "recap-alone"],
    ["Put right", recap.corrected, "recap-corrected"],
    ["With help", recap.assisted, "recap-assisted"],
    ["Mistakes found", recap.mistakes, "recap-mistakes"],
  ];
  return (
    <main data-testid="lesson-recap" className="notebook-scroll flex-1 overflow-y-auto">
      <div className="question-in mx-auto flex w-full max-w-lg flex-col gap-4 px-4 py-8 text-center">
        <div className="flex justify-center"><span className="flex size-28 items-center justify-center rounded-full bg-duck-soft"><Duck state={recap.status === "mastered" ? "resolved" : "idle"} size={84} /></span></div>
        <p className="eyebrow">Quack recap</p>
        <h1 className="-mt-2 font-display text-3xl font-bold tracking-tight">{recap.levelTitle}</h1>
        <p data-testid="recap-message" className="text-[16px] leading-snug text-ink-soft">{recap.message}</p>
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {stats.map(([label, value, id]) => (
            <div key={id} className="card flex flex-col-reverse p-3">
              <dt className="text-[13.5px] text-muted">{label}</dt>
              <dd data-testid={id} className="font-display text-[28px] leading-tight font-bold">{value}</dd>
            </div>
          ))}
        </dl>
        {recap.misconceptions.length > 0 && (
          <section className="card p-4 text-left">
            <h2 className="eyebrow">What came up</h2>
            <ul className="mt-2 space-y-1.5 text-[15px]">
              {recap.misconceptions.map((item) => (
                <li key={item.label} data-testid="recap-misconception" className="flex items-center justify-between gap-2">
                  <span>{item.label}</span>
                  <span className={`text-[13px] font-semibold ${item.cleared ? "text-success" : "text-muted"}`}>{item.cleared ? "Practised since" : "Worth another go"}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
        {recap.status !== "mastered" && (
          <ul className="mx-auto space-y-1 text-left text-[14px]">
            {masteryChecklist(recap.mastery).map((item) => (
              <li key={item.label} className={`flex items-center gap-2 ${item.met ? "" : "text-muted"}`}>{item.met ? <Check size={14} aria-hidden className="text-success" /> : <span aria-hidden className="size-3.5 rounded-full ring-1 ring-ink/20" />}{item.label}</li>
            ))}
          </ul>
        )}
        <div className="mt-2 flex flex-col gap-2">
          {recap.status === "mastered" && lesson.nextLevel && (
            <button type="button" onClick={() => onLevel(lesson.nextLevel!.id)} className="btn btn-brand btn-lg w-full">Next: {lesson.nextLevel.title}<ArrowRight size={18} aria-hidden /></button>
          )}
          <button type="button" data-testid="recap-roadmap" onClick={onRoadmap} className="btn btn-secondary btn-lg w-full">Back to the path</button>
        </div>
      </div>
    </main>
  );
}
