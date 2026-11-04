"use client";

import { useCallback, useEffect, useMemo, useReducer, useRef, useState, useSyncExternalStore } from "react";
import { analyze, askAboutSelection, clearMistakes, fetchHealth, issueFromError, requestExplanation, requestHint, sendVoice } from "@/lib/api";
import { activeMistakes, duckReducer, IMPORT_FAILED_TEXT, initialDuck, nextHelpLevel, SOLVING_TEXT, visibleState } from "@/lib/duckMachine";
import { changedInk, judgeReply } from "@/lib/freshness";
import { boundsOfStrokes, intersects, strokeBounds, unionBounds } from "@/lib/geometry";
import { mark, newRequestId } from "@/lib/latency";
import { isLasso, selectedWorking, strokesInLasso, type AskStanding, type LassoPoint, type ScreenRect } from "@/lib/lasso";
import { anchorBounds, anchorOnLine, isMostlyErased, linesHoldingMistakes, markersFor, replacements, rewrittenLine, strokesUnderMistakes, touchedBy, ERASED_LINE_MS, type ErasedLine, type LineAnchor } from "@/lib/mistakes";
import { eventsFrom, type LearningSink } from "@/lib/learning";
import { groupIntoLines } from "@/lib/lines";
import { getSessionId, type SeenMistakes } from "@/lib/persist";
import { keepCheck } from "@/lib/qaFixture";
import { AnalysisScheduler, type Capture } from "@/lib/scheduler";
import { attemptFor, hashStrokes, isWorthAnalyzing, lineId, linesToLookAgainAt, planImage, regionCovers, renderSnapshot, selectRegion, type Region } from "@/lib/snapshot";
import { playQuack, unlockAudio } from "@/lib/sound";
import { NotebookStore } from "@/lib/store";
import type { PenStroke } from "@/lib/types";
import { canRecord, startRecording, type Recording } from "@/lib/recorder";
import type { AnalyzeResponse, AskAction, AskResponse, Bounds, CheckContext, InkLine, LineIntent, TaskContext, Trigger } from "@/lib/types";

const RESOLVED_MS = 2200;
const NOTE_MS = 5000;
const MAX_IMPORT_EDGE = 2000;
// Far enough apart to hear as two.
const QUACK_GAP_MS = 350;
// Fake-model browser tests script a reading of the whole page, so they ask for it to be drawn.
function readsWholeWorking(): boolean {
  if (typeof localStorage !== "undefined" && localStorage.getItem("quack.incremental") === "1") return false;
  return process.env.NEXT_PUBLIC_QUACK_READ_ALL === "1";
}
const SAVE_ISSUES = {
  unreadable: { category: "storage", message: "Quack couldn't open your saved notes, so new writing won't be saved." },
  failed: { category: "storage", message: "Quack couldn't save your latest writing. It will keep trying, so keep this page open." },
  conflict: { category: "storage", message: "These notes changed in another tab, so this tab has stopped saving. Reload to continue." },
};

export type AnalysisTrace = {
  trigger: Trigger;
  version: number;
  snapshotMs: number;
  roundTripMs: number;
  result: AnalyzeResponse;
  // The reply was thrown away, as the ink it was about changed while it was on its way.
  dropped?: boolean;
  // How many written lines this check left out altogether.
  linesOutOfScope: number;
};

function readSetting(key: string, fallback: boolean): boolean {
  if (typeof localStorage === "undefined") return fallback;
  const stored = localStorage.getItem(key);
  return stored === null ? fallback : stored === "true";
}

// What the same notebook and duck need in order to work for an account, or for a practice question.
export type QuackOptions = {
  // Where the notes are saved. A practice page isn't saved at all.
  notebookKey?: string;
  persist?: boolean;
  // What checks and hints go under, in place of one for each page load.
  sessionId?: string;
  analyzeWith?: typeof analyze;
  // Draw every line each time, for a page whose lines all count together.
  readAllLines?: boolean;
  alwaysWatching?: boolean;
  learning?: LearningSink | null;
  // `trigger` says whether this answers the student asking for a check, or is one Quack started itself.
  onApplied?: (result: AnalyzeResponse, trigger: Trigger) => void;
  // A check that came to nothing, as on a timeout. Nothing else would say it has ended.
  onFailed?: (trigger: Trigger) => void;
  // Which strokes a mistake is on, as it's reported, for showing it again later.
  onMistakeInk?: (mistakeId: string, strokeIds: string[]) => void;
};

// Some working the student has circled to ask about. It's only a question: nothing here is in the notes.
export type AskSelection = {
  strokeIds: string[];
  polygon: LassoPoint[];
  screen: ScreenRect;
  level: 1 | 2;
  loading: boolean;
  reply: AskResponse | null;
  problem: string | null;
  tooMuch: boolean;
  // What was last asked, for what's worth offering next.
  lastAction: AskAction | null;
  // What's already known about what's circled, for what's worth offering first.
  standing: AskStanding;
};

const inkSignature = (strokes: readonly PenStroke[]) => strokes.map((stroke) => stroke.id).sort().join(",");

const NO_OPTIONS: QuackOptions = {};

