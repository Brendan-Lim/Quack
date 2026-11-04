// Automatic checking under realistic timing: a model that takes seconds, and a student who keeps
// writing. These reproduce the reported failures, where nothing was checked until the student
// pressed Check my work.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ALERT_TEXT, CHECKING_CHANGE_TEXT, duckReducer, initialDuck, MANUAL_UNCERTAIN_TEXT, RESOLVED_TEXT } from "@/lib/duckMachine";
import { changedInk, judgeFreshness, touchesBand } from "@/lib/freshness";
import { AnalysisScheduler, DEFAULT_DEBOUNCE_MS, LOOK_AGAIN_MS, RequestTimedOut, type Capture, type Judgement } from "@/lib/scheduler";
import { attemptFor, linesToLookAgainAt, MAX_LINE_RECHECKS } from "@/lib/snapshot";
import type { AnalyzeResponse, Bounds, PenStroke, Trigger } from "@/lib/types";

function reply(overrides: Partial<AnalyzeResponse> = {}): AnalyzeResponse {
  return {
    snapshotVersion: 1,
    requestId: null,
    status: "no_error",
    mistake: null,
    mistakes: [],
    intervention: { shouldQuack: false, showHintImmediately: false },
    resolvedMistakeIds: [],
    reason: null,
    stale: false,
    modelIssue: null,
    lines: [],
    settledLineIds: [],
    unreadLineIds: [], verifiedLineIds: [], undecidedMistakeIds: [],
    clarification: null,
    reminder: null,
    timings: { interpretMs: 0, verifyMs: 0, totalMs: 0, modelRequests: 1, costUsd: 0, cacheHit: false, stages: {}, calls: [] },
    ...overrides,
  };
}

const MODEL_MS = 4000;

