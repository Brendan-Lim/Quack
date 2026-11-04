"use client";

import { ArrowLeft, ArrowRight, BookOpen, Calculator, Divide, Lock, NotebookPen, Ruler, Sigma, Triangle, Variable } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { fetchLearnHome } from "@/lib/learn";
import type { LearningQueue } from "@/lib/learning";
import type { LearnHome, PathOut } from "@/lib/types";
import { Duck } from "../duck/Duck";
import { ModeSwitch } from "../nav/ModeSwitch";
import { LessonView } from "./LessonView";
import { Roadmap } from "./Roadmap";

export type LearnTarget = { courseId: string; levelId?: string };
// Somewhere to go, asked for from outside: a new one each time it's asked for, even for the same place.
export type LearnRequest = { target: LearnTarget | null; at: number };

type Route = { name: "home" } | { name: "course"; courseId: string; focus?: string } | { name: "lesson"; courseId: string; levelId: string };

// Each course's own colour, used only on its icon tile, so the catalog isn't one grey wall and nothing but the
// tile changes. Algebra, the course that's open, wears the duck's yellow.
const COURSE_COLOURS: Record<string, string> = {
  arithmetic_foundations: "var(--color-course-arithmetic)",
  fractions_decimals: "var(--color-course-fractions)",
  algebra_foundations: "var(--color-course-algebra)",
  geometry: "var(--color-course-geometry)",
  trigonometry: "var(--color-course-trigonometry)",
  calculus: "var(--color-course-calculus)",
};

const ICONS: Record<string, ReactNode> = {
  arithmetic_foundations: <Calculator size={22} aria-hidden />,
  fractions_decimals: <Divide size={22} aria-hidden />,
  algebra_foundations: <Variable size={22} aria-hidden />,
  geometry: <Ruler size={22} aria-hidden />,
  trigonometry: <Triangle size={22} aria-hidden />,
  calculus: <Sigma size={22} aria-hidden />,
};

type Props = {
  queue: LearningQueue;
  // Where to open: a course's roadmap, or straight into a level.
  target?: LearnTarget | null;
  // Somewhere to go later. It goes there, unless that lesson is already open.
  request?: LearnRequest | null;
  // Shown, rather than kept underneath the notebook while the student is there.
  active?: boolean;
  onNotebook: () => void;
};

const routeFor = (target: LearnTarget | null): Route =>
  target?.levelId ? { name: "lesson", courseId: target.courseId, levelId: target.levelId } : target ? { name: "course", courseId: target.courseId } : { name: "home" };

// Learning paths, over the notebook, which stays as it was underneath. Everything about progress comes
// from the server: nothing here decides what's unlocked or mastered. Going to the notebook keeps this as it is,
// hidden, so a question half answered is still there, with its working, on the way back.
export function LearnView({ queue, target = null, request = null, active = true, onNotebook }: Props) {
  const [route, setRoute] = useState<Route>(() => routeFor(request?.target ?? target));
  const seen = useRef(request?.at ?? 0);

  useEffect(() => {
    if (!request || request.at === seen.current) return;
    seen.current = request.at;
    // Back to Learn with nowhere in particular to go: where it was. Somewhere in particular: there, and the lesson
    // already open stays open if that's where it is.
    if (!request.target) return;
    const next = routeFor(request.target);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- a request from outside, once per request
    setRoute((now) => (now.name === "lesson" && next.name === "lesson" && now.courseId === next.courseId && now.levelId === next.levelId ? now : next));
  }, [request]);

  const visit = request?.at ?? 0;
  return (
    <div data-testid="learn" data-active={active} hidden={!active} inert={!active} className="fixed inset-0 z-40 flex flex-col bg-paper">
      {route.name === "lesson" ? (
        <LessonView
          key={`${route.courseId}/${route.levelId}`}
          courseId={route.courseId}
          levelId={route.levelId}
          queue={queue}
          active={active}
          onNotebook={onNotebook}
          onRoadmap={(focus) => setRoute({ name: "course", courseId: route.courseId, focus })}
          onLevel={(levelId) => setRoute({ name: "lesson", courseId: route.courseId, levelId })}
        />
      ) : (
        <>
          <Header onNotebook={onNotebook} onHome={route.name === "course" ? () => setRoute({ name: "home" }) : undefined} />
          {/* Each visit loads them again: a level can be mastered by practice from the notebook in between. */}
          {route.name === "home" ? (
            <Home key={visit} onCourse={(courseId) => setRoute({ name: "course", courseId })} onLevel={(courseId, levelId) => setRoute({ name: "lesson", courseId, levelId })} onNotebook={onNotebook} />
          ) : (
            <Roadmap key={visit} courseId={route.courseId} focus={route.focus} onStart={(levelId) => setRoute({ name: "lesson", courseId: route.courseId, levelId })} />
          )}
        </>
      )}
    </div>
  );
}