export function useQuack(options: QuackOptions = NO_OPTIONS) {
  const { notebookKey, persist } = options;
  const store = useMemo(() => new NotebookStore({ key: notebookKey, persist }), [notebookKey, persist]);
  const optionsRef = useRef(options);
  useEffect(() => {
    optionsRef.current = options;
  });
  const checkSession = useCallback(() => optionsRef.current.sessionId ?? getSessionId(), []);
  const [duck, dispatch] = useReducer(duckReducer, initialDuck);
  const [monitoring, setMonitoring] = useState(true);
  const [muted, setMuted] = useState(false);
  const [trace, setTrace] = useState<AnalysisTrace | null>(null);
  const [recording, setRecording] = useState(false);
  // A check is under way, whatever the duck is doing. The duck only shows "checking" from idle, so while a
  // flagged line's fix was being checked on the iPad (2026-11-04), nothing said Quack was looking.
  const [checking, setChecking] = useState(false);
  const [lastTranscript, setLastTranscript] = useState<string | null>(null);
  const [importedImage, setImportedImage] = useState<string | null>(null);
  // What's marked comes from here. The ref is the same thing, for callbacks outside a render.
  const [anchorList, setAnchorList] = useState<ReadonlyMap<string, LineAnchor>>(new Map());
  // Decided after mount, because the server cannot know this and must render the same HTML.
  const [canTalk, setCanTalk] = useState(false);
  const [saveIssue, setSaveIssue] = useState<keyof typeof SAVE_ISSUES | null>(null);
  const [taskContext, setTaskContextState] = useState<TaskContext>("auto");
  const [selection, setSelection] = useState<AskSelection | null>(null);

  const duckRef = useRef(duck);
  const selectionRef = useRef<AskSelection | null>(null);
  const askRequest = useRef<AbortController | null>(null);
  // Help asked for by circling a known mistake, waiting for the duck to have it selected.
  const pendingHelp = useRef<{ id: string; action: "hint" | "explain" } | null>(null);
  // What the student has said this page and each line are for. Kept with the check, not in the notes.
  const checkContext = useRef<CheckContext>({ taskContext: "auto", lineIntents: {} });
  // What the page held when each mistake was reported, to tell a fix from a second opinion.
  const inkWhenFound = useRef(new Map<string, string>());
  // Where each known mistake's lines are, so a later check can always see them.
  const areas = useRef(new Map<string, Bounds | null>());
  // The strokes of the line each mistake is on. Writing there puts it on hold, and they're what's marked.
  const anchors = useRef(new Map<string, LineAnchor>());
  const erasedLines = useRef<ErasedLine[]>([]);
  // Mistakes on this page that have gone, by being fixed or erased. One coming back, as after an undo,
  // isn't news, so there's no quack, and nor is it counted again.
  const goneFromPage = useRef(new Set<string>());
  // Lines the server has read and agreed on, so they needn't go in the next image.
  const settled = useRef(new Set<string>());
  // Lines the verifier has found right, on two readings. An edit makes a new line, with a new id.
  const verified = useRef(new Set<string>());
  // How many times each line that couldn't be settled has been looked at again.
  const lookedAgain = useRef(new Map<string, number>());
  // The check in flight: what it showed the model, and the page as it was then.
  const lastCrop = useRef<{
    id: string;
    version: number;
    region: Region;
    strokes: readonly PenStroke[];
    snapshotMs: number;
    outOfView: string[];
    inkLines: InkLine[];
    attempt: number;
    mistakeLines: Record<string, string[]>;
  } | null>(null);
  // Each recent check by its id. Two can be under way at once, and each reply is judged against its own image.
  const crops = useRef(new Map<string, NonNullable<typeof lastCrop.current>>());
  // The check whose reply was last applied. With two under way, the newest capture isn't necessarily it.
  const appliedCrop = useRef<NonNullable<typeof lastCrop.current> | null>(null);
  const importSession = useRef("");
  const importCount = useRef(0);
  const importRequest = useRef<AbortController | null>(null);
  const recorder = useRef<Recording | null>(null);
  const talkHeld = useRef(false);
  const schedulerRef = useRef<AnalysisScheduler | null>(null);

  useEffect(() => {
    duckRef.current = duck;
  }, [duck]);

  const helpSession = useCallback(
    () => (duckRef.current.mistakeSource === "import" ? importSession.current : checkSession()),
    [checkSession],
  );

  useEffect(() => {
    // Settings live in localStorage, which only exists after mount.
    setMonitoring(optionsRef.current.alwaysWatching || readSetting("quack.monitoring", true));
    setMuted(readSetting("quack.muted", false));
    setCanTalk(canRecord());

    const capture = (trigger: Trigger): Capture | null => {
      const { strokes, version } = store.getState();
      const known = Object.keys(duckRef.current.seen);
      if (!strokes.length) {
        for (const [id, anchor] of anchors.current) erasedLines.current.push({ bounds: anchor.bounds, at: Date.now(), mistakeId: id });
        anchors.current.clear();
        dispatch({ type: "page_empty", trigger });
        store.takeDirty();
        schedulerRef.current?.forgetLastCheck();
        // Erasing isn't fixing, so it goes down as what it is.
        for (const id of known) optionsRef.current.learning?.record({ eventType: "mistake_invalidated", mistakeId: id, reason: "erased" });
        if (known.length) void clearMistakes(checkSession(), version, known).catch(() => {});
        return null;
      }

      // A mistake whose lines have gone was erased, and needs no model call to know that.
      const erased = known.filter((id) => {
        const anchor = anchors.current.get(id);
        if (anchor) return anchorBounds(anchor, strokes) === null || isMostlyErased(anchor, strokes);
        const area = areas.current.get(id);
        return area && !strokes.some((stroke) => intersects(strokeBounds(stroke), area));
      });
      if (erased.length) {
        for (const id of erased) {
          const anchor = anchors.current.get(id);
          if (anchor) erasedLines.current.push({ bounds: anchor.bounds, at: Date.now(), mistakeId: id });
          anchors.current.delete(id);
          goneFromPage.current.add(id);
          optionsRef.current.learning?.record({ eventType: "mistake_invalidated", mistakeId: id, reason: "erased" });
        }
        dispatch({ type: "mistakes_erased", ids: erased });
        schedulerRef.current?.forgetLastCheck();
        void clearMistakes(checkSession(), version, erased).catch(() => {});
      }

      const started = performance.now();
      const dirty = store.takeDirty();
      let focus = dirty;
      if (!focus.length && trigger === "manual") {
        const viewport = store.getViewport();
        const visible = viewport && strokes.some((stroke) => intersects(strokeBounds(stroke), viewport));
        focus = [visible ? viewport : strokeBounds(strokes.at(-1)!)];
      }
      const keep = known.filter((id) => !erased.includes(id)).flatMap((id) => {
        const area = areas.current.get(id);
        return area ? [area] : [];
      });
      if (!focus.length && !keep.length) return null;
      const region = selectRegion(strokes, focus, keep);
      if (!region || (trigger === "auto" && !isWorthAnalyzing(region))) {
        store.restoreDirty(dirty);
        return null;
      }
      const id = newRequestId();
      // Asked directly, it looks at everything again. A misreading two readings shared can then be put right.
      const plan = planImage(region, settled.current, trigger === "manual" || optionsRef.current.readAllLines || readsWholeWorking());
      mark("region_selected", id, { lines: region.lineCount, drawn: plan.image.lineCount });
      // Only the changed writing is owed a later check, not the rest of the viewport.
      const more = dirty.length > 0 && region.overflow.length > 0;
      if (more) store.restoreDirty(region.overflow);
      const image = renderSnapshot(plan.image);
      mark("image_rendered", id, { bytes: image.length });
      const outOfView = known.filter((id) => {
        const area = areas.current.get(id);
        return !area || !regionCovers(region, strokes, area);
      });
      lastCrop.current = { id, version, region, strokes, snapshotMs: performance.now() - started, outOfView, inkLines: plan.inkLines, attempt: attemptFor(plan.inkLines, lookedAgain.current), mistakeLines: linesHoldingMistakes(known.filter((id) => !outOfView.includes(id)), anchors.current, region.lines) };
      crops.current.set(id, lastCrop.current);
      for (const old of crops.current.keys()) if (crops.current.size > 4) crops.current.delete(old);
      return { id, version, hash: hashStrokes(region.strokes), image, restore: () => store.restoreDirty(dirty), more };
    };

    const scheduler = new AnalysisScheduler({
      getVersion: () => store.getState().version,
      capture,
      send: async (snapshot, trigger, signal) => {
        const started = performance.now();
        const own = (snapshot.id && crops.current.get(snapshot.id)) || lastCrop.current;
        const outOfView = own?.version === snapshot.version ? own.outOfView : [];
        const known = Object.keys(duckRef.current.seen).filter((id) => !outOfView.includes(id));
        mark("request_sent", snapshot.id, { trigger });
        const crop = own;
        const inkLines = crop && crop.id === snapshot.id ? crop.inkLines : undefined;
        const result = await (optionsRef.current.analyzeWith ?? analyze)(checkSession(), snapshot.version, snapshot.image, trigger, known, signal, outOfView, snapshot.id, inkLines, crop && crop.id === snapshot.id ? crop.attempt : 0, { ...checkContext.current, mistakeLineIds: crop && crop.id === snapshot.id ? crop.mistakeLines : {} });
        for (const line of result.settledLineIds) settled.current.add(line);
        for (const line of result.verifiedLineIds) verified.current.add(line);
        // Read once as one answer and once as another, so it isn't known to be right after all.
        for (const line of result.contestedLineIds ?? []) verified.current.delete(line);
        // It's forgotten these, or the reading didn't line up, so they get drawn next time.
        for (const line of result.unreadLineIds) settled.current.delete(line);
        mark("response_received", snapshot.id, {
          status: result.status,
          serverMs: result.timings.totalMs,
          stages: result.timings.stages,
          calls: result.timings.calls,
        });
        const checked: AnalysisTrace = {
          trigger,
          version: snapshot.version,
          snapshotMs: own?.snapshotMs ?? 0,
          roundTripMs: performance.now() - started,
          result,
          linesOutOfScope: Math.max(0, (crop?.region.pageLineCount ?? 0) - (crop?.region.lineCount ?? 0)),
        };
        setTrace(checked);
        keepCheck(checked);
        return result;
      },
      judge: (snapshot, result) => {
        const crop = (snapshot.id && crops.current.get(snapshot.id)) || lastCrop.current;
        if (!crop || crop.id !== snapshot.id) {
          return result.snapshotVersion === store.getState().version ? { result, complete: true } : null;
        }
        // It couldn't read this as it stood, and another look can. No verdict either way.
        if (result.reason === "lines_unread" || result.reason === "lines_misaligned") return null;
        const changes = changedInk(crop.strokes, store.getState().strokes);
        // The student was writing elsewhere in the image. Mistakes stand if their lines weren't
        // touched, but nothing the reply says was fixed can be trusted, since that ink may have changed.
        const judged = judgeReply(result, changes, crop.region.bands.map((band) => band.bounds), (mistake) => crop.region.lines.find((line) => line.id === mistake.lineId)?.bounds ?? null);
        if (!judged) setTrace((current) => current && { ...current, dropped: true });
        return judged;
      },
      onResult: (result, trigger) => {
        const crop = (result.requestId && crops.current.get(result.requestId)) || lastCrop.current;
        if (crop) appliedCrop.current = crop;
        let replaced: Record<string, string> = {};
        // Gone with not a stroke different, so there was nothing there to fix.
        const unchanged = new Set<string>();
        if (crop?.version === result.snapshotVersion) {
          const ink = inkSignature(crop.strokes);
          for (const id of result.resolvedMistakeIds) {
            if (inkWhenFound.current.get(id) === ink) unchanged.add(id);
            inkWhenFound.current.delete(id);
          }
          for (const mistake of result.mistakes) if (mistake.isNew) inkWhenFound.current.set(mistake.id, ink);
          replaced = replacements(result.mistakes, result.resolvedMistakeIds, anchors.current, crop.region.lines);
          for (const mistake of result.mistakes) if (mistake.isNew && goneFromPage.current.has(mistake.id)) replaced[mistake.id] ??= mistake.id;
          for (const id of result.resolvedMistakeIds) goneFromPage.current.add(id);
          // Something has been written where an erased mistake was. Say what became of it only
          // once two readings agree on that line, or a mistake has been confirmed there.
          erasedLines.current = erasedLines.current.filter((erased) => {
            if (Date.now() - erased.at > ERASED_LINE_MS) return false;
            const line = rewrittenLine(erased, crop.region.lines, Date.now());
            if (!line) return true;
            const wrong = result.mistakes.find((mistake) => mistake.lineId === line.id && mistake.isNew);
            if (wrong) {
              replaced[wrong.id] = wrong.id;
              return false;
            }
            // Whatever else is on the page, as other mistakes there have no bearing on this line.
            if (result.verifiedLineIds.includes(line.id)) {
              dispatch({ type: "rewritten" });
              // Written out again, and two readings say it's right. This time it is a fix.
              if (erased.mistakeId) optionsRef.current.learning?.record({ eventType: "correction_verified", mistakeId: erased.mistakeId, rewritten: true });
              return false;
            }
            return true;
          });
          for (const mistake of result.mistakes) {
            const anchor = anchorOnLine(mistake, crop.region.lines, crop.version);
            if (anchor) anchors.current.set(mistake.id, anchor);
            if (anchor) optionsRef.current.onMistakeInk?.(mistake.id, anchor.strokeIds);
            // With no line to tie it to, it's somewhere in what was just read.
            const everywhere = crop.region.bands.reduce<Bounds | null>((all, band) => unionBounds(all, band.bounds), null);
            if (anchor || !areas.current.get(mistake.id)) areas.current.set(mistake.id, anchor?.bounds ?? everywhere);
          }
        }
        mark("result_applied", result.requestId ?? undefined, {
          status: result.status,
          reason: result.reason,
          trigger,
          quack: result.intervention.shouldQuack,
          newMistakes: result.mistakes.filter((mistake) => mistake.isNew).map((mistake) => mistake.id),
          keptMistakes: result.mistakes.filter((mistake) => !mistake.isNew).map((mistake) => mistake.id),
          resolved: result.resolvedMistakeIds,
          undecided: result.undecidedMistakeIds,
        });
        for (const id of result.resolvedMistakeIds) {
          areas.current.delete(id);
          anchors.current.delete(id);
        }
        if (result.resolvedMistakeIds.length) schedulerRef.current?.forgetLastCheck();
        setAnchorList(new Map(anchors.current));
        dispatch({ type: "result", result, trigger, replaced });
        // This reply is what the student is being told, which can't be said of one that was dropped.
        const learning = optionsRef.current.learning;
        if (learning && !result.stale) {
          for (const event of eventsFrom(result, replaced, unchanged)) learning.record(event);
        }
        optionsRef.current.onApplied?.(result, trigger);
        // What couldn't be settled is owed another look, as nothing else would bring it back.
        // A reading with a different number of lines from the ink is about how they were grouped,
        // and reading them again wouldn't change that. Any mistake in it has been reported already.
        const linedUp = crop?.inkLines.length === result.lines.length;
        if (crop && crop.id === result.requestId && linedUp && !result.modelIssue && !result.stale) {
          // Only maths can hide a mistake. Words, crossings out and a line stopped part way through can't.
          const maths = crop.inkLines.filter((_, position) => result.lines[position].kind === "math");
          const owed = linesToLookAgainAt(maths, settled.current, lookedAgain.current);
          if (owed.length) {
            mark("line_look_again", result.requestId ?? undefined, { lines: owed });
            for (const line of owed) lookedAgain.current.set(line, (lookedAgain.current.get(line) ?? 0) + 1);
            store.restoreDirty(crop.region.lines.filter((line) => owed.includes(line.id)).map((line) => line.bounds));
            schedulerRef.current?.lookAgainLater();
          } else if (result.undecidedMistakeIds.length) {
            // No more looks are owed, so it won't say more until the line changes. That's worth saying.
            dispatch({ type: "changes_undecided", ids: result.undecidedMistakeIds });
          }
        }
      },
      onError: (error, trigger) => {
        dispatch({ type: "request_failed", issue: issueFromError(error), trigger });
        optionsRef.current.onFailed?.(trigger);
      },
      onBusyChange: (busy) => {
        setChecking(busy);
        dispatch({ type: busy ? "check_started" : "check_finished" });
      },
      debounceMs: Number(process.env.NEXT_PUBLIC_QUACK_DEBOUNCE_MS) || undefined,
      // Lessons and practice read every line on every check, so a newer check covers an older one. Only there
      // may two overlap. See scheduler.ts.
      overlap: Boolean(optionsRef.current.readAllLines),
    });
    scheduler.setEnabled(optionsRef.current.alwaysWatching || readSetting("quack.monitoring", true));
    schedulerRef.current = scheduler;
    let previous = store.getState().strokes;
    store.setEditListener(() => {
      const { strokes } = store.getState();
      const had = new Set(previous.map((stroke) => stroke.id));
      const has = new Set(strokes.map((stroke) => stroke.id));
      const removed = new Set(previous.filter((stroke) => !has.has(stroke.id)).map((stroke) => stroke.id));
      const added = strokes.filter((stroke) => !had.has(stroke.id)).map(strokeBounds);
      previous = strokes;
      // The ink an alert is about has changed, so the alert may no longer be true. Put it away
      // now, and let the next check say whether it's fixed. Other mistakes are left alone.
      for (const id of touchedBy(added, removed, anchors.current)) {
        mark("mistake_touched", id);
        dispatch({ type: "mistake_touched", id });
        // Undoing this edit brings back ink already checked, and the alert has to come back with it.
        scheduler.forgetLastCheck();
      }
      scheduler.notifyEdit();
    });
    store.setSaveListener(setSaveIssue);

    void store.hydrate().then(({ status, seen, unchecked }) => {
      // Saved ink arriving isn't an edit.
      previous = store.getState().strokes;
      if (status === "failed") setSaveIssue("unreadable");
      for (const [id, entry] of Object.entries(seen)) areas.current.set(id, entry.area);
      if (Object.keys(seen).length) {
        dispatch({ type: "restore", seen: Object.fromEntries(Object.entries(seen).map(([id, entry]) => [id, { dismissed: entry.dismissed }])) });
      }
      // An alert that was on screen has to come back, and needs a look at the page to do so.
      if (unchecked || Object.values(seen).some((entry) => !entry.dismissed)) scheduler.requestCheck();
    });

    void fetchHealth()
      .then((health) => {
        if (!health.modelConfigured) {
          dispatch({ type: "request_failed", issue: { category: "no_api_key", message: "Quack isn't connected to Meta's API yet." }, trigger: "auto" });
        }
      })
      .catch(() => dispatch({ type: "request_failed", issue: issueFromError(null), trigger: "auto" }));

    const unlock = () => unlockAudio();
    const flush = () => store.flush();
    const flushIfHidden = () => {
      if (document.visibilityState === "hidden") store.flush();
    };
    for (const name of ["pointerup", "touchend", "keydown"]) window.addEventListener(name, unlock);
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", flushIfHidden);
    return () => {
      scheduler.dispose();
      store.setEditListener(null);
      store.setSaveListener(null);
      for (const name of ["pointerup", "touchend", "keydown"]) window.removeEventListener(name, unlock);
      window.removeEventListener("pagehide", flush);
      document.removeEventListener("visibilitychange", flushIfHidden);
    };
  }, [store, checkSession]);

  // Keep what the student has been told alongside their notes, for after a reload.
  useEffect(() => {
    const seen: SeenMistakes = {};
    for (const [id, entry] of Object.entries(duck.seen)) {
      seen[id] = { dismissed: entry.dismissed, area: areas.current.get(id) ?? null };
    }
    store.setSeen(seen);
  }, [duck.seen, store]);

  // One sound per new mistake, however many times the component renders.
  const playedQuacks = useRef(0);
  useEffect(() => {
    const owed = duck.quackCount - playedQuacks.current;
    if (owed <= 0) return;
    playedQuacks.current = duck.quackCount;
    mark("alert_shown", duck.mistake?.id);
    if (muted) return;
    for (let count = 0; count < owed; count++) {
      setTimeout(() => {
        playQuack();
        mark("sound", duck.mistake?.id);
      }, count * QUACK_GAP_MS);
    }
  }, [duck.quackCount, duck.mistake?.id, muted]);

  const resolvedSeen = useRef(0);
  useEffect(() => {
    if (duck.resolvedCount > resolvedSeen.current) {
      resolvedSeen.current = duck.resolvedCount;
      mark("alert_cleared");
    }
  }, [duck.resolvedCount]);

  useEffect(() => {
    if (!duck.bubble || duck.state === "alert") return;
    const timer = setTimeout(() => dispatch({ type: "bubble_expired" }), duck.state === "resolved" ? RESOLVED_MS : NOTE_MS);
    return () => clearTimeout(timer);
  }, [duck.bubble, duck.state]);

  const setDrawing = useCallback((drawing: boolean) => {
    if (!drawing) mark("stroke_end");
    schedulerRef.current?.setDrawing(drawing);
  }, []);

  const toggleMonitoring = useCallback(() => {
    setMonitoring((current) => {
      const next = !current;
      localStorage.setItem("quack.monitoring", String(next));
      schedulerRef.current?.setEnabled(next);
      return next;
    });
  }, []);

  const toggleMuted = useCallback(() => {
    unlockAudio();
    setMuted((current) => {
      localStorage.setItem("quack.muted", String(!current));
      return !current;
    });
  }, []);

  const checkNow = useCallback(() => {
    unlockAudio();
    setImportedImage(null);
    schedulerRef.current?.checkNow();
  }, []);

  const explain = useCallback(
    async (question?: string) => {
      const model = duckRef.current;
      const mistake = model.mistake;
      if (!mistake || model.loadingHelp || model.suspended) return;
      dispatch({ type: "help_requested" });
      mark("hint_requested", mistake.id, { level: 3 });
      try {
        const reply = await requestExplanation(helpSession(), mistake.id, question);
        // The student may have fixed it while the reply was on its way.
        if (duckRef.current.mistake?.id !== mistake.id) return;
        dispatch({ type: "help_received", level: 3, text: reply.text, source: reply.source, issue: reply.modelIssue });
        mark("hint_shown", mistake.id, { level: 3, source: reply.source, serverMs: reply.totalMs });
      } catch (error) {
        if (duckRef.current.mistake?.id === mistake.id) dispatch({ type: "help_failed", issue: issueFromError(error) });
        else dispatch({ type: "help_settled" });
      }
    },
    [helpSession],
  );

  const askForHelp = useCallback(async () => {
    const model = duckRef.current;
    const mistake = model.mistake;
    if (!mistake || model.loadingHelp || model.suspended) return;
    const level = nextHelpLevel(model);
    if (level === 3) return explain();
    dispatch({ type: "help_requested" });
    mark("hint_requested", mistake.id, { level });
    try {
      const reply = await requestHint(helpSession(), mistake.id, level);
      if (duckRef.current.mistake?.id !== mistake.id) return;
      dispatch({ type: "help_received", level: reply.level, text: reply.text, source: reply.source, issue: reply.modelIssue });
      mark("hint_shown", mistake.id, { level, source: reply.source, serverMs: reply.totalMs });
    } catch (error) {
      // A mistake that has just been fixed is gone from the server too, and that's no failure.
      if (duckRef.current.mistake?.id === mistake.id) dispatch({ type: "help_failed", issue: issueFromError(error) });
      else dispatch({ type: "help_settled" });
    }
  }, [explain, helpSession]);

  // Help on a known mistake goes the usual way, once the duck has it selected, so its hints are one history.
  useEffect(() => {
    const waiting = pendingHelp.current;
    if (!waiting || duck.mistake?.id !== waiting.id) return;
    pendingHelp.current = null;
    if (waiting.action === "explain") void explain();
    else void askForHelp();
  }, [duck, explain, askForHelp]);

  // The same ink says something different now. What's agreed on is remembered, so this costs no reading.
  const lookAtItAgain = useCallback(() => {
    // After a reload there's no last crop to go by, so it's the whole page.
    const whole = boundsOfStrokes(store.getState().strokes);
    const again = (appliedCrop.current ?? lastCrop.current)?.region.lines.map((line) => line.bounds) ?? (whole ? [whole] : []);
    store.restoreDirty(again);
    schedulerRef.current?.forgetLastCheck();
    schedulerRef.current?.requestCheck();
  }, [store]);

  const setTaskContext = useCallback(
    (next: TaskContext) => {
      checkContext.current = { ...checkContext.current, taskContext: next };
      setTaskContextState(next);
      lookAtItAgain();
    },
    [lookAtItAgain],
  );

  const answerClarification = useCallback(
    (lineId: string, intent: LineIntent) => {
      checkContext.current = { ...checkContext.current, lineIntents: { ...checkContext.current.lineIntents, [lineId]: intent } };
      dispatch({ type: "clarified", text: intent === "solve" ? SOLVING_TEXT : undefined });
      lookAtItAgain();
    },
    [lookAtItAgain],
  );

  const dismissClarification = useCallback(() => dispatch({ type: "clarified" }), []);

  const updateSelection = useCallback((next: AskSelection | null) => {
    selectionRef.current = next;
    setSelection(next);
  }, []);

  const cancelSelection = useCallback(() => {
    askRequest.current?.abort();
    askRequest.current = null;
    updateSelection(null);
  }, [updateSelection]);

  // True if the lasso's still wanted. A tap is the student changing their mind, so it's back to writing.
  const finishLasso = useCallback(
    (polygon: LassoPoint[], screen: ScreenRect): boolean => {
      const strokeIds = strokesInLasso(store.getState().strokes, polygon);
      if (!strokeIds.length) {
        // A tap, or a loop around nothing, is just changing their mind.
        updateSelection(null);
        if (!isLasso(polygon)) return false;
        dispatch({ type: "say", text: "There's no writing inside that. Try circling your working." });
        return true;
      }
      const { strokes } = store.getState();
      const working = selectedWorking(strokes, strokeIds);
      const linesOnPage = new Set(groupIntoLines([...strokes]).map((line) => lineId(line.strokes)));
      const open = activeMistakes(duckRef.current);
      const knownId = strokesUnderMistakes(strokeIds, anchors.current, open.map((record) => record.mistake.id), linesOnPage);
      const record = open.find((candidate) => candidate.mistake.id === knownId);
      const allRight = working !== null && !working.partialLineIds.length && working.selectedLineIds.every((id) => verified.current.has(id));
      const standing: AskStanding = record
        ? { kind: "mistake", hintsSeen: record.assistance.filter((entry) => entry.level < 3).length, explained: record.assistance.some((entry) => entry.level === 3) }
        : { kind: allRight ? "verified" : "unknown" };
      updateSelection({ strokeIds, polygon, screen, level: 1, loading: false, reply: null, problem: null, tooMuch: working?.tooMuch ?? false, lastAction: null, standing });
      return true;
    },
    [store, updateSelection],
  );

  // True if it's gone over to the duck, and the panel has nothing more to do.
  const askAboutCircled = useCallback(
    async (action: AskAction, question?: string): Promise<boolean> => {
      const current = selectionRef.current;
      if (!current || current.loading || current.tooMuch) return false;
      const { strokes } = store.getState();
      const working = selectedWorking(strokes, current.strokeIds);
      if (!working) {
        updateSelection({ ...current, problem: "I can't find that writing any more." });
        return false;
      }
      const linesOnPage = new Set(groupIntoLines([...strokes]).map((line) => lineId(line.strokes)));
      const known = strokesUnderMistakes(current.strokeIds, anchors.current, activeMistakes(duckRef.current).map((record) => record.mistake.id), linesOnPage);
      if (known && (action === "hint" || action === "explain")) {
        pendingHelp.current = { id: known, action };
        unlockAudio();
        dispatch({ type: "select", id: known });
        updateSelection(null);
        return true;
      }
      const request = new AbortController();
      askRequest.current?.abort();
      askRequest.current = request;
      updateSelection({ ...current, loading: true, problem: null });
      const send = (drawAll: boolean) => {
        const plan = planImage(working.region, settled.current, drawAll);
        return askAboutSelection(
          {
            sessionId: checkSession(),
            action,
            question,
            level: current.level,
            image: plan.inkLines.some((line) => line.inImage) ? renderSnapshot(plan.image) : undefined,
            inkLines: plan.inkLines,
            selectedLineIds: working.selectedLineIds,
            partialLineIds: working.partialLineIds,
            mistakeId: known ?? undefined,
            ...checkContext.current,
          },
          request.signal,
        );
      };
      try {
        let reply = await send(false);
        // It's forgotten them, so they go again, this once, with the writing to read.
        if (reply.unreadLineIds.length) {
          for (const line of reply.unreadLineIds) settled.current.delete(line);
          reply = await send(true);
        }
        if (request.signal.aborted || selectionRef.current?.strokeIds !== current.strokeIds) return false;
        if (reply.verdict === "correct") for (const line of working.selectedLineIds) verified.current.add(line);
        updateSelection({ ...current, loading: false, reply, lastAction: action, level: reply.level === 1 ? 2 : current.level });
      } catch (error) {
        if (request.signal.aborted || selectionRef.current?.strokeIds !== current.strokeIds) return false;
        updateSelection({ ...current, loading: false, problem: issueFromError(error).message });
      }
      return false;
    },
    [store, checkSession, updateSelection],
  );

  // Whatever it's asking about, the question goes away if the ink does, as after an undo.
  const answerClarificationForCircled = useCallback(
    (lineId: string, intent: LineIntent) => {
      answerClarification(lineId, intent);
      const current = selectionRef.current;
      if (current) {
        updateSelection({ ...current, reply: null });
        void askAboutCircled("check");
      }
    },
    [answerClarification, askAboutCircled, updateSelection],
  );

  const selectMistake = useCallback((id: string) => {
    unlockAudio();
    mark("mistake_selected", id);
    dispatch({ type: "select", id });
  }, []);

  const tapDuck = useCallback(() => {
    unlockAudio();
    const model = duckRef.current;
    if (activeMistakes(model).length > 1 && (model.dismissed || model.suspended || !model.mistake || model.state === "idle")) dispatch({ type: "choose" });
    // What's on record may not be what's there now, so look at what is.
    else if (model.mistake && model.suspended) {
      dispatch({ type: "reopen" });
      schedulerRef.current?.checkNow();
    } else if (model.mistake && model.dismissed) dispatch({ type: "reopen" });
    // It came forward quietly after another was fixed, and already has hints to go back to.
    else if (model.mistake && model.assistance.length > 0 && (model.state === "idle" || model.state === "checking")) dispatch({ type: "reopen" });
    else if (model.mistake && model.assistance.length === 0) void askForHelp();
    else if (!model.mistake) schedulerRef.current?.checkNow();
  }, [askForHelp]);

  const dismiss = useCallback(() => {
    importRequest.current?.abort();
    setImportedImage(null);
    const model = duckRef.current;
    if (model.mistake && model.mistakeSource === "notebook" && !model.dismissed) {
      optionsRef.current.learning?.record({ eventType: "mistake_dismissed", mistakeId: model.mistake.id });
    }
    dispatch({ type: "dismiss" });
  }, []);

  // An alert is about the image it came from, so it goes with the image.
  const closeImport = useCallback(() => {
    importRequest.current?.abort();
    setImportedImage(null);
    if (duckRef.current.mistakeSource === "import") dispatch({ type: "dismiss" });
  }, []);

  const importFile = useCallback(async (file: File) => {
    let image: string;
    try {
      image = await downscaleImage(file);
    } catch {
      dispatch({ type: "say", text: IMPORT_FAILED_TEXT });
      return;
    }
    setImportedImage(image);
    // Each import is its own session, so an imported page and the live notebook can't resolve
    // each other's mistakes.
    importCount.current += 1;
    const sessionId = `${checkSession().slice(0, 40)}-import-${importCount.current}`;
    importRequest.current?.abort();
    const request = new AbortController();
    importRequest.current = request;
    dispatch({ type: "check_started" });
    try {
      const result = await analyze(sessionId, 1, image, "manual", [], request.signal);
      if (request.signal.aborted) return;
      setTrace({ trigger: "manual", version: 1, snapshotMs: 0, roundTripMs: result.timings.totalMs, result, linesOutOfScope: 0 });
      if (result.mistake) importSession.current = sessionId;
      dispatch({ type: "import_result", result });
    } catch (error) {
      if (!request.signal.aborted) dispatch({ type: "request_failed", issue: issueFromError(error), trigger: "manual" });
    } finally {
      dispatch({ type: "check_finished" });
    }
  }, [checkSession]);

  const startTalking = useCallback(async () => {
    if (talkHeld.current || recorder.current || !canRecord()) return;
    talkHeld.current = true;
    setRecording(true);
    try {
      const started = await startRecording(() => !talkHeld.current);
      if (started) recorder.current = started;
      else setRecording(false);
    } catch {
      talkHeld.current = false;
      setRecording(false);
      dispatch({ type: "say", text: "I need microphone access to hear you." });
    }
  }, []);

  const stopTalking = useCallback(async () => {
    talkHeld.current = false;
    setRecording(false);
    const active = recorder.current;
    if (!active) return;
    recorder.current = null;
    const wav = await active.stop();
    if (!wav) return;
    const model = duckRef.current;
    dispatch({ type: "help_requested" });
    try {
      const reply = await sendVoice(model.mistake ? helpSession() : checkSession(), model.mistake?.id ?? null, wav);
      setLastTranscript(reply.transcript || null);
      if (reply.text && reply.level) {
        dispatch({ type: "help_received", level: reply.level, text: reply.text, source: reply.source ?? "model", issue: null });
      } else {
        dispatch({ type: "help_settled" });
        if (reply.modelIssue) dispatch({ type: "say", text: reply.modelIssue.message });
        else if (reply.intent === "check") schedulerRef.current?.checkNow();
        else if (reply.intent === "hint" || reply.intent === "explain") {
          dispatch({ type: "say", text: "I haven't spotted a mistake here. Want me to check your work?" });
        } else dispatch({ type: "say", text: "I can only help with the working on this page." });
      }
    } catch (error) {
      dispatch({ type: "help_failed", issue: issueFromError(error) });
    }
  }, [helpSession, checkSession]);

  const notebook = useSyncExternalStore(store.subscribe, store.getState, store.getState);
  const markers = useMemo(() => markersFor(duck, anchorList, notebook.strokes), [duck, anchorList, notebook.strokes]);
  const liveSelection = useMemo(() => {
    if (!selection) return null;
    const ids = new Set(notebook.strokes.map((stroke) => stroke.id));
    return selection.strokeIds.every((id) => ids.has(id)) ? selection : null;
  }, [selection, notebook.strokes]);

  return {
    store,
    duck,
    duckAppearance: visibleState(duck.state, muted),
    monitoring,
    muted,
    checking,
    trace,
    recording,
    lastTranscript,
    importedImage,
    saveIssue: saveIssue ? SAVE_ISSUES[saveIssue] : null,
    markers,
    canTalk,
    taskContext,
    setTaskContext,
    answerClarification,
    dismissClarification,
    selection: liveSelection,
    // What was circled has gone, as after an undo, so there's nothing left to ask about.
    selectionGone: Boolean(selection) && !liveSelection,
    finishLasso,
    cancelSelection,
    askAboutCircled,
    answerClarificationForCircled,
    setDrawing,
    toggleMonitoring,
    toggleMuted,
    checkNow,
    askForHelp,
    explain,
    tapDuck,
    selectMistake,
    dismiss,
    closeImport,
    importFile,
    startTalking,
    stopTalking,
  };
}

async function downscaleImage(file: File): Promise<string> {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, MAX_IMPORT_EDGE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  const context = canvas.getContext("2d");
  if (!context) throw new Error("2D canvas unavailable");
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  return canvas.toDataURL("image/jpeg", 0.9);
}
