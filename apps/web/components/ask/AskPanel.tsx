"use client";

import { Check, Lightbulb, MessageCircleQuestion, ScrollText, X } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { askMenu, placePanel, type AskMenuItem } from "@/lib/lasso";
import { prettyMath } from "@/lib/mistakes";
import type { AskSelection } from "@/hooks/useQuack";
import type { AskAction, AskResponse, LineIntent, StepExplanation } from "@/lib/types";

const PANEL_WIDTH = 320;
// With a reply, the buttons and a question open.
const PANEL_HEIGHT = 350;
const GUTTER = 12;

type Props = {
  selection: AskSelection;
  onAsk: (action: AskAction, question?: string) => void;
  onClarify: (lineId: string, intent: LineIntent) => void;
  onCancel: () => void;
};

const ICONS: Record<AskMenuItem["action"], typeof Check> = { hint: Lightbulb, explain: ScrollText, check: Check, question: MessageCircleQuestion, open_question: MessageCircleQuestion };
const ACTION = "flex min-h-11 items-center gap-2 rounded-xl px-3 text-left text-[14px] font-medium ring-1 ring-ink/10 transition-colors hover:bg-duck/30 disabled:opacity-50";

// What's circled stays the student's own question. This is a small note beside it, and goes when it's done.
export function AskPanel({ selection, onAsk, onClarify, onCancel }: Props) {
  const [asking, setAsking] = useState(false);
  const [question, setQuestion] = useState("");
  const { reply, loading, tooMuch, problem } = selection;
  // A turned iPad has a different screen to stay on.
  const [, setResized] = useState(0);
  useEffect(() => {
    const resized = () => setResized((count) => count + 1);
    window.addEventListener("resize", resized);
    return () => window.removeEventListener("resize", resized);
  }, []);

  const viewport = typeof window === "undefined" ? { width: 1024, height: 768 } : { width: window.innerWidth, height: window.innerHeight };
  const width = Math.min(PANEL_WIDTH, viewport.width - 24);
  const place = placePanel(selection.screen, viewport, { width, height: PANEL_HEIGHT });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const text = question.trim();
    if (!text || loading) return;
    setQuestion("");
    onAsk("question", text);
  };

  const menu = askMenu(selection).filter((item) => !(asking && item.action === "open_question"));

  return (
    <aside
      role="dialog"
      aria-label="Ask Quack about what you circled"
      data-testid="ask-panel"
      style={{ left: place.left, top: place.top, width, maxHeight: `calc(100dvh - ${place.top + GUTTER}px)` }}
      className="bubble fixed z-30 flex flex-col gap-2 overflow-y-auto rounded-2xl bg-white p-3 shadow-[0_6px_24px_rgba(38,38,38,0.16)] ring-1 ring-ink/10"
    >
      <div className="flex items-center justify-between gap-2">
        <p className="font-display text-[15px] font-bold">{tooMuch ? "That's a lot of working" : "What would you like to know?"}</p>
        <button type="button" data-testid="ask-cancel" aria-label="Close, and keep writing" onClick={onCancel} className="-m-1 flex size-11 shrink-0 items-center justify-center rounded-full text-muted hover:bg-ink/5">
          <X size={16} aria-hidden />
        </button>
      </div>

      {tooMuch && <p data-testid="ask-too-much" className="text-[14px] leading-snug text-muted">Try circling just a few lines, so that I can look at them properly.</p>}

      <div role="status" aria-live="polite">
        {loading && <p data-testid="ask-loading" className="text-[14px] text-muted">Thinking…</p>}
        {problem && <p data-testid="ask-problem" className="text-[14px] leading-snug text-alert">{problem}</p>}
        {reply && !loading && <Reply reply={reply} />}
      </div>

      {reply?.clarification && !loading && (
        <div className="flex gap-1.5 text-[14px] font-medium">
          <button type="button" data-testid="ask-clarify-simplify" onClick={() => onClarify(reply.clarification!.lineId, "simplify")} className="min-h-11 flex-1 rounded-xl bg-duck/50 px-3 hover:bg-duck">Simplifying</button>
          <button type="button" data-testid="ask-clarify-solve" onClick={() => onClarify(reply.clarification!.lineId, "solve")} className="min-h-11 flex-1 rounded-xl bg-duck/50 px-3 hover:bg-duck">Solving</button>
        </div>
      )}

      {menu.length > 0 && (
        <div className="grid grid-cols-2 gap-1.5">
          {menu.map((item, index) => {
            const Icon = ICONS[item.action];
            return (
              <button
                key={item.testId}
                type="button"
                autoFocus={index === 0}
                data-testid={item.testId}
                disabled={loading}
                onClick={() => (item.action === "open_question" ? setAsking(true) : onAsk(item.action))}
                className={`${ACTION} ${menu.length % 2 && index === menu.length - 1 ? "col-span-2" : ""}`}
              >
                <Icon size={16} aria-hidden className="shrink-0" />{item.label}
              </button>
            );
          })}
        </div>
      )}

      {asking && !tooMuch && (
        <form onSubmit={submit} className="flex gap-1.5">
          <input
            data-testid="ask-input"
            autoFocus
            value={question}
            maxLength={300}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Ask about this working"
            aria-label="Your question about this working"
            className="min-h-11 min-w-0 flex-1 rounded-xl px-3 text-[14px] ring-1 ring-ink/15 outline-none focus-visible:ring-2 focus-visible:ring-duck-deep"
          />
          <button type="submit" data-testid="ask-send" disabled={loading || !question.trim()} className="min-h-11 shrink-0 rounded-xl bg-ink px-3 text-[14px] font-semibold text-paper disabled:opacity-50">Ask</button>
        </form>
      )}
    </aside>
  );
}

function Reply({ reply }: { reply: AskResponse }) {
  if (!reply.explanation) return <p data-testid="ask-reply" data-verdict={reply.verdict} className="text-[15px] leading-snug">{reply.text}</p>;
  return (
    <div data-testid="ask-reply" data-verdict={reply.verdict}>
      <Steps explanation={reply.explanation} />
    </div>
  );
}

// A few short numbered steps, each one idea, so a rule can be taken in at a glance in a small panel.
function Steps({ explanation }: { explanation: StepExplanation }) {
  return (
    <div data-testid="ask-explanation" className="flex flex-col gap-2">
      <p className="text-[14px] font-semibold">{explanation.title}</p>
      {/* Safari drops a list's meaning once its markers are hidden, unless it's said again. */}
      <ol role="list" className="flex flex-col gap-1.5">
        {explanation.steps.map((step, index) => (
          <li key={index} data-testid="ask-step" className="flex gap-2 text-[14px] leading-snug">
            <span aria-hidden className="mt-px flex size-5 shrink-0 items-center justify-center rounded-full bg-duck/50 text-[12px] font-bold">{index + 1}</span>
            <span>{prettyMath(step)}</span>
          </li>
        ))}
      </ol>
      {explanation.takeaway && <p data-testid="ask-takeaway" className="rounded-xl bg-paper px-2.5 py-1.5 text-[14px] leading-snug">{prettyMath(explanation.takeaway)}</p>}
    </div>
  );
}
