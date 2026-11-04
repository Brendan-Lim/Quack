"use client";

import { X } from "lucide-react";
import { useEffect, useRef, type FormEvent } from "react";
import type { AssistanceEntry } from "@/lib/duckMachine";
import { prettyMath } from "@/lib/mistakes";

type Props = {
  entries: AssistanceEntry[];
  // Which written step this help is about, as there can be several on the page.
  step: string;
  // The line has changed since this help was given. Says what's happening about that, if so.
  pending: string | null;
  loading: boolean;
  canHint: boolean;
  explained: boolean;
  question: string;
  onQuestionChange: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
  onHint: () => void;
  onExplain: () => void;
  onDismiss: () => void;
  transcript: string | null;
};

export function HintPanel(props: Props) {
  const { entries, step, pending, loading, canHint, explained, question, onQuestionChange, onSubmit, onHint, onExplain, onDismiss, transcript } = props;
  const panel = useRef<HTMLElement>(null);
  const list = useRef<HTMLOListElement>(null);

  // The button that opened this has gone, so keyboard users need somewhere to land.
  useEffect(() => {
    panel.current?.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    list.current?.scrollTo({ top: list.current.scrollHeight });
  }, [entries.length]);

  return (
    <section
      ref={panel}
      tabIndex={-1}
      data-testid="hint-panel"
      aria-label="Help with this step"
      className="bubble pointer-events-auto relative mr-2 flex max-h-[min(62dvh,520px)] w-full flex-col rounded-3xl bg-surface p-4 pr-3 shadow-pop ring-1 ring-ink/5 outline-none"
    >
      <button type="button" data-testid="panel-close" onClick={onDismiss} aria-label="Dismiss" className="absolute top-1 right-1 flex size-11 items-center justify-center rounded-full text-muted transition-colors hover:bg-ink/5">
        <X size={16} aria-hidden />
      </button>

      <div ref={list as unknown as React.RefObject<HTMLDivElement>} className="min-h-0 flex-1 overflow-y-auto pr-7">
        {pending && <p data-testid="pending" className="mb-2 text-[15px] leading-snug font-semibold text-ink">{pending}</p>}
        <p data-testid="step-label" className="mb-2.5 inline-block max-w-full rounded-lg bg-paper-deep/70 px-2 py-1 font-display text-[14px] font-semibold text-ink-soft">{pending ? `Before your change: ${step}` : step}</p>
        {transcript && <p className="mb-2 text-[13px] text-muted">You said: {transcript}</p>}

        <ol className="flex flex-col gap-2.5 text-[15.5px] leading-snug text-ink">
          {entries.map((entry, index) => (
            <li
              key={index}
              data-testid={`help-level-${entry.level}`}
              data-source={entry.source}
              className={`strip-in flex gap-2 ${pending || index < entries.length - 1 ? "text-muted" : ""}`}
            >
              <span aria-hidden className={`mt-[7px] size-2 shrink-0 rounded-full ${pending || index < entries.length - 1 ? "bg-ink/15" : "bg-duck-deep"}`} />
              <span>{prettyMath(entry.text)}</span>
            </li>
          ))}
        </ol>
      </div>

      {!explained && !pending && (
        <div className="mt-3 flex shrink-0 flex-wrap gap-1.5 text-[13px] font-medium">
          {canHint && (
            <button data-testid="another-hint" type="button" onClick={onHint} disabled={loading} className="btn btn-brand rounded-full px-4 text-[14px]">
              Another hint
            </button>
          )}
          <button data-testid="explain" type="button" onClick={onExplain} disabled={loading} className="btn btn-secondary rounded-full px-4 text-[14px]">
            Explain
          </button>
        </div>
      )}

      {!pending && <form onSubmit={onSubmit} className="mt-3 shrink-0 pr-1">
        <input
          data-testid="question"
          value={question}
          onChange={(event) => onQuestionChange(event.target.value)}
          maxLength={300}
          placeholder="Ask about this step"
          aria-label="Ask about this step"
          className="min-h-11 w-full rounded-lg bg-ink/[0.04] px-3 text-base text-ink placeholder:text-muted focus:bg-ink/[0.06] focus:outline-none"
        />
      </form>}

      {loading && <p className="mt-2 shrink-0 text-[13px] text-muted">Thinking…</p>}
    </section>
  );
}
