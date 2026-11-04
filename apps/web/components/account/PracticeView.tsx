"use client";

import { Check, CircleCheck, Eraser, Pen, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { checkPracticeAnswer, finishPractice, nextProblem, revealAnswer, startPractice } from "@/lib/account";
import { setStudySession } from "@/lib/api";
import { prettyMath } from "@/lib/mistakes";
import type { LearningQueue } from "@/lib/learning";
import { useQuack, type QuackOptions } from "@/hooks/useQuack";
import type { AnalyzeResponse, PracticeCheckResponse, PracticeProblem, PracticeSummary, PracticeTopic } from "@/lib/types";
import { DuckCompanion } from "../duck/DuckCompanion";
import { NotebookCanvas, type Tool } from "../notebook/NotebookCanvas";
import { toolClass } from "@/lib/toolClass";

type Props = {
  topic: PracticeTopic | null;
  origin: string | null;
  // The entry in My Mistakes this was started from.
  source?: string | null;
  queue: LearningQueue;
  onClose: () => void;
};

export function PracticeView({ topic, origin, source = null, queue, onClose }: Props) {
  const [problem, setProblem] = useState<PracticeProblem | null>(null);
  const [summary, setSummary] = useState<PracticeSummary | null>(null);
  const [startProblem, setStartProblem] = useState<string | null>(null);
  const [asked, setAsked] = useState(1);
  const started = useRef(false);

  useEffect(() => {
    // Once, even though development runs effects twice. A second start would be a second session.
    if (started.current) return;
    started.current = true;
    startPractice(topic, origin, source).then(setProblem, () => setStartProblem("Quack can't set a question right now. Check the connection and try again."));
  }, [topic, origin, source]);

  useEffect(() => {
    if (!problem) return;
    // Hints asked for here belong to this practice, not to the notebook's session.
    setStudySession(() => problem.sessionId);
  }, [problem]);

  const next = useCallback(async () => {
    if (!problem) return;
    setProblem(await nextProblem(problem.sessionId));
    setAsked((count) => count + 1);
  }, [problem]);

  const end = useCallback(async () => {
    if (!problem) return onClose();
    await queue.flush();
    setSummary(await finishPractice(problem.sessionId));
  }, [problem, queue, onClose]);

  return (
    <div data-testid="practice" className="fixed inset-0 z-40 flex flex-col bg-paper">
      {summary ? (
        <main className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center gap-4 p-6 text-center">
          <h1 className="font-display text-2xl font-bold">{summary.topicLabel}</h1>
          <dl className="grid grid-cols-3 gap-2 text-center">
            {[
              ["Attempted", summary.attempted, "summary-attempted"],
              ["Right", summary.correct, "summary-correct"],
              ["Hints", summary.hints, "summary-hints"],
            ].map(([label, value, id]) => (
              <div key={id as string} className="rounded-2xl bg-white p-3 ring-1 ring-ink/5">
                <dd data-testid={id as string} className="font-display text-2xl font-bold">{value}</dd>
                <dt className="text-[13px] text-muted">{label}</dt>
              </div>
            ))}
          </dl>
          <p data-testid="summary-message" className="text-[15px] leading-snug">{summary.message}</p>
          <button type="button" data-testid="practice-close" onClick={onClose} className="min-h-11 self-center rounded-xl bg-ink px-5 font-semibold text-paper hover:opacity-85">Back to my notebook</button>
        </main>
      ) : problem ? (
        <Sheet key={problem.id} problem={problem} queue={queue} asked={asked} onNext={() => void next().catch(() => {})} onEnd={() => void end().catch(() => onClose())} />
      ) : (
        <main className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
          <p data-testid="practice-loading" className="text-[15px] text-muted">{startProblem ?? "Finding a question..."}</p>
          {startProblem && <button type="button" onClick={onClose} className="min-h-11 rounded-xl bg-ink px-4 font-semibold text-paper">Back to my notebook</button>}
        </main>
      )}
    </div>
  );
}

// One question on its own page. This is the same notebook and duck, and nothing written here is saved
// or goes near the notes.
function Sheet({ problem, queue, asked, onNext, onEnd }: { problem: PracticeProblem; queue: LearningQueue; asked: number; onNext: () => void; onEnd: () => void }) {
  const [done, setDone] = useState(false);
  const [answer, setAnswer] = useState<string | null>(null);
  const [tool, setTool] = useState<Tool>("pen");

  const options = useMemo<QuackOptions>(
    () => ({
      persist: false,
      sessionId: problem.checkSessionId,
      analyzeWith: checkPracticeAnswer(problem.id) as QuackOptions["analyzeWith"],
      readAllLines: true,
      alwaysWatching: true,
      learning: queue.forSession(problem.sessionId),
      onApplied: (result: AnalyzeResponse) => {
        if ((result as PracticeCheckResponse).outcome === "correct") setDone(true);
      },
    }),
    [problem, queue],
  );
  const quack = useQuack(options);

  const reveal = async () => {
    const { expectedAnswer } = await revealAnswer(problem.id);
    setAnswer(expectedAnswer);
  };

  return (
    <>
      <header className="question-in m-2.5 card flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 px-3.5 pt-3 pb-2.5 sm:m-4 sm:mb-1 sm:px-5">
        <div className="min-w-0 flex-1">
          <p className="text-[13.5px] text-muted"><span className="font-semibold text-ink-soft">{problem.topicLabel}</span>, question {asked}. Write your answer below.</p>
          <p data-testid="practice-prompt" data-difficulty={problem.difficulty} className="mt-0.5 font-display text-[26px] leading-tight font-bold tracking-tight sm:text-[32px]">{problem.prompt}</p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <div role="toolbar" aria-label="Writing tools" className="flex items-center gap-0.5 rounded-2xl bg-paper-deep/70 p-1">
            <button type="button" aria-label="Pen" aria-pressed={tool === "pen"} onClick={() => setTool("pen")} className={toolClass(tool === "pen")}><Pen size={19} aria-hidden /></button>
            <button type="button" aria-label="Eraser" aria-pressed={tool === "eraser"} onClick={() => setTool("eraser")} className={toolClass(tool === "eraser")}><Eraser size={19} aria-hidden /></button>
            <button type="button" data-testid="practice-clear" aria-label="Clear" title="Clear" onClick={quack.store.clear} className={toolClass(false)}><RotateCcw size={19} aria-hidden /></button>
          </div>
          <button type="button" data-testid="practice-check" onClick={quack.checkNow} className="btn btn-primary"><Check size={17} strokeWidth={2.5} aria-hidden />Check</button>
        </div>
        <div className="flex w-full flex-wrap items-center justify-between gap-2 border-t border-line pt-2.5">
          <div aria-live="polite" className="flex min-h-11 flex-1 basis-56 flex-col justify-center gap-1.5 text-[15px]">
            {done && (
              <p data-testid="practice-correct" className="strip-success flex items-center gap-2.5 rounded-xl bg-success-soft px-3 py-2 font-semibold text-success-ink"><CircleCheck size={19} aria-hidden />That&apos;s right.</p>
            )}
            {!done && answer && <p data-testid="practice-answer" className="strip-in rounded-xl bg-paper-deep/70 px-3 py-2 text-ink-soft">One answer is <span className="font-semibold text-ink">{prettyMath(answer)}</span>.</p>}
            {!done && !answer && quack.checking && (
              <p data-testid="practice-checking" className="strip-in flex items-center gap-2.5 px-1 text-[14.5px] text-ink-soft">
                <span aria-hidden className="checking-dots flex items-center gap-1"><span /><span /><span /></span>
                Quack is checking your working…
              </p>
            )}
          </div>
          <div className="flex flex-wrap gap-1">
            {!done && !answer && <button type="button" data-testid="practice-reveal" onClick={() => void reveal().catch(() => {})} className="btn btn-ghost px-3 text-muted">Show the answer</button>}
            <button type="button" data-testid="practice-end" onClick={onEnd} className="btn btn-ghost px-3">End practice</button>
            <button type="button" data-testid="practice-next" onClick={onNext} className={done ? "btn btn-brand nudge-once px-5" : "btn btn-secondary px-4"}>Next question</button>
          </div>
        </div>
      </header>
      <NotebookCanvas store={quack.store} tool={tool} color="#262626" width={3} paper="grid" markers={quack.markers} onDrawingChange={quack.setDrawing} />
      {quack.duck.issue && <p data-testid="issue" className="fixed bottom-4 left-3 z-20 max-w-[min(340px,calc(100vw-9rem))] rounded-2xl bg-white/95 px-3 py-1.5 text-[13px] text-alert shadow-sm ring-1 ring-ink/5">{quack.duck.issue.message}</p>}
      <DuckCompanion
        duck={quack.duck}
        appearance={quack.duckAppearance}
        canTalk={false}
        recording={quack.recording}
        transcript={quack.lastTranscript}
        onTap={quack.tapDuck}
        onSelect={quack.selectMistake}
        onHint={() => void quack.askForHelp()}
        onExplain={(question) => void quack.explain(question)}
        onDismiss={quack.dismiss}
        onTalkStart={() => {}}
        onTalkStop={() => {}}
      />
    </>
  );
}
