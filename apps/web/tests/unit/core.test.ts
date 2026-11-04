import "fake-indexeddb/auto";
import * as idb from "idb-keyval";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("idb-keyval", { spy: true });

import {
  ALERT_TEXT,
  duckReducer,
  initialDuck,
  MANUAL_NO_ERROR_TEXT,
  MANUAL_UNCERTAIN_TEXT,
  nextHelpLevel,
  RESOLVED_TEXT,
  visibleState,
  type DuckModel,
} from "@/lib/duckMachine";
import { strokeBounds, strokeHit } from "@/lib/geometry";
import { groupIntoLines } from "@/lib/lines";
import {
  addStroke,
  clearPage,
  emptyNotebook,
  redo,
  removeStrokes,
  restoreDirty,
  takeDirty,
  undo,
  type NotebookState,
} from "@/lib/notebook";
import { loadNotebook, randomId, saveNotebook } from "@/lib/persist";
import { NotebookStore } from "@/lib/store";
import { AnalysisScheduler, type Capture, type SchedulerDeps } from "@/lib/scheduler";
import { hashStrokes, isWorthAnalyzing, MAX_LINES, regionCovers, selectRegion, snapshotScale } from "@/lib/snapshot";
import type { AnalyzeResponse, Mistake, PenStroke } from "@/lib/types";

let nextId = 0;

function stroke(x: number, y: number, width = 40, height = 30): PenStroke {
  nextId += 1;
  return {
    id: `s${nextId}`,
    color: "#262626",
    width: 2,
    createdAt: nextId,
    points: [
      { x, y, timestamp: 0 },
      { x: x + width, y: y + height, timestamp: 1 },
    ],
  };
}

function page(...strokes: PenStroke[]): NotebookState {
  return strokes.reduce(addStroke, emptyNotebook);
}

describe("notebook", () => {
  it("raises the version on every edit", () => {
    let state = addStroke(emptyNotebook, stroke(0, 0));
    expect(state.version).toBe(1);
    state = addStroke(state, stroke(0, 50));
    expect(state.version).toBe(2);
    state = undo(state);
    expect(state.version).toBe(3);
    state = redo(state);
    expect(state.version).toBe(4);
    state = removeStrokes(state, [state.strokes[0].id]);
    expect(state.version).toBe(5);
    state = clearPage(state);
    expect(state.version).toBe(6);
  });

  it("does not change the version when nothing changed", () => {
    const state = page(stroke(0, 0));
    expect(removeStrokes(state, ["missing"]).version).toBe(state.version);
    expect(clearPage(emptyNotebook).version).toBe(0);
    expect(undo(emptyNotebook).version).toBe(0);
    expect(redo(state).version).toBe(state.version);
  });

  it("undoes and redoes additions and erasures", () => {
    const a = stroke(0, 0);
    const b = stroke(0, 50);
    let state = page(a, b);
    state = removeStrokes(state, [a.id]);
    expect(state.strokes).toEqual([b]);
    state = undo(state);
    expect(state.strokes.map((s) => s.id)).toEqual([a.id, b.id]);
    state = undo(state);
    expect(state.strokes).toEqual([a]);
    state = redo(state);
    expect(state.strokes.map((s) => s.id)).toEqual([a.id, b.id]);
  });

  it("drops the redo stack after a new edit", () => {
    const state = addStroke(undo(page(stroke(0, 0))), stroke(0, 50));
    expect(state.redoStack).toEqual([]);
  });

  it("undoes a clear in one step", () => {
    const cleared = clearPage(page(stroke(0, 0), stroke(0, 50)));
    expect(cleared.strokes).toEqual([]);
    expect(undo(cleared).strokes).toHaveLength(2);
  });

  it("records where the page changed, including erasures", () => {
    const a = stroke(10, 10);
    const { state } = takeDirty(page(a));
    expect(state.dirty).toEqual([]);
    const erased = removeStrokes(state, [a.id]);
    expect(erased.dirty).toEqual([strokeBounds(a)]);
  });

  it("puts the region back when an analysis is abandoned", () => {
    const { state, dirty } = takeDirty(page(stroke(10, 10)));
    const later = addStroke(state, stroke(10, 300));
    const restored = restoreDirty(later, dirty);
    expect(restored.dirty).toHaveLength(2);
    expect(Math.min(...restored.dirty.map((area) => area.minY))).toBeLessThan(30);
    expect(Math.max(...restored.dirty.map((area) => area.maxY))).toBeGreaterThan(300);
  });
});

