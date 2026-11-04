// Physical iPad test, round 2: several mistakes, each on its own line, each with its own life.
import { describe, expect, it } from "vitest";
import { activeMistakes, ALERT_TEXT, duckReducer, initialDuck, RESOLVED_TEXT, STILL_WRONG_TEXT, type DuckEvent, type DuckModel } from "@/lib/duckMachine";
import { changedInk, joinsLine, judgeReply } from "@/lib/freshness";
import { strokeBounds } from "@/lib/geometry";
import { groupIntoLines } from "@/lib/lines";
import { anchorBounds, anchorOnLine, markerRect, markersFor, prettyMath, replacements, stepLabel, touchedBy, type LineAnchor } from "@/lib/mistakes";
import { lineId, planImage, selectRegion } from "@/lib/snapshot";
import type { AnalyzeResponse, Bounds, Mistake, PenStroke } from "@/lib/types";

let nextId = 0;
function stroke(x: number, y: number, w: number, h: number): PenStroke {
  const start = { x, y, timestamp: 0 };
  const end = { x: x + w, y: y + h, timestamp: 1 };
  return { id: `s${nextId++}`, points: [start, end], color: "#000", width: 0, createdAt: 0 };
}

// A written line: glyphs left to right in writing order, the way a pen makes them.
function written(y: number, glyphs = 8, height = 30, startX = 40): PenStroke[] {
  return Array.from({ length: glyphs }, (_, index) => stroke(startX + index * 26, y, 20, height));
}

function mistake(id: string, overrides: Partial<Mistake> = {}): Mistake {
  return {
    id,
    category: "arithmetic",
    errorType: "arithmetic_error",
    operationType: "arithmetic",
    previousStep: "3 * 4",
    currentStep: "10",
    verified: true,
    region: null,
    lineId: `line-${id}`,
    problemId: null,
    parsedOperation: "multiplication",
    isNew: false,
    ...overrides,
  };
}

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

function found(mistakes: Mistake[], overrides: Partial<AnalyzeResponse> = {}): AnalyzeResponse {
  return reply({
    status: "confirmed_error",
    mistake: mistakes.find((m) => m.isNew) ?? mistakes[0],
    mistakes,
    intervention: { shouldQuack: mistakes.some((m) => m.isNew), showHintImmediately: false },
    ...overrides,
  });
}

function run(events: DuckEvent[], from: DuckModel = initialDuck): DuckModel {
  return events.reduce(duckReducer, from);
}

const result = (r: AnalyzeResponse, replaced?: Record<string, string>): DuckEvent => ({ type: "result", result: r, trigger: "auto", replaced });
const multiplication = mistake("m_mul", { isNew: true });
const division = mistake("m_div", { isNew: true, previousStep: "1/2", currentStep: "2", parsedOperation: "division" });
const known = (m: Mistake) => ({ ...m, isNew: false });
const both = () => run([result(found([multiplication, division]))]);
const ids = (model: DuckModel) => activeMistakes(model).map((record) => record.mistake.id).sort();

