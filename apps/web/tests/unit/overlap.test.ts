// A finished answer's check shouldn't wait behind a check of the half-written line before it. On the iPad on
// 2026-11-04, a pen lifted part way through `= 4x + 5` started a check of `=` alone, whose second reading took
// 18 s, and the answer's check waited 16 s for it. Times here are from that session's ledger, rounded.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnalysisScheduler, DEFAULT_DEBOUNCE_MS, MAX_IN_FLIGHT, type Capture, type Judgement } from "@/lib/scheduler";
import { duckReducer, initialDuck } from "@/lib/duckMachine";
import type { AnalyzeResponse, Mistake, Trigger } from "@/lib/types";

function reply(version: number, overrides: Partial<AnalyzeResponse> = {}): AnalyzeResponse {
  return {
    snapshotVersion: version,
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
    timings: { interpretMs: 0, verifyMs: 0, totalMs: 0, modelRequests: 2, costUsd: 0, cacheHit: false, stages: {}, calls: [] },
    ...overrides,
  };
}

describe("a check that starts beside a slower one", () => {
  let version: number;
  let durations: number[];
  let sends: { trigger: Trigger; version: number; signal: AbortSignal }[];
  let delivered: { version: number; trigger: Trigger }[];
  let busy: boolean[];
  let restored: number[];
  let verdict: (capture: Capture, result: AnalyzeResponse) => Judgement;
  // What each reply says, by call: stale, as the server says of an older check once a newer one has begun.
  let replies: Partial<AnalyzeResponse>[];
  let failures: (number | null)[];
  let captures = 0;
  let errors: { error: unknown; trigger: Trigger }[];
  let scheduler: AnalysisScheduler;

  function build(overlap: boolean) {
    scheduler?.dispose();
    scheduler = new AnalysisScheduler({
      overlap,
      getVersion: () => version,
      capture: () => {
        const at = version;
        captures += 1;
        return { version: at, hash: `h${at}`, image: "data:", restore: () => restored.push(at) };
      },
      send: (capture, trigger, signal) => {
        const call = sends.length;
        const ms = durations[call] ?? 8000;
        sends.push({ trigger, version: capture.version, signal });
        return new Promise((resolve, reject) => {
          const timer = setTimeout(() => {
            const failure = failures[call];
            if (failure !== null && failure !== undefined) reject(Object.assign(new Error("failed"), { status: failure }));
            else resolve(reply(capture.version, replies[call] ?? {}));
          }, ms);
          signal.addEventListener("abort", () => {
            clearTimeout(timer);
            reject(new DOMException("aborted", "AbortError"));
          });
        });
      },
      judge: (capture, result) => verdict(capture, result),
      onResult: (result, trigger) => delivered.push({ version: result.snapshotVersion, trigger }),
      onError: (error, trigger) => errors.push({ error, trigger }),
      onBusyChange: (value) => busy.push(value),
      requestTimeoutMs: 40_000,
    });
  }

  beforeEach(() => {
    vi.useFakeTimers();
    version = 0;
    durations = [];
    sends = [];
    delivered = [];
    busy = [];
    restored = [];
    verdict = (_capture, result) => ({ result, complete: true });
    replies = [];
    failures = [];
    captures = 0;
    errors = [];
    build(true);
  });

  afterEach(() => {
    scheduler.dispose();
    vi.useRealTimers();
  });

  async function writeStroke() {
    scheduler.setDrawing(true);
    await vi.advanceTimersByTimeAsync(150);
    version += 1;
    scheduler.setDrawing(false);
    scheduler.notifyEdit();
  }

  it("a finished answer's check starts after its own pause, not after the half-written line's", async () => {
    durations = [18_000, 8000];
    await writeStroke(); // `=`
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS);
    expect(sends).toHaveLength(1);
    await writeStroke(); // `4x + 5`
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS - 1);
    expect(sends).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(sends.map((send) => [send.trigger, send.version])).toEqual([["auto", 1], ["auto", 2]]);
    // The answer is in 8 s after its pause, where it waited 18 s for the other before.
    await vi.advanceTimersByTimeAsync(8000);
    expect(delivered.map((entry) => entry.version)).toEqual([2]);
    // Nothing was cancelled: the server finishes the older reading either way.
    expect(sends.every((send) => !send.signal.aborted)).toBe(true);
  });

  it("drops an older reply that arrives after a newer one's, as it's about ink that has changed", async () => {
    durations = [18_000, 8000];
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS);
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS + 8000);
    expect(delivered.map((entry) => entry.version)).toEqual([2]);
    await vi.advanceTimersByTimeAsync(18_000);
    expect(delivered.map((entry) => entry.version)).toEqual([2]);
    // Its areas go back, so nothing it took is lost if the newer image didn't cover them.
    expect(restored).toContain(1);
  });

  it("judges an older reply that arrives first, as before, and then the newer one", async () => {
    durations = [3000, 8000];
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS);
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS + 8000);
    expect(delivered.map((entry) => entry.version)).toEqual([1, 2]);
  });

  it("waits, as before, when nothing on the page has changed since the check under way", async () => {
    durations = [8000, 8000];
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS);
    // A look again owed at the same ink is not a reason for a second check at once.
    scheduler.lookAgainLater(0);
    await vi.advanceTimersByTimeAsync(4000);
    expect(sends).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(4000);
    expect(delivered).toHaveLength(1);
  });

  it("never runs more than two at once", async () => {
    expect(MAX_IN_FLIGHT).toBe(2);
    durations = [20_000, 20_000, 8000];
    for (let stroke = 0; stroke < 4; stroke++) {
      await writeStroke();
      await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS + 200);
    }
    expect(sends).toHaveLength(2);
    // When the first finishes, the page as it is now is checked next.
    await vi.advanceTimersByTimeAsync(20_000);
    expect(sends.map((send) => send.version)).toEqual([1, 2, 4]);
  });

  it("is busy once, from the first check's start to the last one's end", async () => {
    durations = [18_000, 8000];
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS);
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS + 8000);
    // The newer reply is in, and the older check still runs: still busy, said again for the duck's sake.
    expect(busy).toEqual([true, true]);
    await vi.advanceTimersByTimeAsync(18_000);
    expect(busy).toEqual([true, true, false]);
  });

  it("answers Check my work, pressed while two run, with the newer one", async () => {
    durations = [6000, 8000];
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS);
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS);
    scheduler.checkNow();
    await vi.advanceTimersByTimeAsync(8000);
    expect(sends.map((send) => send.trigger)).toEqual(["auto", "auto"]);
    expect(delivered).toEqual([
      { version: 1, trigger: "auto" },
      { version: 2, trigger: "manual" },
    ]);
  });

  it("doesn't start beside a check the student asked for", async () => {
    durations = [8000, 8000];
    await writeStroke();
    scheduler.checkNow();
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS + 1000);
    expect(sends.map((send) => send.trigger)).toEqual(["manual"]);
  });

  it("stops both when monitoring is turned off, and says it's no longer busy", async () => {
    durations = [18_000, 8000];
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS);
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS);
    scheduler.setEnabled(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(sends.every((send) => send.signal.aborted)).toBe(true);
    expect(busy.at(-1)).toBe(false);
    expect(delivered).toHaveLength(0);
  });

  it("stays one at a time in the notebook, where a newer image needn't cover an older one's lines", async () => {
    build(false);
    durations = [18_000, 8000];
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS);
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS + 5000);
    expect(sends).toHaveLength(1);
  });

  it("looks again at what an older check read, when the server called its reply stale", async () => {
    // Found in review: an older reply that comes first, stale because the newer had begun, said nothing.
    durations = [3000, 8000];
    replies = [{ stale: true }];
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS);
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS + 8000);
    expect(restored).toContain(1);
    await vi.advanceTimersByTimeAsync(10);
    // The look it's owed is taken once the newer one is in. That one read the whole page, so it's the same ink,
    // and no reading is sent again. Before the fix, nothing was owed, and nothing looked.
    expect(captures).toBe(3);
    expect(sends).toHaveLength(2);
  });

  it("doesn't take a stale reply as the answer to Check my work", async () => {
    durations = [6000, 8000];
    replies = [{ stale: true }, {}];
    verdict = (capture, result) => (capture.version === 2 ? null : { result, complete: true });
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS);
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS);
    scheduler.checkNow();
    await vi.advanceTimersByTimeAsync(8000);
    // The older reply was stale, the newer one's ink changed: the question is still owed an answer, and gets one.
    expect(delivered.filter((entry) => entry.trigger === "manual")).toHaveLength(0);
    verdict = (_capture, result) => ({ result, complete: true });
    await vi.advanceTimersByTimeAsync(8000);
    expect(delivered.at(-1)?.trigger).toBe("manual");
  });

  it("doesn't report a failure of an older check that a newer one has overtaken", async () => {
    durations = [60_000, 8000];
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS);
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS + 8000);
    expect(delivered.map((entry) => entry.version)).toEqual([2]);
    // The older one hangs until the watchdog gives up on it.
    await vi.advanceTimersByTimeAsync(40_000);
    expect(errors).toHaveLength(0);
  });

  it("says it's checking again after a reply, while another check is still under way", async () => {
    durations = [3000, 8000];
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS);
    await writeStroke();
    await vi.advanceTimersByTimeAsync(DEFAULT_DEBOUNCE_MS + 3000);
    expect(delivered.map((entry) => entry.version)).toEqual([1]);
    expect(busy).toEqual([true, true]);
    await vi.advanceTimersByTimeAsync(8000);
    expect(busy).toEqual([true, true, false]);
  });
});

