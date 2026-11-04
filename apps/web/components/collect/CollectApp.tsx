"use client";

import type { Marker } from "@/lib/mistakes";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { boundsOfStrokes } from "@/lib/geometry";
import prompts from "@/lib/collectionPrompts.json";
import { renderSnapshot, selectRegion } from "@/lib/snapshot";
import { NotebookStore } from "@/lib/store";
import { NotebookCanvas } from "../notebook/NotebookCanvas";

type Prompt = (typeof prompts)[number];
type Sample = Prompt & { image: string };

// Collects labelled samples for evaluation. Nothing leaves this page: the contributor
// downloads a file and hands it over.
const NO_MARKERS: Marker[] = [];

export function CollectApp() {
  const [consent, setConsent] = useState(false);
  const [contributor, setContributor] = useState("");
  const [started, setStarted] = useState(false);
  const [index, setIndex] = useState(0);
  const samples = useRef<Sample[]>([]);
  const [saved, setSaved] = useState(0);
  const store = useMemo(() => new NotebookStore({ persist: false }), []);
  const notebook = useSyncExternalStore(store.subscribe, store.getState, store.getState);
  const prompt = prompts[index];
  const finished = index >= prompts.length;

  const next = () => {
    if (!prompt) return;
    const area = boundsOfStrokes(notebook.strokes);
    const region = area && selectRegion(notebook.strokes, [area]);
    if (region) {
      samples.current.push({ ...prompt, image: renderSnapshot(region) });
      setSaved(samples.current.length);
    }
    // Not `clear`: undo must not bring the last sample back onto the next prompt.
    store.reset();
    setIndex((current) => current + 1);
  };

  // Samples live only in this page until they're downloaded.
  useEffect(() => {
    if (!saved) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [saved]);

  const download = () => {
    const bundle = { consent: true, contributor: contributor.trim(), collectedAt: new Date().toISOString(), samples: samples.current };
    const url = URL.createObjectURL(new Blob([JSON.stringify(bundle)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `quack-samples-${contributor.trim() || "anonymous"}.json`;
    link.click();
    URL.revokeObjectURL(url);
  };

  if (!started) {
    return (
      <main className="mx-auto flex min-h-dvh max-w-lg flex-col justify-center gap-4 p-6 text-[15px] leading-relaxed">
        <h1 className="font-display text-2xl font-bold">Help test Quack</h1>
        <p>You&apos;ll copy {prompts.length} short pieces of maths in your own handwriting, some deliberately wrong. It takes about ten minutes, and you can stop whenever you like.</p>
        <p className="text-muted">
          Your handwriting images are used to measure how well Quack reads writing, and may be sent to Meta&apos;s AI API for that purpose. Please don&apos;t write anything other than the prompts. Nothing is uploaded from this page: at the end you download a file and choose whether to share it.
        </p>
        <label className="flex flex-col gap-1 text-sm font-medium">
          A nickname, not your real name
          <input data-testid="contributor" value={contributor} maxLength={30} onChange={(event) => setContributor(event.target.value.replace(/[^\w-]/g, ""))} className="rounded-lg bg-ink/5 px-3 py-2 font-normal focus:outline-none" />
        </label>
        <label className="flex items-start gap-2">
          <input data-testid="consent" type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} className="mt-1" />
          <span>I agree to my handwriting being used to evaluate Quack, as described above.</span>
        </label>
        <button data-testid="start" type="button" disabled={!consent || !contributor.trim()} onClick={() => setStarted(true)} className="self-start rounded-lg bg-ink px-4 py-2 font-semibold text-paper disabled:opacity-30">
          Start
        </button>
      </main>
    );
  }

  return (
    <main className="flex h-dvh flex-col overflow-hidden bg-paper">
      <header className="flex shrink-0 items-center gap-3 border-b border-ink/5 p-3">
        {finished ? (
          <p className="flex-1 font-medium">All done. {saved} samples saved.</p>
        ) : (
          <div className="min-w-0 flex-1">
            <p className="text-[13px] text-muted">{index + 1} of {prompts.length}. Copy this, line by line, in your normal writing:</p>
            <div data-testid="prompt" className="font-mono text-lg font-semibold">
              {prompt.lines.map((line, lineIndex) => (
                <div key={lineIndex}>{line}</div>
              ))}
            </div>
          </div>
        )}
        <div className="flex shrink-0 gap-2 text-sm font-medium">
          {!finished && (
            <>
              <button type="button" onClick={store.undo} className="rounded-lg px-3 py-2 hover:bg-ink/5">Undo</button>
              <button data-testid="next" type="button" onClick={next} disabled={!notebook.strokes.length} className="rounded-lg bg-duck px-3 py-2 disabled:opacity-40">Save &amp; next</button>
            </>
          )}
          <button data-testid="download" type="button" onClick={download} disabled={!saved} className="rounded-lg bg-ink px-3 py-2 text-paper disabled:opacity-30">Download {saved}</button>
        </div>
      </header>
      <NotebookCanvas store={store} tool="pen" color="#262626" width={3} paper="plain" markers={NO_MARKERS} onDrawingChange={() => {}} />
    </main>
  );
}