describe("independent mistakes", () => {
  it("keeps two at once, and quacks once for each", () => {
    const model = both();
    expect(ids(model)).toEqual(["m_div", "m_mul"]);
    expect(model.quackCount).toBe(2);
    expect(model.state).toBe("alert");
  });

  it("doesn't quack again on unchanged ink", () => {
    const model = run([result(found([known(multiplication), known(division)]))], both());
    expect(model.quackCount).toBe(2);
    expect(ids(model)).toEqual(["m_div", "m_mul"]);
  });

  it("quacks once for a new mistake, and not again for the old one", () => {
    const first = run([result(found([multiplication]))]);
    expect(first.quackCount).toBe(1);
    const second = run([result(found([known(multiplication), division]))], first);
    expect(second.quackCount).toBe(2);
    expect(ids(second)).toEqual(["m_div", "m_mul"]);
    expect(second.mistake?.id).toBe("m_div");
    const third = run([result(found([known(multiplication), known(division)]))], second);
    expect(third.quackCount).toBe(2);
  });

  it("fixing one leaves the other, says so, and makes no sound", () => {
    const fixed = run([result(found([known(division)], { resolvedMistakeIds: ["m_mul"] }))], both());
    expect(ids(fixed)).toEqual(["m_div"]);
    expect(fixed.quackCount).toBe(2);
    expect(fixed.bubble).toBe(RESOLVED_TEXT);
    expect(fixed.seen.m_mul).toBeUndefined();
    expect(fixed.seen.m_div).toBeDefined();
  });

  it("the other comes forward quietly once the fix has been acknowledged", () => {
    const fixed = run([result(found([known(division)], { resolvedMistakeIds: ["m_mul"] }))], both());
    const after = duckReducer(fixed, { type: "bubble_expired" });
    expect(after.mistake?.id).toBe("m_div");
    expect(after.bubble).toBeNull();
    expect(after.quackCount).toBe(2);
  });

  it("the other keeps its mark after it comes forward", () => {
    const strokes = written(100);
    const anchors = new Map<string, LineAnchor>([["m_div", { lineId: "x", strokeIds: strokes.map((s) => s.id), bounds: { minX: 0, maxX: 300, minY: 100, maxY: 130 }, version: 1 }]]);
    const fixed = run([result(found([known(division)], { resolvedMistakeIds: ["m_mul"] }))], both());
    expect(markersFor(fixed, anchors, strokes)).toHaveLength(1);
    expect(markersFor(duckReducer(fixed, { type: "bubble_expired" }), anchors, strokes)).toHaveLength(1);
  });

  it("fixing the one waiting while another's alert shows still gets a That's it", () => {
    const fixed = run([result(found([known(multiplication)], { resolvedMistakeIds: ["m_div"] }))], both());
    expect(fixed.bubble).toBe(RESOLVED_TEXT);
    expect(ids(fixed)).toEqual(["m_mul"]);
  });

  it("each mistake keeps its own hints, and switching is immediate", () => {
    let model = run([{ type: "help_requested" }, { type: "help_received", level: 1, text: "multiplication hint", source: "template", issue: null }], both());
    expect(model.mistake?.id).toBe("m_mul");
    model = duckReducer(model, { type: "select", id: "m_div" });
    expect(model.mistake?.id).toBe("m_div");
    expect(model.assistance).toEqual([]);
    model = run([{ type: "help_requested" }, { type: "help_received", level: 1, text: "division hint", source: "template", issue: null }], model);
    model = duckReducer(model, { type: "select", id: "m_mul" });
    expect(model.assistance.map((entry) => entry.text)).toEqual(["multiplication hint"]);
    expect(model.state).toBe("hint");
    model = duckReducer(model, { type: "select", id: "m_div" });
    expect(model.assistance.map((entry) => entry.text)).toEqual(["division hint"]);
    expect(model.quackCount).toBe(2);
  });

  it("someone reading a hint isn't pulled away by a new mistake", () => {
    const reading = run([result(found([multiplication])), { type: "help_requested" }, { type: "help_received", level: 1, text: "hint", source: "template", issue: null }]);
    const model = run([result(found([known(multiplication), division]))], reading);
    expect(model.mistake?.id).toBe("m_mul");
    expect(model.state).toBe("hint");
    expect(model.assistance).toHaveLength(1);
    expect(model.quackCount).toBe(2);
    expect(ids(model)).toEqual(["m_div", "m_mul"]);
  });

  it("the duck offers a choice only with more than one", () => {
    const one = run([result(found([multiplication]))]);
    expect(duckReducer(one, { type: "choose" }).choosing).toBe(false);
    expect(duckReducer(both(), { type: "choose" }).choosing).toBe(true);
    expect(duckReducer(duckReducer(both(), { type: "choose" }), { type: "select", id: "m_mul" }).choosing).toBe(false);
  });

  it("erasing one mistake's line leaves the other", () => {
    const model = duckReducer(both(), { type: "mistakes_erased", ids: ["m_mul"] });
    expect(ids(model)).toEqual(["m_div"]);
    expect(model.bubble).not.toBe(RESOLVED_TEXT);
  });

  it("a stale reply changes nothing about any of them", () => {
    const before = both();
    expect(duckReducer(before, result(found([], { stale: true, resolvedMistakeIds: ["m_mul"] })))).toBe(before);
  });

  it("an imported page doesn't lose the notebook's mistakes", () => {
    const imported = duckReducer(both(), { type: "import_result", result: found([mistake("m_img", { isNew: true })]) });
    expect(imported.mistakeSource).toBe("import");
    expect(ids(imported)).toEqual(["m_div", "m_mul"]);
  });
});