describe("one mistake reported new by two checks under way at once", () => {
  // Found in review: the second check was sent before the first's reply was in, so it didn't list the mistake as
  // known, and the server called it new again. It quacked twice.
  const mistake: Mistake = {
    id: "m_a", category: "simplification", errorType: "variable_dropped", operationType: "simplification",
    previousStep: "3x + 2x", currentStep: "5", verified: true, region: null, lineId: "a", problemId: null, parsedOperation: "addition", isNew: true,
  };
  const flagged = (version: number) =>
    reply(version, { status: "confirmed_error", mistake, mistakes: [mistake], intervention: { shouldQuack: true, showHintImmediately: false } });

  it("quacks once", () => {
    const first = duckReducer(initialDuck, { type: "result", trigger: "auto", result: flagged(1) });
    expect(first.quackCount).toBe(1);
    const second = duckReducer(first, { type: "result", trigger: "auto", result: flagged(2) });
    expect(second.quackCount).toBe(1);
    expect(second.mistake?.id).toBe("m_a");
  });

  it("still quacks for it when it comes back after being put right", () => {
    const first = duckReducer(initialDuck, { type: "result", trigger: "auto", result: flagged(1) });
    const fixed = duckReducer(first, { type: "result", trigger: "auto", result: reply(2, { resolvedMistakeIds: ["m_a"] }) });
    const again = duckReducer(fixed, { type: "result", trigger: "auto", result: flagged(3) });
    expect(again.quackCount).toBe(2);
  });
});