describe("automatic checking while the student keeps writing", () => {
  let version: number;
  let delivered: { result: AnalyzeResponse; trigger: Trigger }[];
  let errors: unknown[];
  let sends: { trigger: Trigger; signal: AbortSignal }[];
  let verdict: (capture: Capture, result: AnalyzeResponse) => Judgement;
  let scheduler: AnalysisScheduler;

  beforeEach(() => {
    vi.useFakeTimers();
    version = 0;
    delivered = [];
    errors = [];
    sends = [];
    verdict = (_capture, result) => ({ result, complete: true });
    scheduler = new AnalysisScheduler({
      getVersion: () => version,
      capture: () => ({ version, hash: `h${version}`, image: "data:", restore: () => {} }),
      // A model that takes four seconds, like the measured low-effort reading.
      send: (capture, trigger, signal) => {
        sends.push({ trigger, signal });
        return new Promise((resolve, reject) => {
          const timer = setTimeout(() => resolve(reply({ snapshotVersion: capture.version, status: "confirmed_error" })), MODEL_MS);
          signal.addEventListener("abort", () => {
            clearTimeout(timer);
            reject(new DOMException("aborted", "AbortError"));
          });
        });
      },
      judge: (capture, result) => verdict(capture, result),
      onResult: (result, trigger) => delivered.push({ result, trigger }),
      onError: (error) => errors.push(error),
      onBusyChange: () => {},
    });
  });

  afterEach(() => {
    scheduler.dispose();
    vi.useRealTimers();
  });

  it("looks again at the same ink when asked, after a wait, and once", async () => {
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS + MODEL_MS);
    expect(sends).toHaveLength(1);
    scheduler.lookAgainLater();
    await vi.advanceTimersByTimeAsync(LOOK_AGAIN_MS - 1);
    expect(sends).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(MODEL_MS + 1);
    expect(sends).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(LOOK_AGAIN_MS * 3);
    expect(sends).toHaveLength(2);
  });

  it("an edit doesn't wait for a look that was due later", async () => {
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS + MODEL_MS);
    scheduler.lookAgainLater();
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS);
    expect(sends).toHaveLength(2);
  });

  // One stroke: pen down, a short stroke, pen up, and the notebook changes.
  async function writeStroke() {
    scheduler.setDrawing(true);
    await vi.advanceTimersByTimeAsync(150);
    version += 1;
    scheduler.setDrawing(false);
    scheduler.notifyEdit();
  }

  it("checks after a pause of about 800 ms, with no button", async () => {
    expect(DEFAULT_DEBOUNCE_MS).toBe(800);
    await writeStroke();
    await vi.advanceTimersByTimeAsync(799);
    expect(sends).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(sends).toEqual([expect.objectContaining({ trigger: "auto" })]);
    await vi.advanceTimersByTimeAsync(MODEL_MS);
    expect(delivered).toHaveLength(1);
  });

  it("a student who keeps writing still gets answers (this starved before)", async () => {
    // A finished line, a pause long enough for a check to start, then the next line, a stroke a second.
    await writeStroke();
    await vi.advanceTimersByTimeAsync(1000);
    for (let i = 0; i < 12; i++) {
      await writeStroke();
      await vi.advanceTimersByTimeAsync(850);
    }
    // The reply about the first line was judged still true for that line, so it was delivered.
    expect(sends.every((send) => !send.signal.aborted)).toBe(true);
    expect(delivered.length).toBeGreaterThanOrEqual(2);
  });

  it("the next check starts as soon as a slow one finishes, if the pause has already passed", async () => {
    await writeStroke();
    await vi.advanceTimersByTimeAsync(800);
    expect(sends).toHaveLength(1);
    await writeStroke();
    // The pause after this edit ends long before the model answers.
    await vi.advanceTimersByTimeAsync(MODEL_MS - 150 - 1);
    expect(sends).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(2);
    expect(sends).toHaveLength(2);
  });

  it("a reply about ink that has since changed is dropped, and the change is checked next", async () => {
    verdict = (capture, result) => (capture.version === version ? { result, complete: true } : null);
    await writeStroke();
    await vi.advanceTimersByTimeAsync(800);
    await writeStroke();
    await vi.advanceTimersByTimeAsync(MODEL_MS);
    expect(delivered).toHaveLength(0);
    expect(sends).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(MODEL_MS);
    expect(delivered).toHaveLength(1);
  });

  it("pressing Check my work repeatedly sends one request", async () => {
    await writeStroke();
    scheduler.checkNow();
    scheduler.checkNow();
    scheduler.checkNow();
    await vi.advanceTimersByTimeAsync(MODEL_MS);
    expect(sends.map((send) => send.trigger)).toEqual(["manual"]);
    expect(delivered.map((entry) => entry.trigger)).toEqual(["manual"]);
  });

  it("pressing Check my work during an automatic check answers with that check", async () => {
    await writeStroke();
    await vi.advanceTimersByTimeAsync(800);
    scheduler.checkNow();
    await vi.advanceTimersByTimeAsync(MODEL_MS);
    expect(sends.map((send) => send.trigger)).toEqual(["auto"]);
    expect(delivered.map((entry) => entry.trigger)).toEqual(["manual"]);
  });

  it("a request that hangs is abandoned, reported, and doesn't block the next check", async () => {
    scheduler.dispose();
    const pending: AbortSignal[] = [];
    scheduler = new AnalysisScheduler({
      getVersion: () => version,
      capture: () => ({ version, hash: `h${version}`, image: "data:", restore: () => {} }),
      send: (_capture, _trigger, signal) => {
        pending.push(signal);
        return new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))));
      },
      onResult: () => {},
      onError: (error) => errors.push(error),
      onBusyChange: () => {},
      requestTimeoutMs: 10_000,
    });
    await writeStroke();
    await vi.advanceTimersByTimeAsync(800);
    await writeStroke();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(errors[0]).toBeInstanceOf(RequestTimedOut);
    expect(pending[0].aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(800);
    expect(pending).toHaveLength(2);
  });

  it("monitoring off stops checking and cancels, and on again checks what was written", async () => {
    await writeStroke();
    await vi.advanceTimersByTimeAsync(800);
    scheduler.setEnabled(false);
    expect(sends[0].signal.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(sends).toHaveLength(1);
    // Nothing new was written: what the cancelled check was looking at is still owed a check.
    scheduler.setEnabled(true);
    await vi.advanceTimersByTimeAsync(800);
    expect(sends).toHaveLength(2);
  });

  it("a reply arriving mid-stroke waits for the stroke, which may be the correction", async () => {
    verdict = (capture, result) => (capture.version === version ? { result, complete: true } : null);
    await writeStroke();
    await vi.advanceTimersByTimeAsync(800);
    scheduler.setDrawing(true);
    await vi.advanceTimersByTimeAsync(MODEL_MS + 500);
    expect(delivered).toHaveLength(0);
    // As the canvas does it: pen up, then the stroke joins the notebook, in one go.
    version += 1;
    scheduler.setDrawing(false);
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(0);
    expect(delivered).toHaveLength(0);
  });

  it("a check owed after a reload waits a full pause, so restored state can arrive first", async () => {
    scheduler.requestCheck();
    await vi.advanceTimersByTimeAsync(799);
    expect(sends).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(sends).toHaveLength(1);
  });

  it("a stroke that lasts longer than the pause still isn't interrupted by a check", async () => {
    await writeStroke();
    scheduler.setDrawing(true);
    await vi.advanceTimersByTimeAsync(5000);
    expect(sends).toHaveLength(0);
    version += 1;
    scheduler.setDrawing(false);
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(800);
    expect(sends).toHaveLength(1);
  });
});