describe("correcting a mistake", () => {
  const alerted = () => run([result(found([multiplication]))]);

  it("takes the alert away at once, without calling it fixed", () => {
    const model = duckReducer(alerted(), { type: "mistake_touched", id: "m_mul" });
    expect(model.suspended).toBe(true);
    expect(model.state).toBe("idle");
    expect(model.bubble).toBeNull();
    expect(model.mistake?.id).toBe("m_mul");
    expect(model.bubble).not.toBe(RESOLVED_TEXT);
  });

  it("says That's it only once a check confirms it", () => {
    const touched = duckReducer(alerted(), { type: "mistake_touched", id: "m_mul" });
    const unsure = duckReducer(touched, result(reply({ status: "uncertain", reason: "fix_unconfirmed" })));
    expect(unsure.bubble).not.toBe(RESOLVED_TEXT);
    expect(unsure.suspended).toBe(true);
    const fixed = duckReducer(unsure, result(reply({ resolvedMistakeIds: ["m_mul"] })));
    expect(fixed.bubble).toBe(RESOLVED_TEXT);
    expect(fixed.quackCount).toBe(1);
  });

  it("touching one of two holds only that one", () => {
    const model = duckReducer(both(), { type: "mistake_touched", id: "m_mul" });
    const records = Object.fromEntries(activeMistakes(model).map((record) => [record.mistake.id, record.suspended]));
    expect(records).toEqual({ m_mul: true, m_div: false });
  });

  it("another wrong try on the same line is neither a fix nor a new quack", () => {
    const touched = duckReducer(alerted(), { type: "mistake_touched", id: "m_mul" });
    const again = mistake("m_mul2", { isNew: true, currentStep: "11" });
    const model = duckReducer(touched, result(found([again], { resolvedMistakeIds: ["m_mul"] }), { m_mul2: "m_mul" }));
    expect(model.bubble).toBe(STILL_WRONG_TEXT);
    expect(model.mistake?.id).toBe("m_mul2");
    expect(model.quackCount).toBe(1);
    expect(model.resolvedCount).toBe(0);
    expect(ids(model)).toEqual(["m_mul2"]);
  });

  it("the old mistake coming back unchanged returns silently", () => {
    const touched = duckReducer(alerted(), { type: "mistake_touched", id: "m_mul" });
    const model = duckReducer(touched, result(found([known(multiplication)])));
    expect(model.suspended).toBe(false);
    expect(model.bubble).toBe(ALERT_TEXT);
    expect(model.quackCount).toBe(1);
  });
});

describe("line grouping on real handwriting", () => {
  it("keeps a properly raised superscript on its own line", () => {
    const x = stroke(100, 100, 20, 30);
    const raised = stroke(122, 78, 12, 18);
    expect(groupIntoLines([...written(100, 3), x, raised, ...written(100, 3, 30, 200)])).toHaveLength(1);
  });

  it("doesn't let the superscripts of a tight line join the line above", () => {
    const above = written(100);
    const below = [stroke(40, 140, 20, 30), stroke(62, 124, 12, 18), stroke(90, 140, 20, 30), stroke(118, 140, 20, 30), stroke(146, 140, 20, 30)];
    const lines = groupIntoLines([...above, ...below]);
    expect(lines).toHaveLength(2);
    expect(lines[1].strokes).toContain(below[1]);
    expect(lines[0].strokes).not.toContain(below[1]);
  });

  it("keeps a stacked d/dx with the line it's written in, in tight writing", () => {
    const above = written(100, 10);
    // d above a bar above dx, then the rest of the line. The d sits inside the line above's band.
    const stacked = [
      stroke(40, 122, 18, 26), stroke(36, 152, 26, 1), stroke(38, 158, 12, 26), stroke(52, 160, 12, 22),
      stroke(70, 140, 12, 44), stroke(86, 140, 20, 30), stroke(112, 140, 20, 30), stroke(138, 140, 20, 30), stroke(164, 140, 20, 30),
    ];
    const lines = groupIntoLines([...above, ...stacked]);
    expect(lines).toHaveLength(2);
    expect(lines[1].strokes).toContain(stacked[0]);
    expect(lines[0].strokes).toHaveLength(10);
  });

  it("keeps two lines apart when tall brackets overlap", () => {
    const above = written(100);
    const bracket = stroke(40, 128, 10, 50);
    const lines = groupIntoLines([...above, bracket, ...written(140, 8, 30, 54)]);
    expect(lines).toHaveLength(2);
  });

  it("gives a stroke added to an earlier line back to that line", () => {
    const first = written(100, 4);
    const second = written(160, 4);
    const added = stroke(160, 100, 20, 30);
    const lines = groupIntoLines([...first, ...second, added]);
    expect(lines).toHaveLength(2);
    expect(lines[0].strokes).toContain(added);
  });

  it("doesn't pull a lone mark far from any writing onto a line", () => {
    const lines = groupIntoLines([...written(100), stroke(500, 300, 10, 10)]);
    expect(lines).toHaveLength(2);
  });
});