describe("coordinates and hit testing", () => {
  it("erases only near the ink", () => {
    const s = stroke(0, 0, 100, 0);
    expect(strokeHit(s, { x: 50, y: 3 }, 5)).toBe(true);
    expect(strokeHit(s, { x: 50, y: 30 }, 5)).toBe(false);
  });

  it("hits a single-point dot", () => {
    const dot: PenStroke = { ...stroke(0, 0), points: [{ x: 5, y: 5, timestamp: 0 }] };
    expect(strokeHit(dot, { x: 6, y: 6 }, 3)).toBe(true);
    expect(strokeHit(dot, { x: 50, y: 50 }, 3)).toBe(false);
  });
});

describe("line grouping", () => {
  it("separates lines of writing", () => {
    const lines = groupIntoLines([stroke(0, 0), stroke(60, 2), stroke(0, 100), stroke(60, 104)]);
    expect(lines).toHaveLength(2);
    expect(lines[0].strokes).toHaveLength(2);
  });

  it("keeps small marks and superscripts on their line", () => {
    const minus = stroke(50, 12, 20, 1);
    const superscript = stroke(90, -6, 10, 12);
    expect(groupIntoLines([stroke(0, 0), minus, superscript])).toHaveLength(1);
  });

  it("orders lines top to bottom whatever order they were written", () => {
    const low = stroke(0, 200);
    const high = stroke(0, 0);
    const lines = groupIntoLines([low, high]);
    expect(lines[0].strokes[0].id).toBe(high.id);
  });
});

function strokeBoundsOf(strokes: PenStroke[]) {
  return strokes.map(strokeBounds).reduce((all, one) => ({
    minX: Math.min(all.minX, one.minX),
    minY: Math.min(all.minY, one.minY),
    maxX: Math.max(all.maxX, one.maxX),
    maxY: Math.max(all.maxY, one.maxY),
  }));
}

