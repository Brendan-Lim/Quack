"use client";

import { ArrowRight, Flag, GraduationCap, UserRound, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { cachedRecaps, cacheRecap, currentSession, fetchRecap, finishSession, rememberedInk, rememberInk } from "@/lib/account";
import { boundsOfStrokes } from "@/lib/geometry";
import { setStudySession } from "@/lib/api";
import { fetchLearnHome } from "@/lib/learn";
import type { AuthClient, AuthUser } from "@/lib/auth";
import { LearningQueue } from "@/lib/learning";
import { notebookKeyFor } from "@/lib/persist";
import type { ContinueOut, PenStroke, PracticeSummary, PracticeTopic, Recap } from "@/lib/types";
import type { QuackOptions } from "@/hooks/useQuack";
import { QuackApp, type NotebookHandle } from "../QuackApp";
import { LearnView, type LearnRequest, type LearnTarget } from "../learn/LearnView";
import { ModeSwitch } from "../nav/ModeSwitch";
import { HistoryView } from "./HistoryView";
import { MistakesView } from "./MistakesView";
import { PracticeView } from "./PracticeView";
import { RecapView } from "./RecapView";
import { SettingsView } from "./SettingsView";

type View =
  | { name: "notebook" }
  | { name: "recap"; recap: Recap; practice: PracticeSummary[]; from: "finish" | "history" }
  | { name: "history" }
  | { name: "settings" }
  | { name: "mistakes"; sessionId: string | null }
  | { name: "practice"; topic: PracticeTopic | null; origin: string | null; source?: string | null }
  | { name: "learn" };

const sessionKey = (userId: string) => `quack.session.${userId}`;

// Read when an event happens or a request goes out, which aren't renders.
class CurrentSessionId {
  private id: string | null = null;
  get = () => this.id;
  set = (id: string | null) => {
    this.id = id;
  };
}

type Props = {
  user: AuthUser;
  auth: AuthClient;
  // Where to start: the notebook, or Learn, as chosen on a first visit.
  start?: "notebook" | "learn";
  onSignedOut: () => void;
  onRedoCalibration: () => void;
};

// A signed-in student's notebook, and what opens over it. The notebook stays mounted underneath, so
// coming back from a recap finds the page, the highlights and any open alert just as they were.
export function AccountHome({ user, auth, start = "notebook", onSignedOut, onRedoCalibration }: Props) {
  const [view, setView] = useState<View>(() => (start === "learn" ? { name: "learn" } : { name: "notebook" }));
  // Learn, once opened, stays mounted under the notebook, so the lesson under way and its working are there on
  // the way back. Each request to go somewhere in it is new, even to the same place.
  const [learn, setLearn] = useState<LearnRequest | null>(() => (start === "learn" ? { target: null, at: 1 } : null));
  const requests = useRef(1);
  const openLearn = useCallback((target: LearnTarget | null) => {
    requests.current += 1;
    setLearn({ target, at: requests.current });
    setView({ name: "learn" });
  }, []);
  const [wrappedUp, setWrappedUp] = useState<Recap | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  // A lesson under way, to go back to in one tap. Only from the server, so never made up.
  const [resume, setResume] = useState<ContinueOut | null>(null);
  const [resumeDismissed, setResumeDismissed] = useState(false);
  // Until the server says, carry on with the last session known, as events can wait for it.
  const [session] = useState(() => new CurrentSessionId());

  const queue = useMemo(() => new LearningQueue(`quack.events.${user.id}`, session.get), [user.id, session]);

  const backToNotebookSession = useCallback(() => setStudySession(session.get), [session]);

  const loadResume = useCallback(() => {
    fetchLearnHome().then((home) => setResume(home.continueLearning), () => {});
  }, []);

  const startOrResume = useCallback(
    () =>
      currentSession().then(({ session: current, wrappedUp: wrapped }) => {
        session.set(current.id);
        localStorage.setItem(sessionKey(user.id), current.id);
        if (wrapped) {
          cacheRecap(user.id, wrapped);
          setWrappedUp(wrapped);
        }
      }),
    [user.id, session],
  );

  useEffect(() => {
    session.set(localStorage.getItem(sessionKey(user.id)));
    backToNotebookSession();
    void startOrResume().catch(() => {});
    loadResume();
    void queue.flush();
    const flush = () => void queue.flush();
    const flushIfHidden = () => {
      if (document.visibilityState === "hidden") flush();
    };
    window.addEventListener("online", flush);
    document.addEventListener("visibilitychange", flushIfHidden);
    return () => {
      window.removeEventListener("online", flush);
      document.removeEventListener("visibilitychange", flushIfHidden);
      queue.dispose();
    };
  }, [user.id, session, queue, startOrResume, backToNotebookSession, loadResume]);

  const finish = useCallback(async () => {
    const id = session.get();
    if (finishing || !id) return;
    setFinishing(true);
    setProblem(null);
    try {
      await queue.flush();
      const recap = await finishSession(id);
      cacheRecap(user.id, recap);
      setView({ name: "recap", recap, practice: [], from: "finish" });
      // Whatever happens next belongs to a new session. The notes carry on as they were.
      await startOrResume();
    } catch {
      setProblem("Quack can't finish the session without a connection. Your notes are safe, and it will all still be there.");
    } finally {
      setFinishing(false);
    }
  }, [finishing, session, queue, startOrResume, user.id]);

  const openRecap = useCallback(
    async (recapId: string) => {
      const cached = cachedRecaps(user.id).find((item) => item.sessionId === recapId);
      if (cached) setView({ name: "recap", recap: cached, practice: [], from: "history" });
      try {
        const detail = await fetchRecap(recapId);
        cacheRecap(user.id, detail.recap);
        setView({ name: "recap", recap: detail.recap, practice: detail.practice, from: "history" });
      } catch {
        if (!cached) setProblem("That recap isn't on this device, and Quack can't reach its server just now.");
      }
    },
    [user.id],
  );

  const signOut = useCallback(async () => {
    await queue.flush().catch(() => {});
    await auth.signOut().catch(() => {});
    onSignedOut();
  }, [auth, queue, onSignedOut]);

  const notebook = useRef<NotebookHandle | null>(null);
  const options = useMemo<QuackOptions>(
    () => ({
      notebookKey: notebookKeyFor(user.id),
      learning: queue,
      onMistakeInk: (mistakeId, strokeIds) => rememberInk(user.id, mistakeId, strokeIds),
    }),
    [user.id, queue],
  );

  const inkOf = useCallback(
    (mistakeId: string): PenStroke[] => {
      const ids = new Set(rememberedInk(user.id)[mistakeId] ?? []);
      if (!ids.size) return [];
      return (notebook.current?.strokes() ?? []).filter((stroke) => ids.has(stroke.id));
    },
    [user.id],
  );

  const flushEvents = useCallback(() => queue.flush(), [queue]);

  const actions = (
    <>
      {/* Last, on a line of its own, on a phone; in the row from 640px. */}
      <div className="order-last flex w-full justify-center pb-1.5 sm:order-none sm:ml-1 sm:w-auto sm:pb-0">
        <ModeSwitch mode="notebook" compact onNotebook={() => {}} onLearn={() => openLearn(null)} />
      </div>
      <button
        type="button"
        data-testid="finish-session"
        onClick={() => void finish()}
        disabled={finishing}
        title="Finish session"
        aria-label="Finish session"
        className="ml-1 flex h-9 shrink-0 items-center gap-1.5 rounded-lg bg-duck px-3 text-[13px] font-semibold text-ink transition-opacity hover:opacity-85 disabled:opacity-50 pointer-coarse:h-11"
      >
        <Flag size={16} aria-hidden />
        <span className="hidden whitespace-nowrap lg:inline">Finish session</span>
      </button>
      <div className="relative">
        <button
          type="button"
          data-testid="account-menu"
          aria-label="Account"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}
          className="flex size-9 shrink-0 items-center justify-center rounded-lg text-ink/70 hover:bg-ink/5 pointer-coarse:size-11"
        >
          <UserRound size={18} aria-hidden />
        </button>
        {menuOpen && (
          <div role="menu" className="absolute top-full right-0 z-30 mt-1 flex w-44 flex-col rounded-xl bg-white p-1 text-sm shadow-lg ring-1 ring-ink/10">
            {[
              ["menu-mistakes", "My mistakes", () => setView({ name: "mistakes", sessionId: null })],
              ["menu-history", "Past recaps", () => setView({ name: "history" })],
              ["menu-practice", "Practice", () => setView({ name: "practice", topic: null, origin: null })],
              ["menu-settings", "Settings", () => setView({ name: "settings" })],
              ["menu-sign-out", "Sign out", () => void signOut()],
            ].map(([id, label, run]) => (
              <button
                key={id as string}
                type="button"
                role="menuitem"
                data-testid={id as string}
                onClick={() => {
                  setMenuOpen(false);
                  (run as () => void)();
                }}
                className="min-h-11 rounded-lg px-3 text-left hover:bg-ink/5"
              >
                {label as string}
              </button>
            ))}
          </div>
        )}
      </div>
    </>
  );

  const close = () => {
    backToNotebookSession();
    setView({ name: "notebook" });
    loadResume();
  };

  return (
    <>
      <QuackApp options={options} accountActions={actions} notebookHandle={notebook} covered={view.name !== "notebook"} />

      {view.name === "notebook" && (problem || wrappedUp) && (
        <div className="fixed top-16 left-1/2 z-20 w-[min(30rem,calc(100vw-2rem))] -translate-x-1/2 rounded-xl bg-white p-3 text-[14px] shadow-md ring-1 ring-ink/5">
          {problem ? (
            <div className="flex items-center justify-between gap-3">
              <p role="alert" data-testid="home-problem" className="text-alert">{problem}</p>
              <button type="button" onClick={() => setProblem(null)} className="min-h-11 shrink-0 px-2 font-medium">OK</button>
            </div>
          ) : (
            <div className="flex items-center justify-between gap-3">
              <p data-testid="wrapped-up" className="text-muted">Your last session was wrapped up after a long break.</p>
              <div className="flex shrink-0 gap-1 font-medium">
                <button type="button" onClick={() => setWrappedUp(null)} className="min-h-11 px-2 text-muted">Dismiss</button>
                <button
                  type="button"
                  data-testid="wrapped-up-open"
                  className="min-h-11 px-2"
                  onClick={() => {
                    setView({ name: "recap", recap: wrappedUp!, practice: [], from: "finish" });
                    setWrappedUp(null);
                  }}
                >
                  See the recap
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {view.name === "notebook" && !problem && !wrappedUp && resume && !resumeDismissed && (
        <div data-testid="continue-chip" className="fixed top-16 left-1/2 z-20 flex w-[min(30rem,calc(100vw-2rem))] -translate-x-1/2 items-center gap-1 rounded-2xl bg-white p-1.5 pl-3 text-[14px] shadow-md ring-1 ring-duck-deep/40">
          <GraduationCap size={18} aria-hidden className="shrink-0 text-duck-deep" />
          <button
            type="button"
            data-testid="continue-chip-open"
            onClick={() => openLearn({ courseId: resume.courseId, levelId: resume.levelId })}
            className="flex min-h-11 min-w-0 flex-1 items-center justify-between gap-2 rounded-xl px-2 text-left hover:bg-duck/20"
          >
            <span className="min-w-0 leading-tight">
              <span className="block font-semibold">Continue learning</span>
              <span className="block text-[13px] text-muted">{resume.courseTitle}, {resume.levelTitle}</span>
            </span>
            <ArrowRight size={16} aria-hidden className="shrink-0" />
          </button>
          <button type="button" aria-label="Hide for now" onClick={() => setResumeDismissed(true)} className="flex size-11 shrink-0 items-center justify-center rounded-xl text-muted hover:bg-ink/5">
            <X size={16} aria-hidden />
          </button>
        </div>
      )}

      {learn && <LearnView queue={queue} request={learn} active={view.name === "learn"} onNotebook={close} />}

      {view.name === "recap" && (
        <RecapView
          recap={view.recap}
          practice={view.practice}
          onBack={close}
          onHistory={() => setView({ name: "history" })}
          onPractice={(topic) => setView({ name: "practice", topic, origin: view.recap.sessionId })}
          onReview={(sessionId) => setView({ name: "mistakes", sessionId })}
        />
      )}
      {view.name === "mistakes" && (
        <MistakesView
          userId={user.id}
          sessionId={view.sessionId}
          ready={flushEvents}
          inkOf={inkOf}
          onShowOnPage={(mistakeId) => {
            const bounds = boundsOfStrokes(inkOf(mistakeId));
            close();
            if (bounds) notebook.current?.bringIntoView(bounds.minY);
          }}
          onPractice={(topic, mistakeId) => setView({ name: "practice", topic, origin: null, source: mistakeId })}
          onLearn={(courseId, levelId) => openLearn({ courseId, levelId })}
          onBack={close}
        />
      )}
      {view.name === "history" && <HistoryView userId={user.id} onOpen={(id) => void openRecap(id)} onBack={close} />}
      {view.name === "settings" && (
        <SettingsView
          user={user}
          auth={auth}
          onBack={close}
          onSignOut={() => void signOut()}
          onSignedOut={onSignedOut}
          onRedoCalibration={onRedoCalibration}
        />
      )}
      {view.name === "practice" && (
        <PracticeView topic={view.topic} origin={view.origin} source={view.source ?? null} queue={queue} onClose={close} />
      )}
    </>
  );
}