describe("highlight locality", () => {
  const algebra = written(100);
  const derivative = written(140);
  const strokes = [...algebra, ...derivative];
  const scope = selectRegion(strokes, strokes.map(strokeBounds))!;
  const onDerivative = mistake("m_d", { lineId: scope.lines[1].id, isNew: true, previousStep: "d/dx(x^2 + 2x)", currentStep: "3x + 2" });
  const anchors = new Map<string, LineAnchor>([["m_d", anchorOnLine(onDerivative, scope.lines)!]]);
  const shown = run([result(found([onDerivative]))]);

  it("marks only the wrong line, however close the right one is", () => {
    const [marker] = markersFor(shown, anchors, strokes);
    const correct = groupIntoLines(strokes)[0].bounds;
    expect(marker.kind).toBe("selected");
    expect(marker.bounds.minY).toBeGreaterThan(correct.maxY);
    expect(marker.bounds.maxY).toBeLessThan(180);
  });

  it("marks nothing rather than guessing, if the mistake can't be tied to a line", () => {
    const loose = run([result(found([mistake("m_x", { lineId: null, isNew: true })]))]);
    expect(markersFor(loose, new Map(), strokes)).toEqual([]);
    expect(anchorOnLine(mistake("m_x", { lineId: null }), scope.lines)).toBeNull();
    expect(anchorOnLine(mistake("m_x", { lineId: "gone" }), scope.lines)).toBeNull();
  });

  it("two mistakes get two separate marks, the chosen one stronger", () => {
    const first = mistake("m_a", { lineId: scope.lines[0].id, isNew: true });
    const second = mistake("m_b", { lineId: scope.lines[1].id, isNew: true });
    const pair = new Map([first, second].map((m) => [m.id, anchorOnLine(m, scope.lines)!]));
    const model = run([result(found([first, second]))]);
    const marks = markersFor(model, pair, strokes);
    expect(marks.map((m) => m.kind).sort()).toEqual(["other", "selected"]);
    expect(marks[0].bounds.maxY).toBeLessThan(marks[1].bounds.minY + 12);
  });

  it("an edited line turns pending at once, and the other stays marked", () => {
    const first = mistake("m_a", { lineId: scope.lines[0].id, isNew: true });
    const second = mistake("m_b", { lineId: scope.lines[1].id, isNew: true });
    const pair = new Map([first, second].map((m) => [m.id, anchorOnLine(m, scope.lines)!]));
    const touched = touchedBy([strokeBounds(stroke(300, 145, 20, 25))], new Set(), pair);
    expect(touched).toEqual(["m_b"]);
    const model = duckReducer(run([result(found([first, second]))]), { type: "mistake_touched", id: "m_b" });
    const kinds = Object.fromEntries(markersFor(model, pair, strokes).map((m) => [m.id, m.kind]));
    expect(kinds.m_b).toBe("pending");
    expect(kinds.m_a).not.toBe("pending");
  });

  it("a dismissed mistake's mark goes", () => {
    expect(markersFor(duckReducer(shown, { type: "dismiss" }), anchors, strokes)).toEqual([]);
  });

  it("a tall stroke of the next line doesn't count as editing this one", () => {
    const line = { minX: 40, maxX: 260, minY: 100, maxY: 130 };
    expect(joinsLine({ minX: 40, maxX: 50, minY: 125, maxY: 180 }, line)).toBe(false);
    expect(joinsLine({ minX: 300, maxX: 320, minY: 105, maxY: 128 }, line)).toBe(true);
    expect(joinsLine({ minX: 40, maxX: 200, minY: 112, maxY: 118 }, line)).toBe(true);
  });

  it("erasing a stroke of the line counts, wherever it was", () => {
    expect(touchedBy([], new Set([onDerivative.lineId!, algebra[0].id]), anchors)).toEqual([]);
    expect(touchedBy([], new Set([derivative[0].id]), anchors)).toEqual(["m_d"]);
  });

  it("the mark follows the page through scrolling and resizing", () => {
    const anchor = anchors.get("m_d")!;
    const bounds = anchorBounds(anchor, strokes)!;
    const atRest = markerRect(bounds, 1, 0);
    expect(atRest.top).toBe(bounds.minY);
    const scrolled = markerRect(bounds, 1, 60);
    expect(scrolled.top).toBe(bounds.minY - 60);
    expect(scrolled.left).toBe(atRest.left);
    const wider = markerRect(bounds, 1.5, 60);
    expect(wider.top).toBe(bounds.minY * 1.5 - 60);
    expect(wider.left).toBe(bounds.minX * 1.5);
    expect(wider.width).toBe((bounds.maxX - bounds.minX) * 1.5);
  });

  it("is worked out from the strokes as they are now", () => {
    const anchor = anchors.get("m_d")!;
    const remaining = strokes.filter((s) => s !== derivative.at(-1));
    expect(anchorBounds(anchor, remaining)!.maxX).toBeLessThan(anchorBounds(anchor, strokes)!.maxX);
    expect(anchorBounds(anchor, algebra)).toBeNull();
  });
});