describe("region selection", () => {
  const line = (y: number) => stroke(0, y, 200, 30);

  it("sends the changed line with the lines above for context", () => {
    const strokes = [line(0), line(50), line(100), line(150), line(200)];
    const last = strokes[4];
    const region = selectRegion(strokes, [strokeBounds(last)])!;
    expect(region.lineCount).toBe(4);
    expect(region.strokes).not.toContain(strokes[0]);
    expect(region.strokes).toContain(last);
  });

  describe("on a page longer than one crop", () => {
    const tall = Array.from({ length: 20 }, (_, index) => line(index * 50));
    const all = strokeBoundsOf(tall);

    it("keeps a known mistake however much else changed", () => {
      const mistake = strokeBounds(tall[1]);
      const region = selectRegion(tall, [all], [mistake])!;
      expect(region.lineCount).toBeLessThanOrEqual(MAX_LINES);
      expect(region.strokes).toContain(tall[1]);
      expect(regionCovers(region, tall, mistake)).toBe(true);
    });

    it("returns what didn't fit, so that it can be checked next", () => {
      const first = selectRegion(tall, [all])!;
      expect(first.lineCount).toBe(MAX_LINES);
      expect(first.strokes).toContain(tall[19]);
      expect(first.overflow.length).toBe(20 - MAX_LINES);
      const second = selectRegion(tall, first.overflow)!;
      expect(second.strokes).toContain(tall[0]);
      expect(second.overflow).toEqual([]);
    });

    it("says when a crop can't speak for a mistake", () => {
      const region = selectRegion(tall, [strokeBounds(tall[19])])!;
      expect(regionCovers(region, tall, strokeBounds(tall[1]))).toBe(false);
      expect(regionCovers(region, tall, strokeBounds(tall[19]))).toBe(true);
    });

    it("an edit to an old line is sent, even with mistakes filling the crop", () => {
      const keep = tall.map(strokeBounds);
      const region = selectRegion(tall, [strokeBounds(tall[1])], keep)!;
      expect(region.strokes).toContain(tall[1]);
      expect(region.overflow).toEqual([]);
    });

    it("new writing isn't starved by mistakes filling the crop", () => {
      const keep = tall.slice(0, 16).map(strokeBounds);
      const region = selectRegion(tall, [strokeBounds(tall[19])], keep)!;
      expect(region.lineCount).toBeLessThanOrEqual(MAX_LINES);
      expect(region.strokes).toContain(tall[19]);
    });
  });

  it("does not reach across a large gap into unrelated writing", () => {
    const personal = stroke(0, 0, 200, 30);
    const work = [line(1000), line(1050)];
    const region = selectRegion([personal, ...work], [strokeBounds(work[1])])!;
    expect(region.strokes).not.toContain(personal);
  });

  it("includes following lines when earlier working changes", () => {
    const strokes = [line(0), line(50), line(100)];
    const region = selectRegion(strokes, [strokeBounds(strokes[0])])!;
    expect(region.strokes).toContain(strokes[2]);
  });

  it("checks around a line that was erased", () => {
    const strokes = [line(0), line(50), line(150)];
    const erasedAt = strokeBounds(line(100));
    const region = selectRegion(strokes, [erasedAt])!;
    expect(region.strokes).toContain(strokes[1]);
    expect(region.strokes).toContain(strokes[2]);
  });

  it("always includes an outstanding mistake even when far away", () => {
    const mistake = line(0);
    const later = line(2000);
    const region = selectRegion([mistake, later], [strokeBounds(later)], [strokeBounds(mistake)])!;
    expect(region.strokes).toContain(mistake);
    expect(region.strokes).toContain(later);
    expect(region.bands).toHaveLength(2);
  });

  it("doesn't send private writing between two edited places", () => {
    const top = line(0);
    const personal = [line(50), line(100), line(150), line(200), line(250), line(300), line(350), line(400), line(450)];
    const bottom = line(500);
    const all = [top, ...personal, bottom];
    const region = selectRegion(all, [strokeBounds(top), strokeBounds(bottom)])!;
    expect(region.strokes).toContain(top);
    expect(region.strokes).toContain(bottom);
    expect(region.strokes).not.toContain(personal[4]);
    expect(region.bands).toHaveLength(2);
  });

  it("keeps separate edits separate", () => {
    const state = page(line(0), line(500));
    expect(state.dirty).toHaveLength(2);
  });

  it("doesn't reach across a large gap when checking around an erased line", () => {
    const far = line(5000);
    expect(selectRegion([far], [strokeBounds(line(0))])).toBeNull();
  });

  it("limits the lines sent, keeping the image legible", () => {
    const many = Array.from({ length: 60 }, (_, i) => line(i * 45));
    const region = selectRegion(many, many.map(strokeBounds))!;
    expect(region.lineCount).toBeLessThanOrEqual(14);
  });

  it("a margin rule doesn't merge the whole page into one line", () => {
    const rule: PenStroke = { ...stroke(5, 0, 1, 2000) };
    const lines = groupIntoLines([rule, line(0), line(100), line(200)]);
    expect(lines).toHaveLength(3);
  });

  it("keeps a stacked fraction on one line", () => {
    const top = stroke(0, 0, 60, 30);
    const bar = stroke(0, 38, 70, 1);
    const bottom = stroke(0, 46, 60, 30);
    expect(groupIntoLines([top, bar, bottom])).toHaveLength(1);
  });

  it("does not join a lone minus to the lines around it when they are far apart", () => {
    const above = stroke(0, 0, 60, 20);
    const minus = stroke(0, 200, 70, 1);
    const below = stroke(0, 400, 60, 20);
    expect(groupIntoLines([above, minus, below])).toHaveLength(3);
  });

  it("returns nothing for an empty page", () => {
    expect(selectRegion([], [])).toBeNull();
  });

  it("ignores a tiny fragment", () => {
    const dot = stroke(0, 0, 3, 3);
    expect(isWorthAnalyzing(selectRegion([dot], [strokeBounds(dot)])!)).toBe(false);
    const written = line(0);
    expect(isWorthAnalyzing(selectRegion([written], [strokeBounds(written)])!)).toBe(true);
  });

  it("limits image size however long the crop is", () => {
    expect(snapshotScale(1000, 8000) * 8000).toBeLessThanOrEqual(1600);
    expect(snapshotScale(200, 100)).toBeGreaterThan(1);
  });

  it("hashes identical ink identically, and different ink differently", () => {
    const a = line(0);
    expect(hashStrokes([a])).toBe(hashStrokes([{ ...a, id: "other" }]));
    expect(hashStrokes([a])).not.toBe(hashStrokes([line(5)]));
  });
});