describe("a pen that never comes up", () => {
  it("doesn't stop checking for good once the watchdog fires", async () => {
    vi.useFakeTimers();
    const errors: unknown[] = [];
    let busy = false;
    const scheduler = new AnalysisScheduler({
      getVersion: () => 1,
      capture: () => ({ version: 1, hash: "h", image: "data:", restore: () => {} }),
      send: () => new Promise((resolve) => setTimeout(() => resolve(reply()), 1000)),
      onResult: () => {},
      onError: (error) => errors.push(error),
      onBusyChange: (value) => (busy = value),
      requestTimeoutMs: 5000,
    });
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(800);
    expect(busy).toBe(true);
    // The reply arrives with the pen down, and the event that lifts it never comes.
    scheduler.setDrawing(true);
    await vi.advanceTimersByTimeAsync(5000);
    expect(errors[0]).toBeInstanceOf(RequestTimedOut);
    expect(busy).toBe(false);
    scheduler.dispose();
    vi.useRealTimers();
  });
});

describe("a check that fails after the student has stopped writing", () => {
  const issue = (category: string) => ({ category, message: "m" });
  let sends: number;
  let reply_: () => Promise<AnalyzeResponse>;
  let scheduler: AnalysisScheduler;

  beforeEach(() => {
    vi.useFakeTimers();
    sends = 0;
    scheduler = new AnalysisScheduler({
      getVersion: () => 1,
      capture: () => ({ version: 1, hash: "h", image: "data:", restore: () => {} }),
      send: () => {
        sends += 1;
        return reply_();
      },
      onResult: () => {},
      onError: () => {},
      onBusyChange: () => {},
    });
  });

  afterEach(() => {
    scheduler.dispose();
    vi.useRealTimers();
  });

  it("is tried again with no new edit, a limited number of times", async () => {
    reply_ = async () => reply({ status: "uncertain", modelIssue: issue("timeout") });
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(800);
    expect(sends).toBe(1);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(sends).toBe(3);
  });

  it("tries again soon after a request that got no answer, as on the iPad's hotspot", async () => {
    // Round 4: two of these in a row, then 2 s and 6 s waits, held a finished answer back by about 10 s.
    reply_ = async () => {
      if (sends <= 2) throw new TypeError("Load failed");
      return reply();
    };
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(800);
    expect(sends).toBe(1);
    await vi.advanceTimersByTimeAsync(800);
    expect(sends).toBe(2);
    await vi.advanceTimersByTimeAsync(2600);
    expect(sends).toBe(3);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(sends).toBe(3);
  });

  it("waits as before after a failure the server reported", async () => {
    reply_ = () => Promise.reject(Object.assign(new Error("bad gateway"), { status: 502 }));
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(800);
    expect(sends).toBe(1);
    await vi.advanceTimersByTimeAsync(1500);
    expect(sends).toBe(1);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(sends).toBe(3);
  });

  it("stops retrying once one gets through", async () => {
    reply_ = async () => (sends === 1 ? reply({ status: "uncertain", modelIssue: issue("server_error") }) : reply());
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(sends).toBe(2);
  });

  it("isn't retried when the answer would be the same: no key, no budget", async () => {
    for (const category of ["auth", "budget_exceeded", "no_api_key", "invalid_request"]) {
      sends = 0;
      reply_ = async () => reply({ status: "uncertain", modelIssue: issue(category) });
      scheduler.notifyEdit();
      await vi.advanceTimersByTimeAsync(60_000);
      expect(sends).toBe(1);
    }
  });

  it("is retried after a network failure, but not after a refusal", async () => {
    reply_ = () => Promise.reject(new TypeError("offline"));
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(60_000);
    // No answer at all gets one more try than a failure the server reported. See CONNECTION_RETRY_DELAYS_MS.
    expect(sends).toBe(4);
    sends = 0;
    reply_ = () => Promise.reject(Object.assign(new Error("bad"), { status: 400 }));
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(sends).toBe(1);
  });
});

