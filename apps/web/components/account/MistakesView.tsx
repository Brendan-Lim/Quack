"use client";

import { ArrowLeft, CircleCheck, CircleDot, Dumbbell, MapPin, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { cachedMistakes, cacheMistakes, deleteMistake, fetchMistake, fetchMistakes } from "@/lib/account";
import { strokePath } from "@/lib/ink";
import { boundsOfStrokes } from "@/lib/geometry";
import { prettyMath } from "@/lib/mistakes";
import type { MistakeEntry, MistakeEntryDetail, MistakeStatus, PenStroke, PracticeTopic } from "@/lib/types";
import { Duck } from "../duck/Duck";

type Props = {
  userId: string;
  // A recap's own mistakes, when that's where this was opened from.
  sessionId: string | null;
  ready: () => Promise<void>;
  inkOf: (mistakeId: string) => PenStroke[];
  onShowOnPage: (mistakeId: string) => void;
  onPractice: (topic: PracticeTopic, mistakeId: string) => void;
  // Open the lesson a mistake was made in, to practise that skill.
  onLearn?: (courseId: string, levelId: string) => void;
  onBack: () => void;
};

const when = (seconds: number) => new Date(seconds * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

function standing(entry: MistakeEntry): string {
  const STANDING: Record<MistakeStatus, string> = {
    open: "Still open",
    corrected: entry.independent ? "Corrected on your own" : "Corrected with a little help",
    erased: "Erased before it was worked through",
    tried_again: "You had another go at this one",
  };
  return STANDING[entry.status];
}

type Filter = "all" | "open" | "corrected";
const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "open", label: "Still open" },
  { value: "corrected", label: "Corrected" },
];

function StatusIcon({ entry }: { entry: MistakeEntry }) {
  return entry.status === "corrected" ? <CircleCheck size={15} aria-hidden className="shrink-0 text-success" /> : <CircleDot size={15} aria-hidden className={`shrink-0 ${entry.status === "open" ? "text-alert" : "text-muted"}`} />;
}

// The student's own handwriting, drawn from the notes on this device. It isn't stored anywhere else.
function InkPreview({ strokes }: { strokes: PenStroke[] }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const element = canvas.current;
    const bounds = boundsOfStrokes(strokes);
    if (!element || !bounds) return;
    const dpr = window.devicePixelRatio || 1;
    const pad = 6;
    const width = bounds.maxX - bounds.minX + pad * 2;
    const height = bounds.maxY - bounds.minY + pad * 2;
    const scale = Math.min(1, 300 / width, 80 / height);
    element.width = Math.ceil(width * scale * dpr);
    element.height = Math.ceil(height * scale * dpr);
    element.style.width = `${Math.ceil(width * scale)}px`;
    element.style.height = `${Math.ceil(height * scale)}px`;
    const context = element.getContext("2d");
    if (!context) return;
    context.setTransform(scale * dpr, 0, 0, scale * dpr, (pad - bounds.minX) * scale * dpr, (pad - bounds.minY) * scale * dpr);
    for (const stroke of strokes) {
      context.fillStyle = stroke.color;
      context.fill(strokePath(stroke));
    }
  }, [strokes]);
  return <canvas ref={canvas} data-testid="mistake-ink" aria-label="Your handwriting" role="img" className="max-w-full rounded-lg bg-paper" />;
}

