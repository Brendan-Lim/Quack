"use client";

import { Check, CornerDownRight, Flag, Lock, Play, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchCourse, masteryChecklist, masteryShare, moveOn } from "@/lib/learn";
import { prettyMath } from "@/lib/mistakes";
import type { CourseOut, LevelOut, LevelStatus } from "@/lib/types";
import { Duck } from "../duck/Duck";

const STATUS_TEXT: Record<LevelStatus, string> = {
  locked: "Locked",
  available: "Ready to start",
  in_progress: "In progress",
  mastered: "Mastered",
  moved_on: "Moved on for now",
};
// Where each node sits across the path, as a share of its width. A gentle zigzag, not a straight list.
const SWAY = [0.5, 0.72, 0.5, 0.28];
const ROW = 118;

type Props = {
  courseId: string;
  focus?: string;
  onStart: (levelId: string) => void;
};

// The journey through one course: what's done, what's next and what it needs.
export function Roadmap({ courseId, focus, onStart }: Props) {
  const [course, setCourse] = useState<CourseOut | null>(null);
  const [selected, setSelected] = useState<string | null>(focus ?? null);
  const [problem, setProblem] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchCourse(courseId).then(setCourse, () => setProblem("Quack can't load this path right now. Check the connection and try again."));
  }, [courseId]);
  useEffect(load, [load]);
  const retry = () => {
    setProblem(null);
    load();
  };

  // Where the student is: the level under way, or else the first one open to them.
  const current = useMemo(
    () => course?.levels.find((level) => level.status === "in_progress") ?? course?.levels.find((level) => level.status === "available") ?? null,
    [course],
  );
  const shown = course?.levels.find((level) => level.id === (selected ?? current?.id)) ?? course?.levels[0] ?? null;

  if (!course) {
    return (
      <main className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
        <Duck state={problem ? "idle" : "checking"} size={64} />
        <p className="text-[15px] text-muted">{problem ?? "Opening the path..."}</p>
        {problem && <button type="button" onClick={retry} className="min-h-11 rounded-xl bg-ink px-4 font-semibold text-paper">Try again</button>}
      </main>
    );
  }

  return (
    <main data-testid="roadmap" className="notebook-scroll flex-1 overflow-y-auto">
      <div className="mx-auto grid w-full max-w-5xl gap-6 px-4 py-6 sm:px-6 sm:py-8 md:grid-cols-[minmax(0,1fr)_minmax(300px,380px)] md:items-start">
        <section className="question-in min-w-0">
          <p className="eyebrow">Learning path</p>
          <h1 className="mt-0.5 font-display text-[30px] leading-tight font-bold tracking-tight sm:text-[34px]">{course.title}</h1>
          <p className="mt-1 text-[15.5px] text-ink-soft">{course.tagline}</p>
          <div className="mt-4 flex items-center gap-3">
            <div className="h-3 flex-1 overflow-hidden rounded-full bg-ink/[0.07]" role="progressbar" aria-valuemin={0} aria-valuemax={course.total} aria-valuenow={course.mastered} aria-label="Levels mastered">
              <div key={course.mastered} className="progress-fill h-full rounded-full bg-duck-deep" style={{ width: `${Math.max((course.mastered / course.total) * 100, 2)}%` }} />
            </div>
            <span data-testid="course-progress" className="text-[14px] font-semibold whitespace-nowrap text-ink-soft">{course.mastered} of {course.total} mastered</span>
          </div>
          <Path levels={course.levels} current={current?.id ?? null} selected={shown?.id ?? null} onSelect={setSelected} />
        </section>
        {shown && (
          <aside className="md:sticky md:top-6">
            <LevelCard
              level={shown}
              levels={course.levels}
              onStart={() => onStart(shown.id)}
              onMoveOn={() => void moveOn(course.id, shown.id).then(setCourse, () => setProblem("Quack can't do that right now."))}
            />
            {problem && <p role="alert" className="mt-2 text-[14px] text-alert-ink">{problem}</p>}
          </aside>
        )}
      </div>
      {/* On a narrow screen the level's card is below the whole path, so its action stays in reach here. */}
      {shown && shown.status !== "locked" && (
        <div data-testid="roadmap-dock" className="sticky bottom-0 z-10 border-t border-line bg-paper/95 px-4 py-3 backdrop-blur-sm md:hidden">
          <div className="mx-auto flex max-w-lg items-center gap-3">
            <div className="min-w-0 flex-1">
              <p className="eyebrow truncate !text-[12px]">{shown.kind === "challenge" ? "Final challenge" : `Level ${shown.number}`} · {STATUS_TEXT[shown.status]}</p>
              <p className="truncate font-display text-[17px] font-bold">{shown.title}</p>
            </div>
            <button type="button" onClick={() => onStart(shown.id)} className="btn btn-primary px-5">
              {shown.status === "mastered" ? <RotateCcw size={17} aria-hidden /> : <Play size={17} aria-hidden />}
              {shown.status === "mastered" ? "Practise again" : shown.status === "in_progress" ? "Continue" : "Start"}
            </button>
          </div>
        </div>
      )}
    </main>
  );
}

