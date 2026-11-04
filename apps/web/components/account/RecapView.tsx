"use client";

import { BookOpenCheck, Dumbbell } from "lucide-react";
import { useEffect, useState } from "react";
import { fetchPracticeTopics } from "@/lib/account";
import { prettyMath } from "@/lib/mistakes";
import type { PracticeOptions, PracticeSummary, PracticeTopic, Recap } from "@/lib/types";
import { Duck } from "../duck/Duck";

type Props = {
  recap: Recap;
  practice: PracticeSummary[];
  onBack: () => void;
  onHistory: () => void;
  onPractice: (topic: PracticeTopic | null) => void;
  onReview: (sessionId: string) => void;
};

const when = (seconds: number) =>
  new Date(seconds * 1000).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", hour: "numeric", minute: "2-digit" });

function Tally({ label, value, testId, tone }: { label: string; value: number; testId: string; tone?: "good" | "open" }) {
  const colour = tone === "good" && value ? "text-success" : tone === "open" && value ? "text-alert" : "text-ink";
  return (
    <div className="flex min-w-0 flex-1 flex-col items-center rounded-2xl bg-white px-2 py-2.5 text-center ring-1 ring-ink/5">
      <p data-testid={testId} className={`font-display text-2xl leading-none font-bold ${colour}`}>{value}</p>
      <p className="mt-1 text-[13px] leading-tight text-muted">{label}</p>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2.5">
      <h2 className="font-display text-[19px] font-bold">{title}</h2>
      {children}
    </section>
  );
}

function plural(count: number, noun: string) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

