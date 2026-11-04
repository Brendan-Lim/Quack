"use client";

import { Mic, X } from "lucide-react";
import { useState, type FormEvent, type KeyboardEvent } from "react";
import { activeMistakes, CHECKING_CHANGE_TEXT, isAlertShowing, nextHelpLevel, UNDECIDED_TEXT, type DuckModel } from "@/lib/duckMachine";
import { prettyMath, stepLabel } from "@/lib/mistakes";
import type { DuckState, LineIntent } from "@/lib/types";
import { Duck } from "./Duck";
import { HintPanel } from "../hints/HintPanel";

type Props = {
  duck: DuckModel;
  appearance: DuckState;
  canTalk: boolean;
  recording: boolean;
  transcript: string | null;
  onTap: () => void;
  onSelect: (id: string) => void;
  onHint: () => void;
  onExplain: (question?: string) => void;
  onDismiss: () => void;
  onTalkStart: () => void;
  onTalkStop: () => void;
  onClarify?: (lineId: string, intent: LineIntent) => void;
  onDismissClarification?: () => void;
  // Praise for a question just answered right, checked line by line. The duck jumps while it shows.
  cheer?: string | null;
};

const SPARKS: [number, string][] = [
  [0, "#FFD95A"],
  [45, "#F4A340"],
  [90, "#2B7D5A"],
  [135, "#FFD95A"],
  [180, "#F4A340"],
  [225, "#4F86C6"],
  [270, "#FFD95A"],
  [315, "#2B7D5A"],
];

function duckLabel(duck: DuckModel): string {
  const open = activeMistakes(duck).length;
  if (open > 1) return `Quack found ${open} steps to re-check. Tap to choose one.`;
  if (duck.mistake && duck.suspended) return "Quack is looking at the line you changed. Tap to check it now.";
  if (duck.mistake && duck.assistance.length === 0) return "Quack found something to re-check. Tap for a hint.";
  if (duck.mistake) return "Quack, showing help for this step";
  return "Quack. Tap to check your work.";
}