function Detail({ entry, inkOf, onShowOnPage, onPractice, onLearn, onBack, onDeleted }: { entry: MistakeEntry; inkOf: Props["inkOf"]; onShowOnPage: Props["onShowOnPage"]; onPractice: Props["onPractice"]; onLearn?: Props["onLearn"]; onBack: () => void; onDeleted: (id: string) => void }) {
  const [full, setFull] = useState<MistakeEntryDetail | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [revealed, setRevealed] = useState(entry.status === "corrected");
  const [confirming, setConfirming] = useState(false);
  const ink = useMemo(() => inkOf(entry.id), [entry.id, inkOf]);

  useEffect(() => {
    fetchMistake(entry.id).then(setFull, () => setUnavailable(true));
  }, [entry.id]);

  return (
    <div data-testid="mistake-detail" className="flex flex-col gap-4">
      <button type="button" data-testid="mistake-back" onClick={onBack} className="flex min-h-11 items-center gap-1.5 self-start rounded-xl px-2 text-[14px] font-medium hover:bg-ink/5">
        <ArrowLeft size={16} aria-hidden />All my mistakes
      </button>
      <header>
        <p className="text-[13px] text-muted">{when(entry.lastSeenAt)}, {entry.topicGroup}</p>
        <h2 className="font-display text-xl font-bold">{entry.topicLabel}</h2>
        {entry.kindLabel !== entry.topicLabel && <p className="text-[14px] text-muted">{entry.kindLabel}</p>}
        {entry.lesson && (
          <p data-testid="mistake-lesson" className="mt-1 text-[14px]">
            In a lesson: {entry.lesson.levelTitle} ({entry.lesson.skillLabel})
            {entry.lesson.misconception && <span className="block text-muted">What it showed: {entry.lesson.misconception.toLowerCase()}</span>}
          </p>
        )}
      </header>

      <section className="card flex flex-col gap-2 p-5">
        <h3 className="text-[13px] font-semibold text-muted">What you wrote</h3>
        {ink.length > 0 && <InkPreview strokes={ink} />}
        <p data-testid="mistake-written" className="font-display text-[17px] break-words">{prettyMath(entry.written)}</p>
        <h3 className="mt-2 text-[13px] font-semibold text-muted">What it comes to</h3>
        {revealed ? (
          <p data-testid="mistake-corrected" className="font-display text-[17px] break-words text-success">{full?.correctedStep ? prettyMath(full.correctedStep) : unavailable ? "I can't get that without a connection." : full ? "I don't have that for this one." : "…"}</p>
        ) : (
          <button type="button" data-testid="mistake-reveal" onClick={() => setRevealed(true)} className="min-h-11 self-start rounded-xl px-3 text-[14px] font-medium ring-1 ring-ink/15 hover:bg-ink/5">{entry.status === "open" ? "This one's still open. Show me anyway" : "Show me"}</button>
        )}
      </section>

      <p data-testid="mistake-standing" className="flex items-center gap-1.5 text-[15px]"><StatusIcon entry={entry} />{standing(entry)}</p>

      {full && full.hints.length > 0 && (
        <section className="flex flex-col gap-1.5">
          <h3 className="text-[13px] font-semibold text-muted">Help you asked for</h3>
          <ul data-testid="mistake-hints" className="flex flex-col gap-1.5 text-[15px] leading-snug">
            {full.hints.map((hint, index) => <li key={index} className="rounded-xl bg-duck/15 p-3">{hint.text}</li>)}
          </ul>
        </section>
      )}

      <div className="flex flex-wrap gap-2 text-[14px] font-medium">
        {entry.lesson && onLearn && (
          <button type="button" data-testid="mistake-practise-skill" onClick={() => onLearn(entry.lesson!.courseId, entry.lesson!.levelId)} className="btn btn-primary">
            <Dumbbell size={16} aria-hidden />Practise this skill
          </button>
        )}
        {entry.practiceTopic && !entry.lesson && (
          <button type="button" data-testid="mistake-practise" onClick={() => onPractice(entry.practiceTopic!, entry.id)} className="btn btn-primary">
            <Dumbbell size={16} aria-hidden />Practise another
          </button>
        )}
        {ink.length > 0 && (
          <button type="button" data-testid="mistake-show" onClick={() => onShowOnPage(entry.id)} className="btn btn-secondary">
            <MapPin size={16} aria-hidden />Show it on my page
          </button>
        )}
        {confirming ? (
          <button
            type="button"
            data-testid="mistake-delete-confirm"
            onClick={() => void deleteMistake(entry.id).then(() => onDeleted(entry.id), () => setUnavailable(true))}
            className="min-h-11 rounded-xl bg-alert px-4 font-semibold text-white"
          >
            Remove it
          </button>
        ) : (
          <button type="button" data-testid="mistake-delete" onClick={() => setConfirming(true)} className="flex min-h-11 items-center gap-2 rounded-xl px-3 text-muted hover:bg-ink/5">
            <Trash2 size={16} aria-hidden />Remove
          </button>
        )}
      </div>
      {entry.practised > 0 && <p className="text-[14px] text-muted">You&apos;ve got {entry.practised === 1 ? "a question" : `${entry.practised} questions`} right since, starting from this one.</p>}
    </div>
  );
}