// Every number and sentence here came from the server's count of verified events.
export function RecapView({ recap, practice, onBack, onHistory, onPractice, onReview }: Props) {
  const { glance } = recap;
  const [options, setOptions] = useState<PracticeOptions | null>(null);
  const [choosing, setChoosing] = useState(false);

  useEffect(() => {
    if (!choosing || options) return;
    fetchPracticeTopics().then(setOptions, () => setChoosing(false));
  }, [choosing, options]);

  const wentWell = recap.observations.length > 0;
  const nothingFound = glance.found === 0;

  return (
    <div data-testid="recap" className="fixed inset-0 z-40 overflow-y-auto bg-paper">
      <main className="mx-auto flex w-full max-w-xl flex-col gap-7 p-4 pb-10 sm:p-8">
        <header className="flex items-start gap-3">
          <Duck state={glance.found > 0 && glance.found === glance.corrected ? "resolved" : "idle"} size={64} />
          <div className="min-w-0 pt-1">
            <p className="text-[13px] text-muted">{when(recap.finishedAt)}{recap.finishReason === "inactive" ? ", after a long break" : ""}</p>
            <h1 data-testid="recap-opening" className="font-display text-[22px] leading-tight font-bold tracking-tight sm:text-2xl">{recap.opening || "Here's how that went."}</h1>
          </div>
        </header>

        {!nothingFound && (
          <div data-testid="recap-tally" className="flex gap-2">
            <Tally label="Spotted" value={glance.found} testId="glance-found" />
            <Tally label="Corrected" value={glance.corrected} testId="glance-corrected" tone="good" />
            <Tally label="Still open" value={glance.unresolved} testId="glance-unresolved" tone="open" />
          </div>
        )}
        {/* Kept in reach for anyone counting, but not the point of the page. */}
        <p data-testid="recap-small-print" className={nothingFound ? "sr-only" : "-mt-5 text-[13px] text-muted"}>
          {nothingFound && <span data-testid="glance-found">{glance.found}</span>}
          <span data-testid="glance-independent" className="sr-only">{glance.independent}</span>
          <span data-testid="glance-hints" className="sr-only">{glance.hints}</span>
          <span data-testid="glance-explanations" className="sr-only">{glance.explanations}</span>
          {glance.independent} fixed on your own · {plural(glance.hints, "hint")} · {plural(glance.explanations, "explanation")}
        </p>

        {recap.topics.length > 0 && (
          <Section title="What you worked on">
            <div className="flex flex-col gap-2.5" data-testid="recap-topics">
              {recap.topics.flatMap((group) =>
                group.topics.map((topic) => (
                  <div key={topic.topic} className="rounded-2xl bg-white p-3 ring-1 ring-ink/5">
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                      <span className="min-w-0 text-[15px] font-medium break-words">{topic.label}</span>
                      <span className={`text-[13px] ${topic.corrected === topic.mistakes ? "text-success" : "text-muted"}`}>
                        {topic.corrected === topic.mistakes ? (topic.mistakes === 1 ? "Corrected" : "All corrected") : `${topic.corrected} of ${topic.mistakes} corrected`}
                      </span>
                    </div>
                    {topic.examples.length > 0 && (
                      <ul className="mt-1 flex flex-col gap-0.5 text-[14px] break-words text-muted">
                        {topic.examples.map((example) => <li key={example}>{prettyMath(example)}</li>)}
                      </ul>
                    )}
                  </div>
                )),
              )}
            </div>
            {recap.leftOpen.length > 0 && (
              <ul data-testid="recap-left-open" className="flex flex-col gap-1 text-[14px] leading-snug text-muted">
                {recap.leftOpen.map((line) => <li key={line}>{line}</li>)}
              </ul>
            )}
            <button type="button" data-testid="review-mistakes" onClick={() => onReview(recap.sessionId)} className="flex min-h-11 items-center gap-2 self-start rounded-xl px-3 text-[14px] font-medium ring-1 ring-ink/15 hover:bg-ink/5">
              <BookOpenCheck size={16} aria-hidden />Review those steps
            </button>
          </Section>
        )}

        {wentWell && (
          <Section title="What went well">
            <ul data-testid="recap-observations" className="flex flex-col gap-1 text-[15px] leading-snug">
              {recap.observations.map((line) => <li key={line}>{line}</li>)}
            </ul>
          </Section>
        )}

        {practice.length > 0 && (
          <Section title="Practice since then">
            <ul className="flex flex-col gap-1 text-[15px]">
              {practice.map((item) => <li key={item.sessionId}>{item.topicLabel}: {item.correct} of {item.attempted} right</li>)}
            </ul>
          </Section>
        )}

        <Section title="What to practise next">
          {recap.recommendation ? (
            <div className="flex flex-col gap-3 rounded-2xl bg-duck/20 p-4">
              <p data-testid="recommendation" className="text-[15px] leading-snug"><span className="font-semibold">{recap.recommendation.label}.</span> {recap.recommendation.reason}</p>
              <button type="button" data-testid="practice-this" onClick={() => onPractice(recap.recommendation!.topic)} className="flex min-h-11 items-center gap-2 self-start rounded-xl bg-ink px-4 font-semibold text-paper hover:opacity-85">
                <Dumbbell size={16} aria-hidden />Try a question
              </button>
            </div>
          ) : (
            <p data-testid="recap-note" className="text-[15px] leading-snug">{recap.note}</p>
          )}
          {!recap.recommendation && !choosing && (
            <button type="button" data-testid="general-practice" onClick={() => setChoosing(true)} className="min-h-11 self-start rounded-xl px-4 font-medium ring-1 ring-ink/15 hover:bg-ink/5">Pick something to practise</button>
          )}
          {choosing && options && (
            <div className="flex flex-wrap gap-2">
              {options.topics.map((option) => (
                <button key={option.topic} type="button" data-testid={`practice-topic-${option.topic}`} onClick={() => onPractice(option.topic)} className="min-h-11 rounded-full bg-white px-4 text-[14px] font-medium ring-1 ring-ink/10 hover:bg-duck/30">{option.label}</button>
              ))}
            </div>
          )}
        </Section>

        <footer className="flex flex-wrap gap-2 border-t border-ink/5 pt-4 text-[15px]">
          <button type="button" data-testid="recap-back" onClick={onBack} className="min-h-11 rounded-xl bg-ink px-4 font-semibold text-paper hover:opacity-85">Back to my notebook</button>
          <button type="button" data-testid="recap-history" onClick={onHistory} className="min-h-11 rounded-xl px-4 font-medium ring-1 ring-ink/15 hover:bg-ink/5">Past recaps</button>
        </footer>
      </main>
    </div>
  );
}
