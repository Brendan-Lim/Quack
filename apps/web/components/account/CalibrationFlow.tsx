"use client";

import { Check, RotateCcw, Undo2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { fetchCalibration, saveSample } from "@/lib/account";
import { ApiError } from "@/lib/api";
import { boundsOfStrokes } from "@/lib/geometry";
import type { Marker } from "@/lib/mistakes";
import { PAGE_WIDTH } from "@/lib/notebook";
import { renderSnapshot, selectRegion } from "@/lib/snapshot";
import { NotebookStore } from "@/lib/store";
import type { CalibrationState } from "@/lib/types";
import { Duck } from "../duck/Duck";
import { NotebookCanvas } from "../notebook/NotebookCanvas";

const NO_MARKERS: Marker[] = [];

async function problemFrom(error: unknown): Promise<string> {
  if (error instanceof ApiError && error.status === 422) return "That looks unfinished. Try writing the whole expression.";
  return "Quack couldn't save that. Check the connection and try again. Your writing is still here.";
}

// The one time a student is asked to write what they're told. It's copying, not a test, so nothing
// here says whether anything is right. What's already saved stays saved, on any device.
export function CalibrationFlow({ onDone }: { onDone: () => void }) {
  const [state, setState] = useState<CalibrationState | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [started, setStarted] = useState(false);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const store = useMemo(() => new NotebookStore({ persist: false }), []);
  const notebook = useSyncExternalStore(store.subscribe, store.getState, store.getState);
  const savingRef = useRef(false);

  const load = useCallback(() => fetchCalibration().then(setState, () => setLoadFailed(true)), []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loadFailed) {
    return (
      <main className="flex min-h-dvh flex-col items-center justify-center gap-3 bg-paper p-6 text-center">
        <p className="text-[15px] text-muted">Quack can&apos;t reach its server just now.</p>
        <button
          type="button"
          onClick={() => {
            setLoadFailed(false);
            void load();
          }}
          className="btn btn-primary"
        >
          Try again
        </button>
      </main>
    );
  }
  if (!state) return <main aria-busy="true" className="flex h-dvh items-center justify-center bg-paper"><Duck state="idle" size={72} /></main>;

  const done = new Set(state.completedPromptIds);
  const prompt = state.prompts.find((candidate) => !done.has(candidate.id));
  const position = state.prompts.filter((candidate) => done.has(candidate.id)).length;

  if (!prompt) {
    return (
      <main className="flex min-h-dvh flex-col items-center justify-center bg-paper p-6">
        <div data-testid="calibration-done" className="flex w-full max-w-md flex-col items-center gap-4 text-center">
          <Duck state="resolved" size={84} />
          <h1 className="font-display text-2xl font-bold">All {state.prompts.length} saved</h1>
          <p className="text-[15px] leading-snug text-muted">Quack has what it needs, and won&apos;t ask again. When it can&apos;t quite read a line, it can look at how you write.</p>
          <button type="button" data-testid="calibration-finish" onClick={onDone} className="btn btn-primary btn-lg px-8">Start writing</button>
        </div>
      </main>
    );
  }

  if (!started && position === 0) {
    return (
      <main className="flex min-h-dvh flex-col items-center justify-center bg-paper p-6">
        <div className="flex w-full max-w-md flex-col items-center gap-4 text-center">
          <div className="bubble rounded-2xl bg-white px-4 py-2.5 text-[14px] shadow-sm ring-1 ring-ink/5">Before we begin, write a few expressions so I can learn your handwriting.</div>
          <Duck state="idle" size={84} />
          <h1 data-testid="calibration-intro" className="font-display text-2xl font-bold">First, let Quack get to know your handwriting.</h1>
          <p className="text-[15px] leading-snug text-muted">Write these expressions the way you normally would in your notes. Quack will use them to better recognize your writing.</p>
          <p className="text-[13px] leading-snug text-muted">They&apos;re saved privately to your account. When Quack can&apos;t settle a line, a couple of them go to Meta&apos;s AI API along with it, and are never shared with anyone else&apos;s work. You can delete them in Settings.</p>
          <button type="button" data-testid="calibration-start" onClick={() => setStarted(true)} className="btn btn-primary btn-lg px-8">Start</button>
        </div>
      </main>
    );
  }

  const next = async () => {
    if (savingRef.current) return;
    const area = boundsOfStrokes(notebook.strokes);
    const region = area && selectRegion(notebook.strokes, [area]);
    if (!region) return;
    savingRef.current = true;
    setSaving(true);
    setProblem(null);
    try {
      const updated = await saveSample(prompt.id, notebook.strokes, PAGE_WIDTH, renderSnapshot(region));
      // Not `clear`: undo must not bring the last sample back onto the next one.
      store.reset();
      setState(updated);
    } catch (error) {
      setProblem(await problemFrom(error));
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  return (
    <main className="flex h-dvh flex-col overflow-hidden bg-paper">
      <header className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-ink/5 p-3 sm:px-5">
        <div className="min-w-0 flex-1">
          <div
            role="progressbar"
            aria-label="Handwriting samples"
            aria-valuemin={0}
            aria-valuemax={state.prompts.length}
            aria-valuenow={position}
            data-testid="calibration-progress"
            className="mb-2 flex max-w-52 gap-1"
          >
            {state.prompts.map((candidate, index) => (
              <span key={candidate.id} className={`h-1.5 flex-1 rounded-full ${index < position ? "bg-duck-deep" : index === position ? "bg-duck" : "bg-ink/10"}`} />
            ))}
          </div>
          <p className="text-[13px] text-muted">{position + 1} of {state.prompts.length}. Copy this in your normal writing:</p>
          <p data-testid="calibration-prompt" className="font-display text-2xl font-bold tracking-tight sm:text-3xl">{prompt.display}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2 text-sm font-medium">
          <button type="button" aria-label="Undo" title="Undo" onClick={store.undo} disabled={!notebook.undoStack.length} className="flex size-11 items-center justify-center rounded-xl hover:bg-ink/5 disabled:opacity-30"><Undo2 size={18} aria-hidden /></button>
          <button type="button" data-testid="calibration-clear" onClick={() => store.reset()} disabled={!notebook.strokes.length} className="flex min-h-11 items-center gap-1.5 rounded-xl px-3 hover:bg-ink/5 disabled:opacity-30"><RotateCcw size={16} aria-hidden />Clear</button>
          <button type="button" data-testid="calibration-next" onClick={() => void next()} disabled={!notebook.strokes.length || saving} className="btn btn-primary"><Check size={16} aria-hidden />Continue</button>
        </div>
        {problem && <p role="alert" data-testid="calibration-problem" className="w-full text-sm text-alert">{problem}</p>}
      </header>
      <NotebookCanvas store={store} tool="pen" color="#262626" width={3} paper="grid" markers={NO_MARKERS} onDrawingChange={() => {}} />
    </main>
  );
}
