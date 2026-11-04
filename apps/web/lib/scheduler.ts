// When to analyse, and which results still count.
//
// Rules: wait for a short pause; never start a check while the pen is down. A check in flight is never
// cancelled by writing: the student keeps writing, and the reply is judged against the ink as it is when it
// arrives. Only the part of a reply whose ink is unchanged is used. Work the reply doesn't cover stays pending.
//
// One check at a time, except where every check reads the whole page (`overlap`, for lessons and practice). There
// a second may start beside one under way, and only one, when the page has changed since that one's image was
// taken and the pause has passed. On the iPad on 2026-11-04, a pen lifted part way through an answer (`=`,
// `= 10x+`, `=6+`) started a check, and the finished answer's check then waited 5 to 16 seconds for it, in six of
// eight results. The newer check reads everything the older one did, so nothing it would have said is lost: the
// server marks the older reply stale once the newer has begun, and if it arrives after the newer one's, it's
// dropped here. The notebook stays one at a time, because there a newer image needn't cover an older one's
// lines, and an alert in them would wait for the next edit.

import { mark } from "./latency";
import type { AnalyzeResponse, Trigger } from "./types";

export const DEFAULT_DEBOUNCE_MS = 800;
// Longer than the server's own worst case for one reading, so this only catches a request that
// is stuck, and then frees the way for the next check.
export const DEFAULT_REQUEST_TIMEOUT_MS = 40_000;
// At most this many checks at once: the one under way, and one for what's changed since its image.
export const MAX_IN_FLIGHT = 2;
// A student who has stopped writing sends no more edits, so a check that failed for a passing
// reason has to be tried again without one. Bounded, because each try can cost money.
const RETRY_DELAYS_MS = [2000, 6000];
// A request that got no answer at all, on a flaky Wi-Fi link, as on the iPad's hotspot in round 4: two
// failures in a row held a finished answer back by about 10 s. These are tried again sooner, and once more.
// That costs nothing new: the server never saw the request, or it has the image's readings already.
const CONNECTION_RETRY_DELAYS_MS = [750, 2500, 6000];
// Long enough that a student moving on to the next line isn't racing it.
export const LOOK_AGAIN_MS = 2500;
const PASSING_ISSUES = new Set(["timeout", "network", "server_error", "rate_limited", "invalid_output", "truncated"]);

// `more` means some changed writing didn't fit in this image and needs a further check.
export type Capture = { id?: string; version: number; hash: string; image: string; restore: () => void; more?: boolean };

// What can still be used of a reply. `complete` is false when only part of it was kept.
export type Judgement = { result: AnalyzeResponse; complete: boolean } | null;

export class RequestTimedOut extends Error {
  constructor() {
    super("analysis took too long");
  }
}

export type SchedulerDeps = {
  getVersion: () => number;
  capture: (trigger: Trigger) => Capture | null;
  send: (capture: Capture, trigger: Trigger, signal: AbortSignal) => Promise<AnalyzeResponse>;
  onResult: (result: AnalyzeResponse, trigger: Trigger) => void;
  onError: (error: unknown, trigger: Trigger) => void;
  onBusyChange: (busy: boolean) => void;
  // Without this, a reply counts only if nothing at all has changed since it was captured.
  judge?: (capture: Capture, result: AnalyzeResponse) => Judgement;
  debounceMs?: number;
  requestTimeoutMs?: number;
  // Every check reads the whole page, so a newer one covers what an older one read. Only then may two overlap.
  overlap?: boolean;
  now?: () => number;
};

type Flight = { controller: AbortController; trigger: Trigger; capture: Capture; penUp: (() => void) | null };

export class AnalysisScheduler {
  private timer: ReturnType<typeof setTimeout> | null = null;
  // Oldest first.
  private flights: Flight[] = [];
  private drawing = false;
  private enabled = true;
  private lastHash: string | null = null;
  // There are edits no finished check has covered.
  private pending = false;
  // The student asked while an automatic check was running. Its answer is theirs.
  private manualWaiting = false;
  private lastActivity = -Infinity;
  private retries = 0;
  private notBefore = -Infinity;
  // The newest page version a delivered reply was about. Nothing older is delivered after it.
  private delivered = -Infinity;
  private disposed = false;

  constructor(private readonly deps: SchedulerDeps) {}

  private get debounceMs() {
    return this.deps.debounceMs ?? DEFAULT_DEBOUNCE_MS;
  }

  private now() {
    return this.deps.now?.() ?? Date.now();
  }

  private get newest(): Flight | null {
    return this.flights.at(-1) ?? null;
  }

