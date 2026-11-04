import { describe, expect, it } from "vitest";
import { duckReducer, initialDuck, MANUAL_AMBIGUOUS_TEXT, NEEDS_CONSTANT_TEXT, SOLVING_TEXT } from "@/lib/duckMachine";
import { allDrawn, isLasso, placePanel, pointInLasso, selectedWorking, strokesInLasso, type LassoPoint } from "@/lib/lasso";
import { eventsFrom } from "@/lib/learning";
import { prettyMath, strokesUnderMistakes, type LineAnchor } from "@/lib/mistakes";
import { lineId, planImage, selectRegion } from "@/lib/snapshot";
import type { AnalyzeResponse, PenStroke } from "@/lib/types";

const stroke = (id: string, points: [number, number][]): PenStroke => ({
  id,
  points: points.map(([x, y], index) => ({ x, y, timestamp: index })),
  color: "#000",
  width: 2,
  createdAt: 0,
});

// A stroke of writing that takes up the space a character would.
const glyph = (id: string, x: number, y: number) => stroke(id, [[x, y], [x + 10, y + 12], [x, y + 24], [x + 10, y + 24]]);
const row = (prefix: string, y: number, count = 3) => Array.from({ length: count }, (_, index) => glyph(`${prefix}${index}`, 100 + index * 30, y));
const box = (left: number, top: number, right: number, bottom: number): LassoPoint[] => [
  { x: left, y: top },
  { x: right, y: top },
  { x: right, y: bottom },
  { x: left, y: bottom },
];

describe("what a loop goes around", () => {
  it("knows inside from outside, and closes an open loop", () => {
    const loop = box(0, 0, 100, 100);
    expect(pointInLasso({ x: 50, y: 50 }, loop)).toBe(true);
    expect(pointInLasso({ x: 150, y: 50 }, loop)).toBe(false);
    expect(pointInLasso({ x: 50, y: -1 }, loop)).toBe(false);
  });

  it("follows a shape, not its bounding box", () => {
    // An L. Its bounding box would take in the corner it leaves out.
    const loop = [{ x: 0, y: 0 }, { x: 40, y: 0 }, { x: 40, y: 60 }, { x: 100, y: 60 }, { x: 100, y: 100 }, { x: 0, y: 100 }];
    expect(strokesInLasso([glyph("inside", 10, 10), glyph("corner", 70, 10)], loop)).toEqual(["inside"]);
  });

  it("selects one line and leaves the next alone", () => {
    const strokes = [...row("a", 100), ...row("b", 200)];
    expect(strokesInLasso(strokes, box(90, 90, 200, 135))).toEqual(["a0", "a1", "a2"]);
  });

  it("selects several lines", () => {
    const strokes = [...row("a", 100), ...row("b", 150), ...row("c", 400)];
    expect(strokesInLasso(strokes, box(90, 90, 200, 185))).toEqual(["a0", "a1", "a2", "b0", "b1", "b2"]);
  });

  it("a stroke mostly outside stays out, even if the loop clips it", () => {
    const long = stroke("long", [[50, 110], [500, 110]]);
    expect(strokesInLasso([long], box(40, 90, 120, 130))).toEqual([]);
  });

  it("a long straight stroke across the middle counts, though only its ends are recorded", () => {
    const bar = stroke("bar", [[90, 110], [210, 110]]);
    expect(strokesInLasso([bar], box(80, 90, 220, 130))).toEqual(["bar"]);
  });

  it("a dot counts", () => {
    expect(strokesInLasso([stroke("dot", [[100, 100]])], box(90, 90, 110, 110))).toEqual(["dot"]);
  });

  it("a tap or a scribble is no loop", () => {
    expect(isLasso([{ x: 0, y: 0 }, { x: 1, y: 1 }])).toBe(false);
    expect(isLasso(box(0, 0, 3, 3))).toBe(false);
    expect(strokesInLasso(row("a", 100), box(100, 100, 103, 103))).toEqual([]);
  });

  it("is the same at any scale, since it's all in page units", () => {
    const strokes = row("a", 100);
    const page = box(90, 90, 200, 135);
    expect(strokesInLasso(strokes, page)).toEqual(["a0", "a1", "a2"]);
    // What a canvas at another scale and scroll position would have worked out from its own pixels.
    for (const [scale, scrollTop] of [[0.5, 0], [1.3, 80], [2, 300]]) {
      const screen = page.map(({ x, y }) => ({ x: x * scale, y: y * scale - scrollTop }));
      const back = screen.map(({ x, y }) => ({ x: x / scale, y: (y + scrollTop) / scale }));
      expect(strokesInLasso(strokes, back)).toEqual(["a0", "a1", "a2"]);
    }
  });
});