function Header({ onNotebook, onHome }: { onNotebook: () => void; onHome?: () => void }) {
  return (
    <header className="flex h-16 shrink-0 items-center justify-between gap-3 border-b border-line px-3 sm:px-5">
      <button type="button" onClick={onHome} disabled={!onHome} data-testid="learn-home" className="flex min-h-11 items-center gap-1.5 rounded-xl pr-2.5 pl-1 transition-colors enabled:hover:bg-ink/5 disabled:cursor-default">
        {onHome ? <ArrowLeft size={20} aria-hidden className="ml-1" /> : <Duck state="idle" size={32} />}
        <span className="font-display text-[19px] font-bold tracking-tight whitespace-nowrap">{onHome ? "All paths" : "Quack"}</span>
      </button>
      <ModeSwitch mode="learn" onNotebook={onNotebook} onLearn={() => {}} />
    </header>
  );
}

function Home({ onCourse, onLevel, onNotebook }: { onCourse: (courseId: string) => void; onLevel: (courseId: string, levelId: string) => void; onNotebook: () => void }) {
  const [home, setHome] = useState<LearnHome | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchLearnHome().then(setHome, () => setProblem("Quack can't load the learning paths right now. Check the connection and try again."));
  }, []);
  useEffect(load, [load]);
  const retry = () => {
    setProblem(null);
    load();
  };

  const resume = home?.continueLearning;
  return (
    <main className="notebook-scroll flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-4 py-6 sm:px-6 sm:py-8">
        <section className="question-in flex items-center gap-4">
          <span className="flex size-[76px] shrink-0 items-center justify-center rounded-full bg-duck-soft"><Duck state="hint" size={62} /></span>
          <div className="min-w-0">
            <h1 className="font-display text-[26px] leading-tight font-bold tracking-tight sm:text-[32px]">What would you like to learn?</h1>
            <p className="mt-1 max-w-2xl text-[15.5px] leading-snug text-ink-soft">Pick a path and work in your own handwriting. Quack checks each step and helps you fix your own mistakes.</p>
          </div>
        </section>

        {resume && (
          <button
            type="button"
            data-testid="continue-learning"
            onClick={() => onLevel(resume.courseId, resume.levelId)}
            className="group pressable flex items-center gap-4 rounded-[var(--radius-card)] bg-duck p-4 text-left shadow-[0_4px_0_var(--color-duck-edge)] sm:p-5"
          >
            <span className="flex size-14 shrink-0 items-center justify-center rounded-2xl bg-surface/80 font-display text-[26px] font-bold">{resume.levelNumber}</span>
            <span className="min-w-0 flex-1">
              <span className="block text-[13px] font-bold tracking-[0.06em] text-duck-ink uppercase">Continue learning</span>
              <span className="block font-display text-[20px] leading-tight font-bold sm:text-[22px]">{resume.courseTitle}: {resume.levelTitle}</span>
            </span>
            <span className="flex size-11 shrink-0 items-center justify-center rounded-full bg-ink text-paper transition-transform group-hover:translate-x-0.5"><ArrowRight size={20} aria-hidden /></span>
          </button>
        )}

        {problem && (
          <p role="alert" className="flex items-center justify-between gap-3 rounded-2xl bg-alert-soft p-3 text-[14.5px] text-alert-ink">
            {problem}
            <button type="button" onClick={retry} className="btn btn-secondary shrink-0">Try again</button>
          </p>
        )}

        <section aria-labelledby="paths-title">
          <h2 id="paths-title" className="eyebrow mb-3">Learning paths</h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {(home?.paths ?? []).map((path) => <PathCard key={path.id} path={path} onOpen={() => onCourse(path.id)} />)}
            {!home && !problem && [0, 1, 2].map((index) => <div key={index} aria-hidden className="h-44 animate-pulse rounded-[var(--radius-card)] bg-ink/5 motion-reduce:animate-none" />)}
          </div>
        </section>

        <button type="button" data-testid="notebook-card" onClick={onNotebook} className="card pressable flex items-center gap-4 p-4 text-left sm:p-5">
          <span className="flex size-12 shrink-0 items-center justify-center rounded-2xl bg-ink text-paper"><NotebookPen size={22} aria-hidden /></span>
          <span className="min-w-0 flex-1">
            <span className="block font-display text-lg font-bold">My Notebook</span>
            <span className="block text-[14.5px] text-muted">Write any maths you like. Quack still watches for slips.</span>
          </span>
          <ArrowRight size={20} aria-hidden className="shrink-0 text-muted" />
        </button>
      </div>
    </main>
  );
}