describe("drawing only what needs reading", () => {
  const first = written(100);
  const second = written(150);
  const third = written(200);
  const strokes = [...first, ...second, ...third];
  const all = selectRegion(strokes, strokes.map(strokeBounds))!;
  const idOf = (line: PenStroke[]) => lineId(line);

  it("draws everything the first time", () => {
    const plan = planImage(all, new Set());
    expect(plan.inkLines.every((line) => line.inImage)).toBe(true);
    expect(plan.image.lineCount).toBe(3);
  });

  it("draws only the new line and the one above it", () => {
    const plan = planImage(all, new Set([idOf(first), idOf(second)]));
    expect(plan.inkLines.map((line) => line.inImage)).toEqual([false, true, true]);
    expect(plan.image.strokes).not.toContain(first[0]);
    expect(plan.image.strokes).toContain(third[0]);
    expect(plan.image.lineCount).toBe(2);
  });

  it("an edit changes a line's id, and only that line's", () => {
    const edited = [...second.slice(0, -1), stroke(250, 150, 20, 30)];
    expect(idOf(edited)).not.toBe(idOf(second));
    expect(idOf([...first].reverse())).toBe(idOf(first));
    const page = [...first, ...edited, ...third];
    const region = selectRegion(page, [strokeBounds(edited.at(-1)!)])!;
    const plan = planImage(region, new Set([idOf(first), idOf(second), idOf(third)]));
    expect(plan.inkLines.filter((line) => line.inImage).length).toBe(2);
    expect(plan.inkLines.at(-1)?.inImage).toBe(false);
  });

  it("undoing an edit is back to lines already read", () => {
    const plan = planImage(all, new Set([idOf(first), idOf(second), idOf(third)]));
    expect(plan.inkLines.some((line) => line.inImage)).toBe(false);
    expect(plan.image.strokes.length).toBeGreaterThan(0);
  });

  it("asking directly reads it all again", () => {
    const plan = planImage(all, new Set([idOf(first), idOf(second), idOf(third)]), true);
    expect(plan.inkLines.every((line) => line.inImage)).toBe(true);
  });

  it("marks a wide gap, and not a blank line between steps", () => {
    const far = written(400);
    const near = written(180);
    const page = [...first, ...near, ...far];
    const region = selectRegion(page, page.map(strokeBounds))!;
    expect(region.lines.map((line) => line.spaced)).toEqual([false, false, true]);
  });
});