describe("what goes to the server", () => {
  it("a whole line has the id a check would give it, so what's remembered can be used", () => {
    const strokes = row("a", 100);
    const working = selectedWorking(strokes, strokes.map((item) => item.id))!;
    const checked = selectRegion(strokes, [{ minX: 0, maxX: 1000, minY: 0, maxY: 1000 }])!;
    expect(working.selectedLineIds).toEqual([checked.lines[0].id]);
    expect(planImage(working.region, new Set(working.selectedLineIds)).inkLines).toEqual([{ id: lineId(strokes), inImage: false, spaced: false }]);
  });

  it("part of a line is only that part, and can't be mistaken for the whole", () => {
    const strokes = row("a", 100, 6);
    const some = strokes.slice(0, 3);
    const working = selectedWorking(strokes, some.map((item) => item.id))!;
    expect(working.selectedLineIds).toEqual([lineId(some)]);
    expect(working.selectedLineIds[0]).not.toBe(lineId(strokes));
    expect(working.region.strokes.map((item) => item.id)).toEqual(["a0", "a1", "a2"]);
  });

  it("the lines just above go along as context, and aren't called circled", () => {
    const strokes = [...row("a", 100), ...row("b", 140), ...row("c", 180)];
    const working = selectedWorking(strokes, ["c0", "c1", "c2"])!;
    expect(working.selectedLineIds).toHaveLength(1);
    expect(working.region.lines).toHaveLength(3);
    expect(working.region.lines.at(-1)!.id).toBe(working.selectedLineIds[0]);
  });

  it("no more than two, and not across a gap", () => {
    const strokes = [...row("a", 100), ...row("b", 140), ...row("c", 180), ...row("d", 220)];
    expect(selectedWorking(strokes, ["d0", "d1", "d2"])!.region.lines).toHaveLength(3);
    const apart = [...row("a", 100), ...row("c", 900)];
    expect(selectedWorking(apart, ["c0", "c1", "c2"])!.region.lines).toHaveLength(1);
  });

  it("says when it's too much to look at", () => {
    const strokes = Array.from({ length: 9 }, (_, index) => row(`l${index}-`, 100 + index * 40)).flat();
    expect(selectedWorking(strokes, strokes.map((item) => item.id))!.tooMuch).toBe(true);
  });

  it("nothing at all is nothing", () => {
    expect(selectedWorking(row("a", 100), ["missing"])).toBeNull();
  });

  it("asking again can draw it all", () => {
    expect(allDrawn([{ id: "a", inImage: false, spaced: false }])[0].inImage).toBe(true);
  });

  it("a mistake on any circled stroke is found", () => {
    const anchors = new Map<string, LineAnchor>([["m1", { lineId: "l", strokeIds: ["a", "b"], bounds: { minX: 0, minY: 0, maxX: 1, maxY: 1 }, version: 1 }]]);
    const page = new Set(["l"]);
    expect(strokesUnderMistakes(["b", "z"], anchors, ["m1"], page)).toBe("m1");
    expect(strokesUnderMistakes(["z"], anchors, ["m1"], page)).toBeNull();
    expect(strokesUnderMistakes(["b"], anchors, [], page)).toBeNull();
    // Written on since, it's a different line, and what was found about it no longer says.
    expect(strokesUnderMistakes(["b"], anchors, ["m1"], new Set(["l+more"]))).toBeNull();
  });
});