function PathCard({ path, onOpen }: { path: PathOut; onOpen: () => void }) {
  const playable = path.status === "available";
  const share = playable && path.total ? (path.mastered ?? 0) / path.total : 0;
  const colour = COURSE_COLOURS[path.id] ?? "var(--color-muted)";
  const duckTile = playable && path.id === "algebra_foundations";
  const body = (
    <>
      <span className="flex items-start justify-between gap-2">
        {/* Algebra wears the duck's yellow. Every other open path its own colour, on its tile only, so the six don't
            look like one wall of yellow, or like six different products. */}
        <span
          className={`flex size-12 items-center justify-center rounded-2xl ${duckTile ? "bg-duck text-ink shadow-[inset_0_-3px_0_var(--color-duck-edge)]" : ""}`}
          style={
            duckTile
              ? undefined
              : playable
                ? { color: `color-mix(in srgb, ${colour} 82%, black)`, background: `color-mix(in srgb, ${colour} 20%, white)`, boxShadow: `inset 0 -3px 0 color-mix(in srgb, ${colour} 45%, white)` }
                : { color: colour, background: `color-mix(in srgb, ${colour} 13%, white)` }
          }
        >
          {ICONS[path.id] ?? <BookOpen size={22} aria-hidden />}
        </span>
        {playable ? (
          <span className="flex items-center gap-1 rounded-full bg-success-soft px-2.5 py-1 text-[12.5px] font-bold text-success-ink">Open</span>
        ) : (
          <span className="flex items-center gap-1 rounded-full bg-paper-deep px-2.5 py-1 text-[12.5px] font-semibold text-muted">
            <Lock size={12} aria-hidden />
            Coming later
          </span>
        )}
      </span>
      <span className="mt-3.5 block font-display text-[19px] leading-tight font-bold">{path.title}</span>
      <span className={`mt-1 block text-[14.5px] leading-snug ${playable ? "text-ink-soft" : "text-muted"}`}>{path.tagline}</span>
      {playable ? (
        <span className="mt-auto block pt-4">
          <span className="flex items-center justify-between text-[13.5px] font-semibold text-ink-soft">
            <span>{path.mastered} of {path.total} levels mastered</span>
            <ArrowRight size={18} aria-hidden className="text-ink" />
          </span>
          <span className="mt-2 block h-2.5 overflow-hidden rounded-full bg-ink/[0.07]">
            <span className="progress-fill block h-full rounded-full bg-duck-deep" style={{ width: `${Math.max(share * 100, 3)}%` }} />
          </span>
        </span>
      ) : (
        <span className="mt-auto block pt-3 text-[13.5px] text-muted">{path.preview.join(" · ")}</span>
      )}
    </>
  );
  if (!playable) {
    return (
      <div data-testid={`path-${path.id}`} data-status={path.status} aria-disabled className="flex flex-col rounded-[var(--radius-card)] border border-dashed border-line bg-surface/55 p-4 sm:p-5">
        {body}
      </div>
    );
  }
  return (
    <button type="button" data-testid={`path-${path.id}`} data-status={path.status} onClick={onOpen} className="card pressable flex flex-col p-4 text-left ring-2 ring-duck sm:p-5">
      {body}
    </button>
  );
}