let nextId = 0;
function inkAt(y: number, x = 100, height = 30): PenStroke {
  nextId += 1;
  return {
    id: `k${nextId}`,
    color: "#000",
    width: 2,
    createdAt: nextId,
    points: [
      { x, y, timestamp: 0 },
      { x: x + 30, y: y + height, timestamp: 1 },
    ],
  };
}
const band = (minY: number, maxY: number): Bounds => ({ minX: 0, maxX: 1000, minY, maxY });

describe("which part of a reply still applies", () => {
  const line1 = band(100, 140);
  const farLine = band(400, 440);

  it("all of it when nothing changed, or the changes are outside the image and away from the mistake", () => {
    const before = [inkAt(100)];
    expect(judgeFreshness(changedInk(before, before), [line1], { spot: line1 })).toBe("current");
    expect(judgeFreshness(changedInk(before, [...before, inkAt(600)]), [line1], { spot: line1 })).toBe("current");
    expect(judgeFreshness(changedInk(before, [...before, inkAt(600)]), [line1], null)).toBe("current");
  });

  it("the alert only, when the student is writing elsewhere in the image", () => {
    const before = [inkAt(100), inkAt(400)];
    const after = [...before, inkAt(405, 500)];
    expect(judgeFreshness(changedInk(before, after), [line1, farLine], { spot: line1 })).toBe("alert_only");
  });

  it("nothing, when the mistake's own line changed", () => {
    const before = [inkAt(100)];
    // Appended to the right of the line: outside its old bounds, but the same line.
    const after = [...before, inkAt(105, 700)];
    expect(judgeFreshness(changedInk(before, after), [line1], { spot: line1 })).toBe("stale");
  });

  it("nothing, when part of the mistake was erased", () => {
    const wrong = inkAt(100);
    const kept = inkAt(100, 300);
    expect(judgeFreshness(changedInk([wrong, kept], [kept]), [line1], { spot: line1 })).toBe("stale");
  });

  it("nothing, when a line is written just below, which may be the student's own correction", () => {
    const before = [inkAt(100)];
    // Outside the image altogether, but it might answer the mistake.
    const after = [...before, inkAt(150)];
    expect(judgeFreshness(changedInk(before, after), [line1], { spot: line1 })).toBe("stale");
  });

  it("nothing, when the line the step came from was changed", () => {
    const before = [inkAt(100), inkAt(50)];
    const after = [...before, inkAt(52, 400)];
    expect(judgeFreshness(changedInk(before, after), [line1, band(50, 90)], { spot: line1 })).toBe("stale");
  });

  it("nothing, when an earlier line in the image changed, such as a definition far above", () => {
    const before = [inkAt(100), inkAt(400)];
    const after = [...before, inkAt(102, 500)];
    expect(judgeFreshness(changedInk(before, after), [line1, farLine], { spot: farLine })).toBe("stale");
  });

  it("nothing, for small writing on the next ruled lines, however small the writing", () => {
    const tiny = band(100, 115);
    const before = [inkAt(100, 100, 15)];
    expect(judgeFreshness(changedInk(before, [...before, inkAt(144, 100, 15)]), [tiny], { spot: tiny })).toBe("stale");
    expect(judgeFreshness(changedInk(before, [...before, inkAt(188, 100, 15)]), [tiny], { spot: tiny })).toBe("stale");
  });

  it("nothing, when a mistake can't be located and the page has changed", () => {
    const before = [inkAt(100)];
    expect(judgeFreshness(changedInk(before, [...before, inkAt(900)]), [line1], { spot: null })).toBe("stale");
  });

  it("nothing, when the image changed and there was no mistake to keep", () => {
    const before = [inkAt(100)];
    expect(judgeFreshness(changedInk(before, [...before, inkAt(110, 500)]), [line1], null)).toBe("stale");
  });

  it("treats a mark just above or below a line as part of it", () => {
    expect(touchesBand([band(141, 150)], line1)).toBe(true);
    expect(touchesBand([band(160, 170)], line1)).toBe(false);
  });
});