  setEnabled(enabled: boolean) {
    this.enabled = enabled;
    if (enabled) {
      this.rearm();
    } else {
      this.clearTimer();
      this.cancelInFlight();
    }
  }

  setDrawing(drawing: boolean) {
    this.drawing = drawing;
    if (drawing) {
      this.clearTimer();
    } else {
      this.lastActivity = this.now();
      this.releasePenUp();
      this.rearm();
    }
  }

  // Call on every change to the notebook.
  notifyEdit() {
    this.pending = true;
    this.lastActivity = this.now();
    this.retries = 0;
    this.notBefore = -Infinity;
    this.clearTimer();
    this.rearm();
  }

  checkNow() {
    // Pressing again while a check runs can't make it faster, and used to start the same
    // expensive reading over and over on the server.
    const newest = this.newest;
    if (newest?.trigger === "manual") return;
    if (newest) {
      this.manualWaiting = true;
      return;
    }
    this.clearTimer();
    void this.run("manual");
  }

  // Writing from before a reload hasn't been checked, and no edit is coming to say so.
  requestCheck() {
    this.pending = true;
    // A full pause, as after an edit. State restored with the notes may not have reached the
    // capture yet.
    this.lastActivity = this.now();
    this.rearm();
  }

  // Some of what was just checked couldn't be settled. The same ink is owed another look, unless
  // an edit brings on a check first, which will include it.
  lookAgainLater(delay = LOOK_AGAIN_MS) {
    this.pending = true;
    this.lastHash = null;
    this.notBefore = Math.max(this.notBefore, this.now() + delay);
    this.rearm();
  }

  // The ink's history has changed meaning, for example after a mistake was resolved. The same
  // ink coming back must be checked again rather than skipped as already seen.
  forgetLastCheck() {
    this.lastHash = null;
  }

  dispose() {
    this.disposed = true;
    this.clearTimer();
    this.cancelInFlight();
  }

  // Whether a check may start now. With none under way, yes. Beside one, only if the page has changed since
  // its image was taken: a pause with nothing new, or work put back from a reply, waits for it as before.
  private mayStart(): boolean {
    const newest = this.newest;
    if (!newest) return true;
    if (!this.deps.overlap) return false;
    if (this.flights.length >= MAX_IN_FLIGHT || newest.trigger !== "auto" || this.manualWaiting) return false;
    return this.deps.getVersion() > newest.capture.version;
  }

  private rearm() {
    if (this.disposed || !this.pending || !this.enabled || this.drawing || this.timer !== null || !this.mayStart()) return;
    // The pause counts from the last edit, so a check that ends after the pause has passed is
    // followed at once by the next.
    const now = this.now();
    const wait = Math.max(0, this.debounceMs - (now - this.lastActivity), this.notBefore - now);
    mark("debounce_start", undefined, { waitMs: wait, overlapping: this.flights.length > 0 });
    this.timer = setTimeout(() => this.onPause(), wait);
  }