// Everything here is counted from verified events, as a recap is. See docs/MISTAKE_NOTEBOOK.md.
export function MistakesView({ userId, sessionId, ready, inkOf, onShowOnPage, onPractice, onLearn, onBack }: Props) {
  const [entries, setEntries] = useState<MistakeEntry[] | null>(null);
  const [offline, setOffline] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const [topic, setTopic] = useState<string | null>(null);
  const [opened, setOpened] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Whatever has only just happened goes first, so that it's counted.
    void ready()
      .catch(() => {})
      .then(() => fetchMistakes(sessionId))
      .then(
        (notebook) => {
          if (cancelled) return;
          if (!sessionId) cacheMistakes(userId, notebook);
          setEntries(notebook.entries);
        },
        () => {
          if (cancelled) return;
          setOffline(true);
          const cached = cachedMistakes(userId)?.entries ?? [];
          setEntries(sessionId ? cached.filter((entry) => entry.sessionIds.includes(sessionId)) : cached);
        },
      );
    return () => {
      cancelled = true;
    };
  }, [userId, sessionId, ready]);

  const topics = useMemo(() => {
    const found = new Map<string, string>();
    for (const entry of entries ?? []) found.set(entry.topic, entry.topicLabel);
    return [...found.entries()];
  }, [entries]);

  const shown = (entries ?? []).filter((entry) => (topic === null || entry.topic === topic) && (filter === "all" || entry.status === filter));
  const entry = opened ? (entries ?? []).find((item) => item.id === opened) : undefined;
  const chip = (selected: boolean) => `min-h-11 max-w-full truncate rounded-full px-4 text-[14.5px] font-semibold transition-[background-color,box-shadow] ${selected ? "bg-duck shadow-[inset_0_-2px_0_var(--color-duck-edge)]" : "bg-surface text-ink-soft shadow-[inset_0_0_0_1px_rgb(38_38_38/0.12)] hover:bg-duck-soft"}`;
  const open = (entries ?? []).filter((item) => item.status === "open").length;
  const corrected = (entries ?? []).filter((item) => item.status === "corrected").length;

  return (
    <div data-testid="mistakes" className="fixed inset-0 z-40 overflow-y-auto bg-paper">
      <main className="mx-auto flex w-full max-w-2xl flex-col gap-4 p-4 pb-10 sm:p-8">
        <header className="question-in flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex size-14 shrink-0 items-center justify-center rounded-full bg-duck-soft"><Duck state="idle" size={44} /></span>
            <div className="min-w-0">
              <h1 className="font-display text-[28px] leading-tight font-bold tracking-tight">My mistakes</h1>
              {entries && entries.length > 0 && (
                <p className="text-[14.5px] text-ink-soft">
                  {corrected} put right{open > 0 ? <>, <span className="font-semibold text-alert-ink">{open} still to fix</span></> : ""}
                </p>
              )}
            </div>
          </div>
          <button type="button" data-testid="mistakes-close" onClick={onBack} className="btn btn-secondary">Back to my notebook</button>
        </header>
        {offline && <p data-testid="mistakes-offline" className="text-[14px] text-muted">I can&apos;t reach the server, so this is what&apos;s saved on this device.</p>}

        {entry ? (
          <Detail
            key={entry.id}
            entry={entry}
            inkOf={inkOf}
            onShowOnPage={onShowOnPage}
            onPractice={onPractice}
            onLearn={onLearn}
            onBack={() => setOpened(null)}
            onDeleted={(id) => {
              setOpened(null);
              setEntries((current) => (current ?? []).filter((item) => item.id !== id));
              const cached = cachedMistakes(userId);
              if (cached) cacheMistakes(userId, { ...cached, entries: cached.entries.filter((item) => item.id !== id) });
            }}
          />
        ) : (
          <>
            {entries?.length === 0 && (
              <div data-testid="mistakes-empty" className="flex flex-col items-center gap-3 py-10 text-center">
                <Duck state="idle" size={72} />
                <p className="max-w-sm text-[15px] leading-snug text-muted">Nothing here yet. When I spot a mistake in your working, you can come back to it here.</p>
              </div>
            )}
            {sessionId && entries && entries.length > 0 && <p data-testid="mistakes-from-session" className="text-[14px] text-muted">From that session.</p>}
            {entries && entries.length > 0 && (
              <div className="flex flex-col gap-2">
                <div role="group" aria-label="Show" className="flex flex-wrap gap-1.5">
                  {FILTERS.map((item) => (
                    <button key={item.value} type="button" data-testid={`mistakes-filter-${item.value}`} aria-pressed={filter === item.value} onClick={() => setFilter(item.value)} className={chip(filter === item.value)}>{item.label}</button>
                  ))}
                </div>
                {topics.length > 1 && (
                  <div role="group" aria-label="Topic" className="flex flex-wrap gap-1.5">
                    <button type="button" data-testid="mistakes-topic-all" aria-pressed={topic === null} onClick={() => setTopic(null)} className={chip(topic === null)}>Every topic</button>
                    {topics.map(([id, label]) => (
                      <button key={id} type="button" data-testid={`mistakes-topic-${id}`} aria-pressed={topic === id} onClick={() => setTopic(id)} className={chip(topic === id)}>{label}</button>
                    ))}
                  </div>
                )}
              </div>
            )}
            {entries && entries.length > 0 && shown.length === 0 && <p data-testid="mistakes-none-match" className="text-[15px] text-muted">None of those right now.</p>}
            <ul className="flex flex-col gap-2">
              {shown.map((item) => (
                <li key={item.id}>
                  <button type="button" data-testid="mistake-entry" data-status={item.status} onClick={() => setOpened(item.id)} className="card pressable relative flex min-h-11 w-full flex-col gap-1.5 overflow-hidden p-4 pl-5 text-left">
                    <span aria-hidden className={`absolute inset-y-0 left-0 w-1.5 ${item.status === "corrected" ? "bg-success" : item.status === "open" ? "bg-alert" : "bg-line"}`} />
                    <span className="flex flex-wrap items-baseline justify-between gap-x-3 text-[13.5px] text-muted">
                      <span className="min-w-0 font-semibold break-words text-ink-soft">{item.topicLabel}{item.lesson && <span className="font-normal text-muted"> · {item.lesson.levelTitle}</span>}</span>
                      <span>{when(item.lastSeenAt)}</span>
                    </span>
                    <span className="font-display text-[20px] font-bold break-words">{prettyMath(item.written)}</span>
                    <span className={`flex items-center gap-1.5 self-start rounded-full px-2.5 py-0.5 text-[13.5px] font-medium ${item.status === "corrected" ? "bg-success-soft text-success-ink" : item.status === "open" ? "bg-alert-soft text-alert-ink" : "bg-paper-deep text-muted"}`}><StatusIcon entry={item} />{standing(item)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </main>
    </div>
  );
}
