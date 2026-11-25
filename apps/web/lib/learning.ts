// What's happened to each verified mistake, on its way to the server.
//
// Events are kept on this device until the server has them, since an iPad can close or lose its
// connection at any moment, and nothing can be left to the page being told it's closing. Each has an
// id from when it happened, so sending it again after a lost reply changes nothing.

import { sendProgressJson } from "./api";
import { randomId } from "./persist";
import type { AnalyzeResponse, ClientEventType, LearningEventIn, LearningEventResult } from "./types";

export type LearningRecord = {
  eventType: ClientEventType;
  mistakeId: string;
  reason?: "erased" | "replaced" | "misread";
  rewritten?: boolean;
};

export type LearningSink = { record: (event: LearningRecord) => void };

const FLUSH_DELAY_MS = 300;
const RETRY_MS = 5000;
const BATCH = 50;

// What one applied reply says happened. A mistake reported as new is one the student's being told
// about. One that's gone is fixed, unless another try on the same line has taken its place. And with
// no change to the ink, nothing was fixed: it was read wrongly before, or the line turned out to be
// doing something else, so it never was a mistake.
export function eventsFrom(result: AnalyzeResponse, replaced: Record<string, string> = {}, unchanged: ReadonlySet<string> = new Set()): LearningRecord[] {
  const replacedIds = new Set(Object.values(replaced));
  const events: LearningRecord[] = [];
  for (const id of result.resolvedMistakeIds) {
    if (replacedIds.has(id)) events.push({ eventType: "mistake_invalidated", mistakeId: id, reason: "replaced" });
    else if (unchanged.has(id)) events.push({ eventType: "mistake_invalidated", mistakeId: id, reason: "misread" });
    else events.push({ eventType: "correction_verified", mistakeId: id });
  }
  for (const mistake of result.mistakes) {
    if (mistake.isNew) events.push({ eventType: "mistake_detected", mistakeId: mistake.id });
  }
  return events;
}

type Send = (events: LearningEventIn[]) => Promise<LearningEventResult>;

const sendToServer: Send = (events) => sendProgressJson("/api/learning/events", { events });

export class LearningQueue implements LearningSink {
  private queue: LearningEventIn[];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private sending: Promise<void> | null = null;

  constructor(
    private readonly storageKey: string,
    // The session it happened in is decided when it happens, not when it's finally sent.
    private readonly sessionId: () => string | null,
    private readonly send: Send = sendToServer,
    private readonly storage: Pick<Storage, "getItem" | "setItem"> | null = typeof localStorage === "undefined" ? null : localStorage,
  ) {
    this.queue = this.load();
  }

  private load(): LearningEventIn[] {
    try {
      return JSON.parse(this.storage?.getItem(this.storageKey) ?? "[]") as LearningEventIn[];
    } catch {
      return [];
    }
  }

  private save() {
    try {
      this.storage?.setItem(this.storageKey, JSON.stringify(this.queue));
    } catch {
      // Full or blocked storage. It's still held here, for as long as the page is open.
    }
  }

  get pending(): number {
    return this.queue.length;
  }

  record = (event: LearningRecord, sessionId: string | null = this.sessionId()) => {
    this.queue.push({ id: randomId(), sessionId, ...event });
    this.save();
    this.schedule(FLUSH_DELAY_MS);
  };

  // For events belonging to a practice session rather than the notebook's.
  forSession(sessionId: string): LearningSink {
    return { record: (event) => this.record(event, sessionId) };
  }

  private schedule(delay: number) {
    if (this.timer !== null || this.sending !== null) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, delay);
  }

  // Background uploads keep trying after a transport failure.
  async flush(): Promise<void> {
    await this.drain().catch(() => {});
  }

  // Explicit Finish needs to know whether every batch made it to the server.
  drain(): Promise<void> {
    if (this.sending) return this.sending;
    this.clearTimer();
    this.sending = this.sendAll().finally(() => {
      this.sending = null;
      if (this.queue.length) this.schedule(RETRY_MS);
    });
    return this.sending;
  }

  private async sendAll(): Promise<void> {
    while (this.queue.length) {
      const batch = this.queue.slice(0, BATCH);
      // A refused event won't be accepted later. On transport failure, keep the same events and IDs.
      await this.send(batch);
      const sent = new Set(batch.map((event) => event.id));
      this.queue = this.queue.filter((event) => !sent.has(event.id));
      this.save();
    }
  }

  private clearTimer() {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  dispose() {
    this.clearTimer();
  }
}