export function DuckCompanion(props: Props) {
  const { duck, appearance, canTalk, recording, transcript, onTap, onSelect, onHint, onExplain, onDismiss, onTalkStart, onTalkStop, onClarify, onDismissClarification, cheer } = props;
  const [question, setQuestion] = useState("");
  // Not while idle: a mistake that came forward on its own hasn't been opened yet.
  const showPanel = duck.assistance.length > 0 && !duck.dismissed && duck.mistake !== null && duck.state !== "idle" && duck.state !== "checking";
  const isAlert = isAlertShowing(duck);
  // A cheer goes above what the duck is saying: "That's it." for a fix still says the fix was seen.
  const bubble = showPanel ? null : duck.bubble;
  const level = nextHelpLevel(duck);
  const pendingText = !duck.suspended ? null : duck.undecided ? UNDECIDED_TEXT : CHECKING_CHANGE_TEXT;
  const latestHelp = showPanel ? (pendingText ?? duck.assistance.at(-1)?.text) : null;
  const open = activeMistakes(duck);
  const showChoices = open.length > 1 && (showPanel || isAlert || duck.choosing);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const text = question.trim();
    if (!text || duck.loadingHelp) return;
    setQuestion("");
    onExplain(text);
  };

  const onTalkKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== " " && event.key !== "Enter") return;
    event.preventDefault();
    if (event.type === "keydown" && !event.repeat) onTalkStart();
    if (event.type === "keyup") onTalkStop();
  };

  return (
    <div className="pointer-events-none fixed right-3 bottom-3 z-20 flex max-w-[min(360px,calc(100vw-1.5rem))] flex-col items-end gap-1 sm:right-5 sm:bottom-4">
      {/* Always mounted, because screen readers only announce changes to a live region they
          already know about. */}
      <div role="status" aria-live="polite" className="sr-only" data-testid="announcer">
        {cheer ?? latestHelp ?? bubble ?? ""}
      </div>

      {cheer && (
        <div data-testid="cheer" aria-hidden className="bubble pointer-events-none mr-14 rounded-2xl rounded-br-md bg-duck px-4 py-2 font-display text-[22px] font-bold text-ink shadow-[0_3px_0_var(--color-duck-edge),0_10px_28px_rgba(38,38,38,0.16)]">
          {cheer}
        </div>
      )}

      {showChoices && (
        <nav aria-label="Steps to re-check" data-testid="mistake-choices" className="bubble pointer-events-auto mr-2 flex max-w-full flex-wrap justify-end gap-1.5">
          {open.map(({ mistake, suspended }) => {
            const selected = mistake.id === duck.mistake?.id && !duck.dismissed;
            return (
              <button
                key={mistake.id}
                type="button"
                data-testid="mistake-choice"
                aria-current={selected ? "true" : undefined}
                onClick={() => onSelect(mistake.id)}
                className={`min-h-11 max-w-full truncate rounded-full px-3.5 text-[13px] font-medium shadow-sm ring-1 ring-ink/5 transition-colors ${
                  selected ? "bg-duck text-ink" : "bg-white text-ink hover:bg-duck/30"
                } ${suspended ? "border border-dashed border-duck-deep" : ""}`}
              >
                {/* It no longer says this, so the old maths isn't offered as what's there. */}
                {suspended ? "Your change" : stepLabel(mistake)}
              </button>
            );
          })}
        </nav>
      )}

      {showPanel && (
        <HintPanel
          step={stepLabel(duck.mistake!)}
          pending={pendingText}
          entries={duck.assistance}
          loading={duck.loadingHelp}
          canHint={level < 3}
          explained={duck.assistance.some((entry) => entry.level === 3)}
          question={question}
          onQuestionChange={setQuestion}
          onSubmit={submit}
          onHint={onHint}
          onExplain={() => onExplain()}
          onDismiss={onDismiss}
          transcript={transcript}
        />
      )}

      {duck.clarification && onClarify && !showPanel && !isAlert && (
        <div data-testid="clarify" role="group" aria-label={duck.clarification.question} className="bubble pointer-events-auto mr-14 flex max-w-full flex-col gap-1.5 rounded-2xl rounded-br-md bg-white px-4 py-2.5 shadow-[0_6px_24px_rgba(38,38,38,0.12)] ring-1 ring-ink/5">
          <span className="text-[15px] leading-snug">{prettyMath(duck.clarification.step)}. {duck.clarification.question}</span>
          <span className="flex items-center gap-1 text-[14px] font-medium">
            <button type="button" data-testid="clarify-simplify" onClick={() => onClarify(duck.clarification!.lineId, "simplify")} className="min-h-11 rounded-full bg-duck/50 px-4 transition-colors hover:bg-duck">Simplifying</button>
            <button type="button" data-testid="clarify-solve" onClick={() => onClarify(duck.clarification!.lineId, "solve")} className="min-h-11 rounded-full bg-duck/50 px-4 transition-colors hover:bg-duck">Solving</button>
            <button type="button" data-testid="clarify-dismiss" aria-label="Leave it" onClick={onDismissClarification} className="flex size-11 items-center justify-center rounded-full text-muted transition-colors hover:bg-ink/5"><X size={16} aria-hidden /></button>
          </span>
        </div>
      )}

      {bubble && (
        <div
          data-testid="bubble"
          key={bubble}
          className={`bubble pointer-events-auto mr-14 rounded-2xl rounded-br-md px-4 py-2.5 text-ink shadow-[0_10px_28px_rgba(38,38,38,0.14)] ${
            isAlert ? "bg-surface font-display text-[22px] font-bold ring-2 ring-alert/25" : "bg-surface text-[15px] leading-snug ring-1 ring-ink/5"
          }`}
        >
          <span className={isAlert ? "text-alert-ink" : undefined}>{bubble}</span>
          {isAlert && (
            <span className="mt-1.5 flex items-center gap-1 font-sans text-[14px] font-medium">
              <button data-testid="hint-button" type="button" onClick={onTap} disabled={duck.loadingHelp} className="btn btn-brand min-h-11 rounded-full px-5 text-[14.5px]">
                {duck.loadingHelp ? "Thinking…" : "Hint"}
              </button>
              <button data-testid="dismiss-button" type="button" onClick={onDismiss} aria-label="Dismiss" className="flex size-11 items-center justify-center rounded-full text-muted transition-colors hover:bg-ink/5">
                <X size={16} aria-hidden />
              </button>
            </span>
          )}
        </div>
      )}

      <div className="pointer-events-auto flex items-end gap-1">
        {canTalk && (
          <button
            type="button"
            aria-label={recording ? "Recording. Release to send." : "Hold to talk to Quack"}
            aria-pressed={recording}
            title="Hold to talk"
            data-testid="talk-button"
            onPointerDown={(event) => {
              event.currentTarget.setPointerCapture(event.pointerId);
              onTalkStart();
            }}
            onPointerUp={onTalkStop}
            onPointerCancel={onTalkStop}
            onBlur={onTalkStop}
            onKeyDown={onTalkKey}
            onKeyUp={onTalkKey}
            className={`mb-2 flex size-11 touch-none items-center justify-center rounded-full shadow-sm ring-1 ring-ink/5 transition-colors ${
              recording ? "bg-alert text-white" : "bg-white/90 text-muted hover:text-ink"
            }`}
          >
            <Mic size={18} aria-hidden />
          </button>
        )}
        <button
          type="button"
          data-testid="duck"
          data-duck-state={appearance}
          data-cheering={cheer ? "true" : undefined}
          aria-label={duckLabel(duck)}
          onClick={onTap}
          className="relative rounded-full outline-offset-2 focus-visible:outline-2 focus-visible:outline-duck-deep"
        >
          <Duck state={appearance} />
          {/* A verified right answer: a small burst of sparks, once, around the duck. Never on the page. */}
          {cheer && (
            <span aria-hidden className="spark-burst">
              {SPARKS.map(([angle, colour]) => (
                <i key={angle} style={{ ["--angle" as string]: `${angle}deg`, ["--spark" as string]: colour }} />
              ))}
            </span>
          )}
          {open.length > 0 && (
            <span data-testid="mistake-count" aria-hidden className="absolute -top-0.5 -left-0.5 flex size-6 items-center justify-center rounded-full bg-alert text-[12px] font-bold text-white ring-2 ring-white">
              {open.length}
            </span>
          )}
          {/* A mistake on hold is neither fixed nor being reported. This says it isn't forgotten. */}
          {duck.mistake && duck.suspended && !showPanel && (
            <span data-testid="held" aria-hidden className="absolute top-1 right-1 size-3 rounded-full bg-duck-deep ring-2 ring-white" />
          )}
        </button>
      </div>
    </div>
  );
}