describe("what still stands when the page has moved on", () => {
  const line = (y: number): Bounds => ({ minX: 0, maxX: 300, minY: y, maxY: y + 30 });
  const spots: Record<string, Bounds> = { m_a: line(100), m_b: line(400) };
  const spotOf = (m: Mistake) => spots[m.id] ?? null;
  const a = mistake("m_a", { isNew: true });
  const b = mistake("m_b", { isNew: true });
  const crop = [line(100), line(400)];

  it("keeps it all if nothing changed", () => {
    const answer = found([a, b]);
    expect(judgeReply(answer, [], crop, spotOf)).toEqual({ result: answer, complete: true });
  });

  it("drops only the mistake whose line was edited during the check", () => {
    const judged = judgeReply(found([a, b], { resolvedMistakeIds: ["m_c"] }), [line(405)], crop, spotOf)!;
    expect(judged.result.mistakes.map((m) => m.id)).toEqual(["m_a"]);
    expect(judged.result.mistake?.id).toBe("m_a");
    expect(judged.result.resolvedMistakeIds).toEqual([]);
    expect(judged.complete).toBe(false);
  });

  it("several edits during one check don't confuse which mistake is which", () => {
    const judged = judgeReply(found([a, b]), [line(102), line(405)], crop, spotOf);
    expect(judged).toBeNull();
  });

  it("no longer quacks for a kept mistake that was already known", () => {
    const judged = judgeReply(found([known(a), b]), [line(405)], crop, spotOf)!;
    expect(judged.result.intervention.shouldQuack).toBe(false);
  });

  it("can't place a mistake with no line, so any change drops it", () => {
    const judged = judgeReply(found([mistake("m_z", { lineId: null })]), [line(405)], crop, () => null);
    expect(judged).toBeNull();
  });

  it("drops a reply about correct work if the image's ink changed", () => {
    expect(judgeReply(reply(), [line(102)], crop, spotOf)).toBeNull();
    expect(judgeReply(reply(), [line(700)], crop, spotOf)?.complete).toBe(true);
  });
});

describe("mistake identity across edits", () => {
  it("recognises a new try on the same strokes", () => {
    const strokes = written(100);
    const scope = selectRegion(strokes, strokes.map(strokeBounds))!;
    const anchors = new Map([["m_old", { lineId: "old", strokeIds: strokes.slice(0, 7).map((s) => s.id), bounds: scope.lines[0].bounds, version: 1 }]]);
    const next = mistake("m_new", { lineId: scope.lines[0].id, isNew: true });
    expect(replacements([next], ["m_old"], anchors, scope.lines)).toEqual({ m_new: "m_old" });
  });

  it("a fix elsewhere and a new mistake elsewhere are separate", () => {
    const one = written(100);
    const other = written(200);
    const scope = selectRegion([...one, ...other], [...one, ...other].map(strokeBounds))!;
    const anchors = new Map([["m_old", { lineId: "x", strokeIds: one.map((s) => s.id), bounds: scope.lines[0].bounds, version: 1 }]]);
    const next = mistake("m_new", { lineId: scope.lines[1].id, isNew: true });
    expect(replacements([next], ["m_old"], anchors, scope.lines)).toEqual({});
  });

  it("changedInk reports additions and removals", () => {
    const a = stroke(0, 0, 10, 10);
    const b = stroke(0, 50, 10, 10);
    expect(changedInk([a], [a, b])).toEqual([strokeBounds(b)]);
    expect(changedInk([a, b], [a])).toEqual([strokeBounds(b)]);
  });
});

describe("how a step is written back", () => {
  it("uses the student's signs", () => {
    expect(prettyMath("x^2 + 3 * 4")).toBe("x² + 3 × 4");
    expect(prettyMath("x^12")).toBe("x¹²");
    expect(stepLabel(mistake("m", { previousStep: "d/dx(x^2 + 2x)", currentStep: "3x + 2" }))).toBe("d/dx(x² + 2x) = 3x + 2");
  });
});