describe("where the panel goes", () => {
  const viewport = { width: 800, height: 600 };
  const panel = { width: 320, height: 250 };

  it("below what's circled, when there's room", () => {
    expect(placePanel({ left: 100, right: 300, top: 50, bottom: 100 }, viewport, panel)).toEqual({ left: 100, top: 112 });
  });
  it("above, when there isn't", () => {
    expect(placePanel({ left: 100, right: 300, top: 400, bottom: 500 }, viewport, panel)).toEqual({ left: 100, top: 138 });
  });
  it("on the screen, whatever else", () => {
    const place = placePanel({ left: 700, right: 790, top: 100, bottom: 500 }, { width: 800, height: 600 }, panel);
    expect(place.left + panel.width).toBeLessThanOrEqual(800);
    expect(place.top + panel.height).toBeLessThanOrEqual(600);
    expect(placePanel({ left: -50, right: 10, top: 10, bottom: 20 }, viewport, panel).left).toBe(12);
  });
  it("fits a phone", () => {
    const place = placePanel({ left: 10, right: 300, top: 10, bottom: 700 }, { width: 375, height: 700 }, { width: 351, height: 250 });
    expect(place.left).toBeGreaterThanOrEqual(12);
    expect(place.top).toBeGreaterThanOrEqual(12);
  });
});

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

describe("asking what a line is doing", () => {
  const question = { lineId: "l", step: "3x + 2x = 5", question: "Are you simplifying this, or solving it?" };

  it("a question is shown with no quack", () => {
    const model = duckReducer(initialDuck, { type: "result", result: reply({ status: "uncertain", reason: "ambiguous_intent", clarification: question }), trigger: "auto" });
    expect(model.clarification).toEqual(question);
    expect(model.quackCount).toBe(0);
    expect(model.state).toBe("idle");
  });

  it("goes when the next check has no need of it", () => {
    let model = duckReducer(initialDuck, { type: "result", result: reply({ clarification: question }), trigger: "auto" });
    model = duckReducer(model, { type: "result", result: reply(), trigger: "auto" });
    expect(model.clarification).toBeNull();
  });

  it("a stale reply says nothing about it", () => {
    let model = duckReducer(initialDuck, { type: "result", result: reply({ clarification: question }), trigger: "auto" });
    model = duckReducer(model, { type: "result", result: reply({ stale: true }), trigger: "auto" });
    expect(model.clarification).toEqual(question);
  });

  it("asked directly, it isn't called unreadable", () => {
    const model = duckReducer(initialDuck, { type: "result", result: reply({ status: "uncertain", reason: "ambiguous_intent", clarification: question }), trigger: "manual" });
    expect(model.bubble).toBeNull();
  });

  it("an imported page has nowhere to ask, so it says", () => {
    const model = duckReducer(initialDuck, { type: "import_result", result: reply({ status: "uncertain", reason: "ambiguous_intent" }) });
    expect(model.bubble).toBe(MANUAL_AMBIGUOUS_TEXT);
  });

  it("answering clears it, and says so if it's solving", () => {
    const model = duckReducer(duckReducer(initialDuck, { type: "result", result: reply({ clarification: question }), trigger: "auto" }), { type: "clarified", text: SOLVING_TEXT });
    expect(model.clarification).toBeNull();
    expect(model.bubble).toBe(SOLVING_TEXT);
  });

  it("a missing constant is a quiet note", () => {
    const model = duckReducer(initialDuck, { type: "result", result: reply({ reminder: "missing_constant" }), trigger: "auto" });
    expect(model.bubble).toBe(NEEDS_CONSTANT_TEXT);
    expect(model.quackCount).toBe(0);
  });
});

describe("what's sent to be counted", () => {
  it("gone with no change to the ink wasn't fixed", () => {
    expect(eventsFrom(reply({ resolvedMistakeIds: ["m1"] }), {}, new Set(["m1"]))).toEqual([{ eventType: "mistake_invalidated", mistakeId: "m1", reason: "misread" }]);
  });
  it("gone with the ink changed was", () => {
    expect(eventsFrom(reply({ resolvedMistakeIds: ["m1"] }), {}, new Set())).toEqual([{ eventType: "correction_verified", mistakeId: "m1" }]);
  });
});

describe("how maths is shown", () => {
  it("limits go where they're written", () => {
    expect(prettyMath("∫_0^1 x^2 dx")).toBe("∫₀¹ x² dx");
  });
});

describe("going round more than once", () => {
  const turns = (count: number) => Array.from({ length: Math.round(count * 40) }, (_, step) => {
    const angle = (step / 40) * 2 * Math.PI;
    return { x: 100 + 80 * Math.cos(angle), y: 100 + 80 * Math.sin(angle) };
  });

  it.each([1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3])("%s turns is still round what's inside", (count) => {
    expect(pointInLasso({ x: 100, y: 100 }, turns(count))).toBe(true);
    expect(pointInLasso({ x: 300, y: 100 }, turns(count))).toBe(false);
  });
});