function Path({ levels, current, selected, onSelect }: { levels: LevelOut[]; current: string | null; selected: string | null; onSelect: (id: string) => void }) {
  const height = levels.length * ROW + 24;
  const point = (index: number) => ({ x: SWAY[index % SWAY.length] * 100, y: index * ROW + 56 });
  return (
    <ol data-testid="roadmap-path" className="relative mx-auto mt-6 w-full max-w-md" style={{ height }}>
      <svg aria-hidden className="absolute inset-0 h-full w-full overflow-visible" viewBox={`0 0 100 ${height}`} preserveAspectRatio="none">
        {levels.slice(1).map((level, index) => {
          const from = point(index);
          const to = point(index + 1);
          const done = levels[index].status === "mastered" || levels[index].status === "moved_on";
          const middle = (from.y + to.y) / 2;
          return (
            <path
              key={level.id}
              d={`M ${from.x} ${from.y} C ${from.x} ${middle}, ${to.x} ${middle}, ${to.x} ${to.y}`}
              fill="none"
              stroke={done ? "#F4A340" : "#E2DAC4"}
              strokeWidth={done ? 6 : 4}
              strokeDasharray={done ? undefined : "2 10"}
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
            />
          );
        })}
      </svg>
      {levels.map((level, index) => {
        const { x, y } = point(index);
        const isCurrent = level.id === current;
        const big = level.kind === "challenge";
        const labelLeft = x > 50;
        return (
          <li key={level.id} className="absolute" style={{ left: `${x}%`, top: y, transform: "translate(-50%, -50%)" }}>
            <button
              type="button"
              data-testid={`level-${level.id}`}
              data-status={level.status}
              aria-label={`Level ${level.number}, ${level.title}. ${STATUS_TEXT[level.status]}`}
              aria-current={isCurrent ? "step" : undefined}
              aria-pressed={level.id === selected}
              onClick={() => onSelect(level.id)}
              className={`relative flex items-center justify-center rounded-full font-display font-bold transition-[transform,box-shadow] duration-200 ease-[var(--ease-spring)] hover:scale-105 active:scale-95 focus-visible:outline-3 focus-visible:outline-offset-4 focus-visible:outline-duck-deep ${big ? "size-[84px] text-2xl" : "size-[68px] text-[22px]"} ${nodeStyle(level.status)} ${level.id === selected ? "ring-4 ring-ink/80 ring-offset-2 ring-offset-paper" : ""}`}
            >
              {isCurrent && <span aria-hidden className="node-pulse" />}
              <NodeIcon level={level} />
              {level.status !== "mastered" && level.status !== "locked" && <Ring share={masteryShare(level.mastery)} big={big} />}
            </button>
            <span className={`pointer-events-none absolute top-1/2 w-36 -translate-y-1/2 text-[13.5px] leading-tight sm:w-44 ${labelLeft ? "right-full mr-3 text-right" : "left-full ml-3"}`}>
              <span className="block text-[12.5px] font-bold tracking-[0.04em] text-muted uppercase">{level.kind === "challenge" ? "Final challenge" : `Level ${level.number}`}</span>
              <span className={`block font-semibold ${level.status === "locked" ? "text-ink-soft" : "text-ink"}`}>{level.title}</span>
              <span className={`block ${level.status === "mastered" ? "font-semibold text-duck-ink" : isCurrent ? "font-semibold text-ink-soft" : "text-muted"}`}>{STATUS_TEXT[level.status]}</span>
            </span>
            {isCurrent && (
              <span aria-hidden className={`absolute top-1/2 -translate-y-1/2 ${labelLeft ? "left-full ml-2" : "right-full mr-2"}`}>
                <Duck state="idle" size={44} />
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function nodeStyle(status: LevelStatus): string {
  if (status === "mastered") return "bg-duck text-ink shadow-[0_5px_0_var(--color-duck-deep)]";
  if (status === "locked") return "bg-paper-deep text-ink/40 shadow-[inset_0_0_0_2px_var(--color-line)]";
  if (status === "moved_on") return "bg-surface text-ink ring-2 ring-dashed ring-duck-deep shadow-[0_5px_0_var(--color-line)]";
  return "bg-surface text-ink ring-[3px] ring-ink shadow-[0_5px_0_var(--color-line)]";
}

function NodeIcon({ level }: { level: LevelOut }) {
  if (level.status === "mastered") return <Check size={30} strokeWidth={3} aria-hidden />;
  if (level.status === "locked") return <Lock size={22} aria-hidden />;
  if (level.kind === "challenge") return <Flag size={30} aria-hidden />;
  return <span>{level.number}</span>;
}

// How far through its mastery rule a level is, round its node.
function Ring({ share, big }: { share: number; big: boolean }) {
  const size = big ? 84 : 68;
  const radius = size / 2 - 3;
  const length = 2 * Math.PI * radius;
  if (share <= 0) return null;
  return (
    <svg aria-hidden className="absolute inset-0 -rotate-90" width={size} height={size}>
      <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="#F4A340" strokeWidth={5} strokeLinecap="round" strokeDasharray={`${share * length} ${length}`} />
    </svg>
  );
}

function LevelCard({ level, levels, onStart, onMoveOn }: { level: LevelOut; levels: LevelOut[]; onStart: () => void; onMoveOn: () => void }) {
  const waiting = level.prerequisites.filter((before) => {
    const found = levels.find((other) => other.id === before.id);
    return found && found.status !== "mastered" && found.status !== "moved_on";
  });
  const action = level.status === "mastered" ? "Practise again" : level.status === "in_progress" ? "Continue" : "Start";
  return (
    <div key={level.id} data-testid="level-card" data-level={level.id} className="card fade-in p-5 sm:p-6">
      <p className="eyebrow">{level.kind === "challenge" ? "Final challenge" : `Level ${level.number}`} · {STATUS_TEXT[level.status]}</p>
      <h2 className="mt-1 font-display text-[26px] leading-tight font-bold tracking-tight">{level.title}</h2>
      <p className="mt-1.5 text-[15.5px] leading-snug">{level.objective}</p>
      <p className="mt-3 text-[14.5px] leading-snug text-muted">{level.lesson.intro}</p>
      <p className="mt-3 rounded-2xl bg-paper px-3.5 py-2.5 font-display text-[19px] font-bold ring-1 ring-line">{prettyMath(level.lesson.example.question)}</p>

      {level.prerequisites.length > 0 && (
        <p className="mt-3 flex items-start gap-1.5 text-[13px] text-muted">
          <CornerDownRight size={14} aria-hidden className="mt-0.5 shrink-0" />
          Builds on {level.prerequisites.map((before) => before.title).join(", ")}
        </p>
      )}

      {level.status !== "locked" && (
        <ul data-testid="mastery-checklist" aria-label="To master this level" className="mt-4 space-y-1.5 text-[14px]">
          {masteryChecklist(level.mastery).map((item) => (
            <li key={item.label} className={`flex items-center gap-2 ${item.met ? "text-ink" : "text-muted"}`}>
              <span className={`flex size-5 shrink-0 items-center justify-center rounded-full ${item.met ? "bg-success text-white" : "ring-1 ring-ink/20"}`}>{item.met && <Check size={12} strokeWidth={3} aria-hidden />}</span>
              {item.label}
            </li>
          ))}
        </ul>
      )}

      {level.status === "locked" ? (
        <p data-testid="level-locked" className="mt-4 flex items-center gap-2 rounded-2xl bg-paper p-3 text-[14px] text-muted">
          <Lock size={16} aria-hidden className="shrink-0" />
          Finish {waiting.map((before) => before.title).join(" and ")} first.
        </p>
      ) : (
        <div className="mt-5 flex flex-wrap gap-2">
          <button type="button" data-testid="level-start" onClick={onStart} className="btn btn-primary btn-lg flex-1">
            {level.status === "mastered" ? <RotateCcw size={18} aria-hidden /> : <Play size={18} aria-hidden />}
            {action}
          </button>
          {level.mastery.canMoveOn && (
            <button type="button" data-testid="level-move-on" onClick={onMoveOn} className="btn btn-secondary btn-lg text-[15px]">
              Move on for now
            </button>
          )}
        </div>
      )}
      {level.mastery.canMoveOn && level.status !== "locked" && (
        <p className="mt-2 text-[13px] text-muted">Moving on opens the next level. This one stays here to master later.</p>
      )}
    </div>
  );
}