function response(overrides: Partial<AnalyzeResponse> = {}): AnalyzeResponse {
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

const mistake: Mistake = {
  id: "m_1",
  category: "algebra_expansion",
  errorType: "incorrect_distribution",
  operationType: "expression_equivalence",
  previousStep: "2(x + 3)",
  currentStep: "2x + 3",
  verified: true,
  region: null,
  lineId: null,
  problemId: null,
  parsedOperation: "",
  isNew: false,
};

const quackResult = response({
  status: "confirmed_error",
  mistake,
  intervention: { shouldQuack: true, showHintImmediately: false },
});
const alerted = duckReducer(initialDuck, { type: "result", result: quackResult, trigger: "auto" });

describe("duck state machine", () => {
  it("quacks once, without any explanation, on a verified mistake", () => {
    expect(alerted.state).toBe("alert");
    expect(alerted.quackCount).toBe(1);
    expect(alerted.bubble).toBe(ALERT_TEXT);
    expect(alerted.assistance).toEqual([]);
  });

  it("stays silent when the server says not to quack", () => {
    const same = response({ status: "confirmed_error", mistake });
    const next = duckReducer(alerted, { type: "result", result: same, trigger: "auto" });
    expect(next.quackCount).toBe(1);
  });

  it("does not interrupt on uncertain, unsupported or correct work", () => {
    for (const status of ["uncertain", "unsupported", "no_error"] as const) {
      const next = duckReducer(initialDuck, { type: "result", result: response({ status }), trigger: "auto" });
      expect(next.state).toBe("idle");
      expect(next.bubble).toBeNull();
      expect(next.quackCount).toBe(0);
    }
  });

  it("only answers a manual check", () => {
    const fine = duckReducer(initialDuck, { type: "result", result: response(), trigger: "manual" });
    expect(fine.bubble).toBe(MANUAL_NO_ERROR_TEXT);
    const unclear = duckReducer(initialDuck, {
      type: "result",
      result: response({ status: "uncertain" }),
      trigger: "manual",
    });
    expect(unclear.bubble).toBe(MANUAL_UNCERTAIN_TEXT);
  });

  it("ignores a result the server marked stale", () => {
    const stale = { ...quackResult, stale: true };
    const next = duckReducer(initialDuck, { type: "result", result: stale, trigger: "auto" });
    expect(next).toEqual(initialDuck);
  });

  it("acknowledges a correction and returns to idle", () => {
    const fixed = response({ resolvedMistakeIds: ["m_1"] });
    const next = duckReducer(alerted, { type: "result", result: fixed, trigger: "auto" });
    expect(next.state).toBe("resolved");
    expect(next.mistake).toBeNull();
    expect(next.bubble).toBe(RESOLVED_TEXT);
    expect(next.resolvedCount).toBe(1);
    expect(duckReducer(next, { type: "bubble_expired" }).state).toBe("idle");
  });

  it("keeps the alert while a rewrite is unreadable", () => {
    const next = duckReducer(alerted, { type: "result", result: response({ status: "uncertain" }), trigger: "auto" });
    expect(next.state).toBe("alert");
    expect(next.mistake).toEqual(mistake);
  });

  it("alerts for a new error found while fixing the first", () => {
    const newMistake = { ...mistake, id: "m_2" };
    const next = duckReducer(alerted, {
      type: "result",
      result: {
        ...quackResult,
        mistake: newMistake,
        resolvedMistakeIds: ["m_1"],
      },
      trigger: "auto",
    });
    expect(next.state).toBe("alert");
    expect(next.mistake?.id).toBe("m_2");
    expect(next.quackCount).toBe(2);
  });

  it("moves through hint levels to an explanation", () => {
    let model: DuckModel = duckReducer(alerted, { type: "help_requested" });
    expect(nextHelpLevel(alerted)).toBe(1);
    model = duckReducer(model, { type: "help_received", level: 1, text: "Look again.", source: "model", issue: null });
    expect(model.state).toBe("hint");
    expect(nextHelpLevel(model)).toBe(2);
    model = duckReducer(duckReducer(model, { type: "help_requested" }), {
      type: "help_received",
      level: 2,
      text: "What about the 3?",
      source: "model",
      issue: null,
    });
    expect(nextHelpLevel(model)).toBe(3);
    model = duckReducer(duckReducer(model, { type: "help_requested" }), {
      type: "help_received",
      level: 3,
      text: "Both terms.",
      source: "model",
      issue: null,
    });
    expect(model.state).toBe("explaining");
    expect(model.assistance).toHaveLength(3);
  });

  it("drops help that arrives after the mistake was fixed", () => {
    const loading = duckReducer(alerted, { type: "help_requested" });
    const fixed = duckReducer(loading, { type: "result", result: response({ resolvedMistakeIds: ["m_1"] }), trigger: "auto" });
    const late = duckReducer(fixed, { type: "help_received", level: 1, text: "Late.", source: "model", issue: null });
    expect(late.assistance).toEqual([]);
  });

  it("can be dismissed and reopened without another quack", () => {
    const dismissed = duckReducer(alerted, { type: "dismiss" });
    expect(dismissed.state).toBe("idle");
    expect(dismissed.bubble).toBeNull();
    expect(dismissed.mistake).toEqual(mistake);
    const again = duckReducer(dismissed, {
      type: "result",
      result: response({ status: "confirmed_error", mistake }),
      trigger: "auto",
    });
    expect(again.state).toBe("idle");
    expect(again.quackCount).toBe(1);
    const reopened = duckReducer(dismissed, { type: "reopen" });
    expect(reopened.state).toBe("alert");
    expect(reopened.quackCount).toBe(1);
  });

  it("does not interrupt the student's reading when a check starts", () => {
    expect(duckReducer(alerted, { type: "check_started" }).state).toBe("alert");
    expect(duckReducer(initialDuck, { type: "check_started" }).state).toBe("checking");
  });

  it("surfaces model problems without inventing a verdict", () => {
    const issue = { category: "timeout", message: "Meta's API took too long to answer." };
    const next = duckReducer(initialDuck, {
      type: "result",
      result: response({ status: "uncertain", modelIssue: issue }),
      trigger: "auto",
    });
    expect(next.issue).toEqual(issue);
    expect(next.state).toBe("idle");
    expect(next.quackCount).toBe(0);
    const recovered = duckReducer(next, { type: "result", result: response(), trigger: "auto" });
    expect(recovered.issue).toBeNull();
  });

  it("clears the mistake if the whole page is erased, and doesn't call that a fix", () => {
    const next = duckReducer(alerted, { type: "page_empty", trigger: "auto" });
    expect(next.state).toBe("idle");
    expect(next.bubble).toBeNull();
    expect(next.mistake).toBeNull();
  });

  it("keeps an unfixed mistake known when a new one is shown", () => {
    const newer = { ...mistake, id: "m_2" };
    const next = duckReducer(alerted, { type: "result", result: { ...quackResult, mistake: newer }, trigger: "auto" });
    expect(Object.keys(next.seen).sort()).toEqual(["m_1", "m_2"]);
    expect(next.mistake?.id).toBe("m_2");
    expect(next.quackCount).toBe(2);
  });

  it("falls back to the older mistake when the newer is fixed, without a sound", () => {
    const newer = { ...mistake, id: "m_2" };
    const both = duckReducer(alerted, { type: "result", result: { ...quackResult, mistake: newer }, trigger: "auto" });
    const next = duckReducer(both, {
      type: "result",
      result: response({ status: "confirmed_error", mistake, resolvedMistakeIds: ["m_2"] }),
      trigger: "auto",
    });
    // "That's it" gets read first, and the older mistake comes forward quietly after it.
    expect(next.state).toBe("resolved");
    expect(next.bubble).toBe(RESOLVED_TEXT);
    expect(next.quackCount).toBe(2);
    expect(Object.keys(next.seen)).toEqual(["m_1"]);
    const after = duckReducer(next, { type: "bubble_expired" });
    expect(after.mistake?.id).toBe("m_1");
    expect(after.quackCount).toBe(2);
    expect(after.bubble).toBeNull();
  });

  it("does not bring back a dismissed mistake when another is fixed", () => {
    const newer = { ...mistake, id: "m_2" };
    let model = duckReducer(alerted, { type: "dismiss" });
    model = duckReducer(model, { type: "result", result: { ...quackResult, mistake: newer }, trigger: "auto" });
    model = duckReducer(model, {
      type: "result",
      result: response({ status: "confirmed_error", mistake, resolvedMistakeIds: ["m_2"] }),
      trigger: "auto",
    });
    model = duckReducer(model, { type: "bubble_expired" });
    expect(model.mistake?.id).toBe("m_1");
    expect(model.dismissed).toBe(true);
    expect(model.bubble).not.toBe(ALERT_TEXT);
  });

  it("restores what was seen before a reload, and stays quiet about it", () => {
    const restored = duckReducer(initialDuck, { type: "restore", seen: { m_1: { dismissed: true } } });
    const next = duckReducer(restored, { type: "result", result: response({ status: "confirmed_error", mistake }), trigger: "auto" });
    expect(next.quackCount).toBe(0);
    expect(next.dismissed).toBe(true);
    expect(next.bubble).toBeNull();
  });

  it("a failed manual check doesn't replace an alert", () => {
    const next = duckReducer(alerted, { type: "request_failed", issue: { category: "network", message: "x" }, trigger: "manual" });
    expect(next.bubble).toBe(ALERT_TEXT);
    expect(next.state).toBe("alert");
    expect(next.issue?.category).toBe("network");
  });

  it("a note doesn't replace an alert", () => {
    expect(duckReducer(alerted, { type: "say", text: "hello" }).bubble).toBe(ALERT_TEXT);
  });

  it("an imported page doesn't disturb the notebook's mistake", () => {
    const next = duckReducer(alerted, { type: "import_result", result: response() });
    expect(next.mistake).toEqual(mistake);
    expect(next.mistakeSource).toBe("notebook");
    expect(next.state).toBe("alert");
  });

  it("an imported mistake isn't recorded as a notebook mistake", () => {
    const next = duckReducer(initialDuck, { type: "import_result", result: quackResult });
    expect(next.mistakeSource).toBe("import");
    expect(next.seen).toEqual({});
    expect(next.quackCount).toBe(1);
    const empty = duckReducer(next, { type: "page_empty", trigger: "manual" });
    expect(empty.mistake).toEqual(mistake);
  });

  it("erasing a mistake's lines removes it, and doesn't call that a fix", () => {
    const next = duckReducer(alerted, { type: "mistakes_erased", ids: ["m_1"] });
    expect(next.state).toBe("idle");
    expect(next.bubble).toBeNull();
    expect(next.mistake).toBeNull();
    expect(next.seen).toEqual({});
  });

  it("treats sound as separate from the alert", () => {
    expect(visibleState("idle", true)).toBe("muted");
    expect(visibleState("alert", true)).toBe("alert");
    expect(visibleState("idle", false)).toBe("idle");
  });
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("analysis scheduler", () => {
  let version: number;
  let results: AnalyzeResponse[];
  let errors: unknown[];
  let sends: { capture: Capture; signal: AbortSignal; reply: ReturnType<typeof deferred<AnalyzeResponse>> }[];
  let restored: number;
  let hash: string;
  let scheduler: AnalysisScheduler;

  const flush = () => vi.advanceTimersByTimeAsync(0);

  beforeEach(() => {
    vi.useFakeTimers();
    version = 1;
    results = [];
    errors = [];
    sends = [];
    restored = 0;
    hash = "a";
    const deps: SchedulerDeps = {
      getVersion: () => version,
      capture: () => ({ version, hash, image: "data:", restore: () => (restored += 1) }),
      send: (capture, _trigger, signal) => {
        const reply = deferred<AnalyzeResponse>();
        sends.push({ capture, signal, reply });
        return reply.promise;
      },
      onResult: (result) => results.push(result),
      onError: (error) => errors.push(error),
      onBusyChange: () => {},
      debounceMs: 2000,
    };
    scheduler = new AnalysisScheduler(deps);
  });

  afterEach(() => {
    scheduler.dispose();
    vi.useRealTimers();
  });

  it("waits for a pause before analysing", async () => {
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(1999);
    expect(sends).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(sends).toHaveLength(1);
  });

  it("restarts the wait on every edit", async () => {
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(1500);
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(1500);
    expect(sends).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(500);
    expect(sends).toHaveLength(1);
  });

  it("never analyses with the pen down", async () => {
    scheduler.notifyEdit();
    scheduler.setDrawing(true);
    await vi.advanceTimersByTimeAsync(10000);
    expect(sends).toHaveLength(0);
  });

  it("delivers a result for the current version", async () => {
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(2000);
    sends[0].reply.resolve(response({ snapshotVersion: 1 }));
    await flush();
    expect(results).toHaveLength(1);
  });

  it("drops a delayed result when the student has already changed the page", async () => {
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(2000);
    version = 2;
    sends[0].reply.resolve(response({ snapshotVersion: 1, status: "confirmed_error" }));
    await flush();
    expect(results).toHaveLength(0);
    expect(restored).toBe(1);
  });

  it("keeps the request in flight when writing resumes, and judges its reply on arrival", async () => {
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(2000);
    version = 2;
    scheduler.notifyEdit();
    expect(sends[0].signal.aborted).toBe(false);
    sends[0].reply.resolve(response({ snapshotVersion: 1, status: "confirmed_error" }));
    await flush();
    // Without a judge, any change makes the reply stale.
    expect(results).toHaveLength(0);
  });

  it("does not run overlapping analyses: the next starts when the first is answered", async () => {
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(2000);
    version = 2;
    hash = "b";
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(5000);
    expect(sends).toHaveLength(1);
    sends[0].reply.resolve(response({ snapshotVersion: 1 }));
    await flush();
    expect(sends).toHaveLength(2);
    expect(sends[0].signal.aborted).toBe(false);
  });

  it("skips an automatic check of identical ink, but not a manual one", async () => {
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(2000);
    sends[0].reply.resolve(response({ snapshotVersion: 1 }));
    await flush();
    version = 2;
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(2000);
    expect(sends).toHaveLength(1);
    scheduler.checkNow();
    await flush();
    expect(sends).toHaveLength(2);
  });

  it("does retry identical ink after a failed analysis", async () => {
    const issue = { category: "timeout", message: "slow" };
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(2000);
    sends[0].reply.resolve(response({ snapshotVersion: 1, status: "uncertain", modelIssue: issue }));
    await flush();
    version = 2;
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(2000);
    expect(sends).toHaveLength(2);
  });

  it("reports network failure and keeps the changed region for later", async () => {
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(2000);
    sends[0].reply.reject(new Error("offline"));
    await flush();
    expect(errors).toHaveLength(1);
    expect(restored).toBe(1);
  });

  it("does not report a failure caused by our own cancellation", async () => {
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(2000);
    scheduler.setEnabled(false);
    expect(sends[0].signal.aborted).toBe(true);
    sends[0].reply.reject(new DOMException("Aborted", "AbortError"));
    await flush();
    expect(errors).toHaveLength(0);
  });

  it("re-arms after a pen tap cancels the pending wait", async () => {
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(1000);
    scheduler.setDrawing(true);
    scheduler.setDrawing(false);
    await vi.advanceTimersByTimeAsync(2000);
    expect(sends).toHaveLength(1);
  });

  it("a pen tap during a check doesn't cancel it, and the new ink is checked after", async () => {
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(2000);
    scheduler.setDrawing(true);
    expect(sends[0].signal.aborted).toBe(false);
    version = 2;
    hash = "b";
    scheduler.notifyEdit();
    scheduler.setDrawing(false);
    sends[0].reply.resolve(response({ snapshotVersion: 1 }));
    await vi.advanceTimersByTimeAsync(2000);
    expect(sends).toHaveLength(2);
  });

  it("does not re-arm when nothing is waiting to be checked", async () => {
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(2000);
    sends[0].reply.resolve(response({ snapshotVersion: 1 }));
    await flush();
    scheduler.setDrawing(true);
    scheduler.setDrawing(false);
    await vi.advanceTimersByTimeAsync(5000);
    expect(sends).toHaveLength(1);
  });

  it("checks edits made while monitoring was off when it comes back on", async () => {
    scheduler.setEnabled(false);
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(5000);
    expect(sends).toHaveLength(0);
    scheduler.setEnabled(true);
    await vi.advanceTimersByTimeAsync(2000);
    expect(sends).toHaveLength(1);
  });

  it("does not remember ink from a stale reply", async () => {
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(2000);
    sends[0].reply.resolve(response({ snapshotVersion: 1, stale: true }));
    await flush();
    version = 2;
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(2000);
    expect(sends).toHaveLength(2);
  });

  it("checks the same ink again once told to forget", async () => {
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(2000);
    sends[0].reply.resolve(response({ snapshotVersion: 1 }));
    await flush();
    scheduler.forgetLastCheck();
    version = 2;
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(2000);
    expect(sends).toHaveLength(2);
  });

  it("stops watching when monitoring is off, but still allows a manual check", async () => {
    scheduler.setEnabled(false);
    scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(10000);
    expect(sends).toHaveLength(0);
    scheduler.checkNow();
    await flush();
    expect(sends).toHaveLength(1);
  });
});

describe("local persistence", () => {
  it("round-trips the notebook, including its version", async () => {
    const saved = { strokes: [stroke(1, 2)], version: 7, seen: {} };
    expect(await saveNotebook(saved)).toBe(true);
    expect(await loadNotebook()).toEqual({ status: "found", saved });
  });

  it("merges a stroke drawn before the save has loaded, and writes nothing until then", async () => {
    const saved = stroke(1, 2);
    await saveNotebook({ strokes: [saved], version: 7 });

    const notebook = new NotebookStore();
    const early = stroke(3, 400);
    notebook.add(early);
    notebook.flush();
    expect((await loadNotebook() as { saved: { strokes: PenStroke[] } }).saved.strokes).toEqual([saved]);

    await notebook.hydrate();
    expect(notebook.getState().strokes.map((s) => s.id)).toEqual([saved.id, early.id]);
    expect(notebook.getState().version).toBe(8);
    notebook.flush();
    await new Promise((resolve) => setTimeout(resolve, 20));
    const after = (await loadNotebook()) as { saved: { strokes: PenStroke[]; version: number } };
    expect(after.saved.strokes).toHaveLength(2);
    expect(after.saved.version).toBe(8);
  });

  it("never overwrites saved notes after a failed read", async () => {
    vi.spyOn(idb, "get").mockRejectedValueOnce(new Error("blocked"));
    const set = vi.spyOn(idb, "set");
    const notebook = new NotebookStore();
    expect((await notebook.hydrate()).status).toBe("failed");
    notebook.add(stroke(0, 0));
    notebook.flush();
    expect(set).not.toHaveBeenCalled();
    expect(idb.update).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });

  const settle = () => new Promise((resolve) => setTimeout(resolve, 20));
  const savedStrokes = async () => ((await loadNotebook()) as { saved: { strokes: PenStroke[] } }).saved.strokes.length;

  it("a tab holding an old copy can't overwrite newer work from another tab", async () => {
    await saveNotebook({ strokes: [stroke(0, 0)], version: 1 });
    const [one, other] = [new NotebookStore(), new NotebookStore()];
    await Promise.all([one.hydrate(), other.hydrate()]);
    const issues: unknown[] = [];
    other.setSaveListener((issue) => issues.push(issue));

    one.add(stroke(0, 100));
    one.add(stroke(0, 200));
    one.flush();
    await settle();
    expect(await savedStrokes()).toBe(3);

    other.add(stroke(0, 300));
    other.flush();
    await settle();
    expect(await savedStrokes()).toBe(3);
    expect(issues).toEqual(["conflict"]);
  });

  it("opening a second tab doesn't lock the first one out", async () => {
    await saveNotebook({ strokes: [stroke(0, 0)], version: 1 });
    const [one, other] = [new NotebookStore(), new NotebookStore()];
    await Promise.all([one.hydrate(), other.hydrate()]);
    other.setSeen({});
    other.flush();
    await settle();
    one.add(stroke(0, 100));
    one.flush();
    await settle();
    expect(await savedStrokes()).toBe(2);
  });

  it("a failed save is reported, then cleared by the next one that works", async () => {
    await saveNotebook({ strokes: [], version: 1 });
    const notebook = new NotebookStore();
    await notebook.hydrate();
    const issues: unknown[] = [];
    notebook.setSaveListener((issue) => issues.push(issue));
    vi.mocked(idb.update).mockRejectedValueOnce(new Error("quota"));
    notebook.add(stroke(0, 0));
    notebook.flush();
    await settle();
    expect(issues).toEqual(["failed"]);
    notebook.add(stroke(0, 100));
    notebook.flush();
    await settle();
    expect(issues).toEqual(["failed", null]);
    expect(await savedStrokes()).toBe(2);
  });

  it("failing saves wait for one retry, not one each", async () => {
    await saveNotebook({ strokes: [], version: 1 });
    const notebook = new NotebookStore();
    await notebook.hydrate();
    const timeouts = vi.spyOn(globalThis, "setTimeout");
    vi.mocked(idb.update).mockRejectedValue(new Error("quota"));
    for (let i = 0; i < 3; i++) {
      notebook.add(stroke(0, i * 100));
      notebook.flush();
      await settle();
    }
    const retries = timeouts.mock.calls.filter(([, delay]) => delay === 5000);
    expect(retries).toHaveLength(1);
    vi.mocked(idb.update).mockReset();
    vi.restoreAllMocks();
  });

  it("writing not yet checked survives a reload", async () => {
    await saveNotebook({ strokes: [], version: 1 });
    const before = new NotebookStore();
    await before.hydrate();
    before.add(stroke(0, 0));
    before.flush();
    await settle();
    const after = new NotebookStore();
    const { unchecked } = await after.hydrate();
    expect(unchecked).toBe(true);
    expect(after.getState().dirty.length).toBe(1);
  });

  it("builds an id on origins without crypto.randomUUID", () => {
    const original = crypto.randomUUID;
    Object.defineProperty(crypto, "randomUUID", { value: undefined, configurable: true });
    expect(randomId()).toMatch(/^[0-9a-f]{32}$/);
    Object.defineProperty(crypto, "randomUUID", { value: original, configurable: true });
  });

  it("reset leaves nothing to undo", () => {
    const notebook = new NotebookStore({ persist: false });
    notebook.add(stroke(0, 0));
    const version = notebook.getState().version;
    notebook.reset();
    expect(notebook.getState().strokes).toEqual([]);
    expect(notebook.getState().version).toBeGreaterThan(version);
    notebook.undo();
    expect(notebook.getState().strokes).toEqual([]);
  });
});