  private clearTimer() {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  private releasePenUp(flight?: Flight) {
    for (const each of flight ? [flight] : this.flights) {
      each.penUp?.();
      each.penUp = null;
    }
  }

  private cancelInFlight() {
    const flights = this.flights;
    this.flights = [];
    for (const flight of flights) {
      flight.controller.abort();
      flight.penUp?.();
      flight.penUp = null;
    }
  }

  private retryLater(delays: number[] = RETRY_DELAYS_MS) {
    const delay = delays[this.retries];
    // Edits made meanwhile are about to be checked anyway.
    if (delay === undefined || this.pending) return;
    this.retries += 1;
    this.pending = true;
    this.notBefore = this.now() + delay;
  }

  private onPause() {
    this.timer = null;
    mark("debounce_end");
    if (!this.enabled) return;
    if (this.drawing) {
      this.rearm();
      return;
    }
    // A check finishing meanwhile rearms; one still under way with nothing new on the page is waited for.
    if (!this.mayStart()) return;
    void this.run("auto");
  }

  private judge(capture: Capture, result: AnalyzeResponse): Judgement {
    if (this.deps.judge) return this.deps.judge(capture, result);
    return result.snapshotVersion === this.deps.getVersion() ? { result, complete: true } : null;
  }

  private async run(trigger: Trigger) {
    const capture = this.deps.capture(trigger);
    if (!capture) {
      mark("check_skipped", undefined, { trigger, reason: "nothing_to_check" });
      this.pending = false;
      this.manualWaiting = false;
      return;
    }
    if (trigger === "auto" && (capture.hash === this.lastHash || this.flights.some((flight) => flight.capture.hash === capture.hash))) {
      mark("check_skipped", capture.id, { trigger, reason: "same_ink" });
      this.pending = false;
      return;
    }

    // Edits from now on are owed a later check. This one covers what came before.
    this.pending = false;
    const flight: Flight = { controller: new AbortController(), trigger, capture, penUp: null };
    const { controller } = flight;
    if (this.flights.length) mark("check_overlapping", capture.id, { behind: this.flights.map((each) => each.capture.id ?? null) });
    this.flights.push(flight);
    if (this.flights.length === 1) this.deps.onBusyChange(true);
    let timedOut = false;
    const watchdog = setTimeout(() => {
      timedOut = true;
      controller.abort();
      this.releasePenUp(flight);
    }, this.deps.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS);
    let answeredFor: Trigger | null = null;
    // Whether a newer check started while this one was under way. Its reply, or the newer one's, has the last word.
    const overtaken = () => this.flights.indexOf(flight) < this.flights.length - 1 || capture.version < this.delivered;
    try {
      const result = await this.deps.send(capture, trigger, controller.signal);
      // The stroke under way isn't in the notebook yet, and could be this mistake's correction.
      // Pen-up comes just before the stroke is added, and this carries on after that.
      if (this.drawing) await new Promise<void>((resolve) => (flight.penUp = resolve));
      if (controller.signal.aborted) throw new DOMException("aborted", "AbortError");
      // A newer check's reply is already in, so this one can only be about older ink.
      if (capture.version < this.delivered) {
        mark("result_dropped", capture.id, { reason: "superseded" });
        capture.restore();
        this.pending = true;
      } else {
        const judged = this.judge(capture, result);
        const isNewest = !overtaken();
        if (!judged) {
          mark("result_dropped", capture.id, { reason: "ink_changed" });
          capture.restore();
          this.pending = true;
        } else {
          const answered = !result.modelIssue && !result.stale;
          if (answered && judged.complete) this.lastHash = capture.hash;
          if (answered) this.retries = 0;
          else if (result.modelIssue && PASSING_ISSUES.has(result.modelIssue.category) && isNewest) this.retryLater();
          // Nothing was read, or only part of it still applies, so these lines need another look. A reply the
          // server called stale, because a newer check began, said nothing either: what it read is owed a look.
          if (!answered || !judged.complete) {
            capture.restore();
            if (!judged.complete || result.stale || !isNewest) this.pending = true;
          }
          if (answered && capture.more) this.pending = true;
          // A question asked while checks ran is answered by the newest of them.
          // A stale reply says nothing, so it can't be the answer to a question asked.
          answeredFor = trigger === "manual" || (this.manualWaiting && isNewest && !result.stale) ? "manual" : "auto";
          if (answeredFor === "manual" && judged.complete && answered) this.manualWaiting = false;
          if (answered) this.delivered = Math.max(this.delivered, capture.version);
          this.deps.onResult(judged.result, answeredFor);
          // The duck leaves "checking" with any reply. Another check is still under way, so say so again.
          if (this.flights.length > 1) this.deps.onBusyChange(true);
        }
      }
    } catch (error) {
      capture.restore();
      if (overtaken() && (timedOut || !controller.signal.aborted)) {
        // A newer check covers the page as it is now, so this failure isn't the student's news.
        this.pending = true;
      } else if (timedOut) {
        this.deps.onError(new RequestTimedOut(), this.manualWaiting ? "manual" : trigger);
        this.manualWaiting = false;
        this.retryLater();
      } else if (!controller.signal.aborted) {
        this.deps.onError(error, this.manualWaiting ? "manual" : trigger);
        this.manualWaiting = false;
        // A refusal will be refused again. Anything else may pass.
        const status = (error as { status?: number }).status ?? 0;
        if (status === 0) this.retryLater(CONNECTION_RETRY_DELAYS_MS);
        else if (status < 400 || status >= 500 || status === 429) this.retryLater();
      } else {
        // Monitoring was turned off mid-check. What it was checking is still owed a check.
        this.pending = true;
      }
      if (this.flights.length > 1 && (timedOut || !controller.signal.aborted)) this.deps.onBusyChange(true);
    } finally {
      clearTimeout(watchdog);
      const index = this.flights.indexOf(flight);
      if (index >= 0) this.flights.splice(index, 1);
      // Busy until the last check under way has finished, and said once. After a cancel, the list is already empty.
      if (this.flights.length === 0 && (index >= 0 || controller.signal.aborted)) this.deps.onBusyChange(false);
    }
    if (this.manualWaiting && !this.disposed && this.flights.length === 0) {
      // The automatic check's answer didn't cover the page as it is now. Ask again, as asked.
      this.manualWaiting = false;
      this.clearTimer();
      void this.run("manual");
      return;
    }
    this.rearm();
  }
}