describe("editing a flagged line", () => {
  const mistake = {
    id: "m_wrong",
    category: "arithmetic",
    errorType: "arithmetic_error",
    operationType: "arithmetic",
    previousStep: "2 * 3",
    currentStep: "5",
    verified: true,
    region: { yTop: 0.1, yBottom: 0.4, line: 0, lineCount: 1 },
    lineId: null,
    problemId: null,
    parsedOperation: "",
    isNew: false,
  };
  const alerted = () =>
    duckReducer(initialDuck, {
      type: "result",
      trigger: "auto",
      result: reply({ status: "confirmed_error", mistake, intervention: { shouldQuack: true, showHintImmediately: false } }),
    });

  it("puts the alert away at once, without calling it fixed", () => {
    const touched = duckReducer(alerted(), { type: "mistake_touched", id: mistake.id });
    expect(touched.suspended).toBe(true);
    expect(touched.bubble).toBeNull();
    expect(touched.state).toBe("idle");
    expect(touched.resolvedCount).toBe(0);
    // Still known, so the server is still told not to quack about it again.
    expect(touched.seen[mistake.id]).toBeDefined();
    expect(touched.mistake?.id).toBe(mistake.id);
  });

  it("says it's fixed when a check confirms it, and only then", () => {
    let model = duckReducer(alerted(), { type: "mistake_touched", id: mistake.id });
    model = duckReducer(model, { type: "result", trigger: "auto", result: reply({ status: "uncertain" }) });
    expect(model.state).toBe("idle");
    expect(model.mistake?.id).toBe(mistake.id);
    model = duckReducer(model, { type: "result", trigger: "auto", result: reply({ resolvedMistakeIds: [mistake.id] }) });
    expect(model.state).toBe("resolved");
    expect(model.bubble).toBe(RESOLVED_TEXT);
  });

  it("brings the alert back, silently, when the same mistake is still there", () => {
    let model = duckReducer(alerted(), { type: "mistake_touched", id: mistake.id });
    model = duckReducer(model, {
      type: "result",
      trigger: "auto",
      result: reply({ status: "confirmed_error", mistake, intervention: { shouldQuack: false, showHintImmediately: false } }),
    });
    expect(model.state).toBe("alert");
    expect(model.bubble).toBe(ALERT_TEXT);
    expect(model.suspended).toBe(false);
    expect(model.quackCount).toBe(1);
  });

  it("keeps a hint the student is following open while they fix it", () => {
    let model = alerted();
    model = duckReducer(model, { type: "help_requested" });
    model = duckReducer(model, { type: "help_received", level: 1, text: "Check that multiplication again.", source: "template", issue: null });
    const touched = duckReducer(model, { type: "mistake_touched", id: mistake.id });
    expect(touched.state).toBe("hint");
    expect(touched.suspended).toBe(true);
  });

  it("tapping the duck doesn't bring back an alert about what's no longer written", () => {
    const touched = duckReducer(alerted(), { type: "mistake_touched", id: mistake.id });
    const reopened = duckReducer(touched, { type: "reopen" });
    expect(reopened.state).toBe("idle");
    expect(reopened.suspended).toBe(true);
    expect(reopened.bubble).toBe(CHECKING_CHANGE_TEXT);
  });

  it("a different known mistake can show while this one is put away", () => {
    const touched = duckReducer(alerted(), { type: "mistake_touched", id: mistake.id });
    const other = { ...mistake, id: "m_other" };
    const model = duckReducer(touched, {
      type: "result",
      trigger: "auto",
      result: reply({ status: "confirmed_error", mistake: other }),
    });
    expect(model.mistake?.id).toBe("m_other");
    expect(model.state).toBe("alert");
    expect(model.quackCount).toBe(1);
  });

  it("brings the alert back when a reading still finds it", () => {
    const touched = duckReducer(alerted(), { type: "mistake_touched", id: mistake.id });
    const model = duckReducer(touched, { type: "result", trigger: "auto", result: reply({ status: "uncertain", reason: "fix_disputed" }) });
    expect(model.state).toBe("alert");
    expect(model.quackCount).toBe(1);
  });

  it("doesn't bring it back because of a disagreement about something else", () => {
    const touched = duckReducer(alerted(), { type: "mistake_touched", id: mistake.id });
    for (const reason of ["readings_disagree", "fix_unconfirmed"]) {
      const model = duckReducer(touched, { type: "result", trigger: "auto", result: reply({ status: "uncertain", reason }) });
      expect(model.state).toBe("idle");
      expect(model.suspended).toBe(true);
    }
  });

  it("Check my work never says it looks right with a mistake still held", () => {
    const touched = duckReducer(alerted(), { type: "mistake_touched", id: mistake.id });
    const model = duckReducer(touched, { type: "result", trigger: "manual", result: reply({ status: "no_error" }) });
    expect(model.bubble).toBe(MANUAL_UNCERTAIN_TEXT);
  });

  it("ignores edits to a different mistake's line", () => {
    const model = alerted();
    expect(duckReducer(model, { type: "mistake_touched", id: "m_other" })).toBe(model);
  });
});

describe("lines that couldn't be settled", () => {
  const lines = [{ id: "a", inImage: true }, { id: "b", inImage: true }, { id: "c", inImage: false }];

  it("looks again only at lines that were drawn and not settled", () => {
    expect(linesToLookAgainAt(lines, new Set(["a"]), new Map())).toEqual(["b"]);
    expect(linesToLookAgainAt(lines, new Set(["a", "b"]), new Map())).toEqual([]);
  });

  it("gives up after a few looks, line by line", () => {
    const tries = new Map([["a", MAX_LINE_RECHECKS], ["b", MAX_LINE_RECHECKS - 1]]);
    expect(linesToLookAgainAt(lines, new Set(), tries)).toEqual(["b"]);
  });

  it("asks for new readings only when a drawn line has been tried already", () => {
    expect(attemptFor(lines, new Map())).toBe(0);
    expect(attemptFor(lines, new Map([["c", 2]]))).toBe(0);
    expect(attemptFor(lines, new Map([["b", 2]]))).toBe(2);
  });
});
