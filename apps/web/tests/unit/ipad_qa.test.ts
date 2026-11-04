// What the physical iPad session of 2026-11-02 turned up, as interaction sequences. No model and no
// browser here, so this says nothing about how real handwriting is read.
import { describe, expect, it } from "vitest";
import { activeMistakes, CHECKING_CHANGE_TEXT, duckReducer, initialDuck, nextHelpLevel, UNDECIDED_TEXT, type DuckModel } from "@/lib/duckMachine";
import { askMenu, type AskStanding } from "@/lib/lasso";
import { groupIntoLines } from "@/lib/lines";
import { isMostlyErased, linesHoldingMistakes, replacements, type LineAnchor } from "@/lib/mistakes";
import type { ScopeLine } from "@/lib/snapshot";
import type { AnalyzeResponse, AskResponse, Mistake, PenStroke } from "@/lib/types";

const box = (id: string, x: number, y: number, width: number, height: number): PenStroke => ({
  id,
  points: [{ x, y, timestamp: 0 }, { x: x + width, y: y + height, timestamp: 1 }],
  color: "#000",
  width: 0,
  createdAt: 0,
});

function reply(overrides: Partial<AnalyzeResponse> = {}): AnalyzeResponse {
  return {
    snapshotVersion: 1, requestId: null, status: "no_error", mistake: null, mistakes: [],
    intervention: { shouldQuack: false, showHintImmediately: false },
    resolvedMistakeIds: [], reason: null, stale: false, modelIssue: null, lines: [], settledLineIds: [], unreadLineIds: [],
    verifiedLineIds: [], undecidedMistakeIds: [], clarification: null, reminder: null,
    timings: { interpretMs: 0, verifyMs: 0, totalMs: 0, modelRequests: 1, costUsd: 0, cacheHit: false, stages: {}, calls: [] },
    ...overrides,
  };
}

const mistake: Mistake = {
  id: "m_a", category: "integration", errorType: "integral_without_variable", operationType: "integration",
  previousStep: "∫ x dx", currentStep: "2", verified: true, region: null, lineId: "a", problemId: null, parsedOperation: "integration", isNew: true,
};
const alerted = (): DuckModel =>
  duckReducer(initialDuck, { type: "result", trigger: "auto", result: reply({ status: "confirmed_error", mistake, mistakes: [mistake], intervention: { shouldQuack: true, showHintImmediately: false } }) });
const withHint = () => {
  const asked = duckReducer(alerted(), { type: "help_requested" });
  return duckReducer(asked, { type: "help_received", level: 1, text: "Look at your answer.", source: "template", issue: null });
};

describe("an edited line, and what's said about it", () => {
  it("a hint that arrives after the edit isn't shown, and doesn't count as seen", () => {
    let model = duckReducer(alerted(), { type: "help_requested" });
    model = duckReducer(model, { type: "mistake_touched", id: mistake.id });
    model = duckReducer({ ...model, loadingHelp: true }, { type: "help_received", level: 1, text: "Old advice.", source: "template", issue: null });
    expect(model.assistance).toEqual([]);
    expect(model.loadingHelp).toBe(false);
    expect(nextHelpLevel(model)).toBe(1);
  });

  it("says when it can't tell, until the line changes again", () => {
    let model = duckReducer(withHint(), { type: "mistake_touched", id: mistake.id });
    model = duckReducer(model, { type: "changes_undecided", ids: [mistake.id] });
    expect(model.undecided).toBe(true);
    model = duckReducer(model, { type: "mistake_touched", id: mistake.id });
    expect(model.undecided).toBe(false);
    expect(model.suspended).toBe(true);
  });

  it("only an edited line can be undecided", () => {
    expect(duckReducer(alerted(), { type: "changes_undecided", ids: [mistake.id] }).undecided).toBe(false);
  });

  it("choosing an edited mistake says what's happening, not what was wrong before", () => {
    const touched = duckReducer(alerted(), { type: "mistake_touched", id: mistake.id });
    expect(duckReducer(touched, { type: "select", id: mistake.id }).bubble).toBe(CHECKING_CHANGE_TEXT);
    const undecided = duckReducer(touched, { type: "changes_undecided", ids: [mistake.id] });
    expect(duckReducer(undecided, { type: "reopen" }).bubble).toBe(UNDECIDED_TEXT);
  });

  it("the undecided state goes with it when it's parked and comes back", () => {
    const other = { ...mistake, id: "m_other", lineId: "b" };
    let model = duckReducer(alerted(), { type: "mistake_touched", id: mistake.id });
    model = duckReducer(model, { type: "changes_undecided", ids: [mistake.id] });
    model = duckReducer(model, { type: "result", trigger: "auto", result: reply({ status: "confirmed_error", mistake: other, mistakes: [other] }) });
    expect(activeMistakes(model).find((record) => record.mistake.id === mistake.id)?.undecided).toBe(true);
    expect(duckReducer(model, { type: "select", id: mistake.id }).undecided).toBe(true);
  });

  it("a verified correction clears it, once, with its hints", () => {
    let model = duckReducer(withHint(), { type: "mistake_touched", id: mistake.id });
    model = duckReducer(model, { type: "result", trigger: "auto", result: reply({ resolvedMistakeIds: [mistake.id] }) });
    expect(model.mistake).toBeNull();
    expect(model.assistance).toEqual([]);
    expect(model.resolvedCount).toBe(1);
    expect(activeMistakes(model)).toHaveLength(0);
  });

  it("a different wrong answer takes over, with its own hints and no quack", () => {
    const next = { ...mistake, id: "m_b", errorType: "integral_exponent", currentStep: "2x" };
    let model = duckReducer(withHint(), { type: "mistake_touched", id: mistake.id });
    const quacks = model.quackCount;
    model = duckReducer(model, { type: "result", trigger: "auto", replaced: { m_b: "m_a" }, result: reply({ status: "confirmed_error", mistake: next, mistakes: [next], resolvedMistakeIds: ["m_a"] }) });
    expect(model.mistake?.id).toBe("m_b");
    expect(model.assistance).toEqual([]);
    expect(nextHelpLevel(model)).toBe(1);
    expect(model.quackCount).toBe(quacks);
    expect(activeMistakes(model)).toHaveLength(1);
    expect(model.resolvedCount).toBe(0);
  });

  it("different mistakes don't share a hint count", () => {
    const other = { ...mistake, id: "m_other", lineId: "b" };
    let model = duckReducer(withHint(), { type: "result", trigger: "auto", result: reply({ status: "confirmed_error", mistake: other, mistakes: [mistake, other] }) });
    model = duckReducer(model, { type: "select", id: "m_other" });
    expect(nextHelpLevel(model)).toBe(1);
    model = duckReducer(model, { type: "select", id: "m_a" });
    expect(nextHelpLevel(model)).toBe(2);
  });
});

describe("which line a mistake is on now", () => {
  const anchors = new Map<string, LineAnchor>([["m_a", { lineId: "old", strokeIds: ["s1", "s2", "s3"], bounds: { minX: 0, minY: 0, maxX: 1, maxY: 1 }, version: 1 }]]);
  const scope = (id: string, ...strokes: string[]): ScopeLine => ({ id, bounds: { minX: 0, minY: 0, maxX: 1, maxY: 1 }, strokes: strokes.map((name) => box(name, 0, 0, 1, 1)), joinsAbove: false, spaced: false });

  it("follows what's left of it, with the new ink, and leaves a nearby problem alone", () => {
    expect(linesHoldingMistakes(["m_a"], anchors, [scope("edited", "s1", "s2", "s9"), scope("nearby", "s7")])).toEqual({ m_a: ["edited"] });
  });

  it("says nothing if none of it is in this check", () => {
    expect(linesHoldingMistakes(["m_a"], anchors, [scope("nearby", "s7")])).toEqual({});
  });

  it("a mistake on a new problem isn't taken for the old one's next try", () => {
    const fresh = { ...mistake, id: "m_b", lineId: "nearby" };
    expect(replacements([fresh], ["m_a"], anchors, [scope("edited", "s1"), scope("nearby", "s7")])).toEqual({});
    expect(replacements([{ ...fresh, lineId: "edited" }], ["m_a"], anchors, [scope("edited", "s1")])).toEqual({ m_b: "m_a" });
  });
});

describe("a line nearly all rubbed out", () => {
  const anchor: LineAnchor = { lineId: "old", strokeIds: Array.from({ length: 12 }, (_, i) => `s${i}`), bounds: { minX: 0, minY: 0, maxX: 1, maxY: 1 }, version: 1 };
  const left = (...ids: string[]) => ids.map((id) => box(id, 0, 0, 1, 1));

  it("a stray mark or two the eraser missed aren't that line any more", () => {
    expect(isMostlyErased(anchor, left("s1", "s7"))).toBe(true);
  });

  it("rubbing out an answer leaves the line as it was", () => {
    expect(isMostlyErased(anchor, left("s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"))).toBe(false);
  });

  it("a short line isn't judged by a share of it", () => {
    expect(isMostlyErased({ ...anchor, strokeIds: ["a", "b", "c"] }, left("a"))).toBe(false);
  });
});

describe("a line with more flat strokes than digits", () => {
  // Measured from the student's 1 ÷ 4 = 0 in the screenshots.
  const divide = [box("1", 800, 238, 6, 86), box("bar", 846, 276, 48, 6), box("dot", 866, 250, 8, 8), box("dot2", 866, 300, 8, 8), box("4", 912, 232, 36, 100), box("=1", 982, 262, 22, 6), box("=2", 984, 286, 18, 6), box("0", 1062, 226, 68, 102)];

  it("stays one line, with nothing left out", () => {
    const lines = groupIntoLines(divide);
    expect(lines).toHaveLength(1);
    expect(lines[0].strokes).toHaveLength(divide.length);
  });

  it("a margin rule is still kept out of the page's lines", () => {
    const row = (y: number, name: string) => [0, 40, 80, 120].map((x, i) => box(`${name}${i}`, x, y, 30, 40));
    expect(groupIntoLines([box("rule", 5, 0, 1, 2000), ...row(0, "a"), ...row(100, "b"), ...row(200, "c")])).toHaveLength(3);
  });
});

describe("what circling offers", () => {
  const answer = (verdict: AskResponse["verdict"], level: number | null = null): AskResponse => ({ verdict, disposition: "", text: "", source: "template", level, lines: [], clarification: null, unreadLineIds: [], modelIssue: null, timings: reply().timings });
  const menu = (standing: AskStanding, reply_: AskResponse | null = null, lastAction: "hint" | "explain" | "check" | "question" | null = null) =>
    askMenu({ standing, reply: reply_, lastAction, tooMuch: false }).map((item) => item.label);
  const unknown: AskStanding = { kind: "unknown" };

  it("not yet checked: a check of just this, or to understand it, with no four-button grid", () => {
    expect(menu(unknown)).toEqual(["Check selection", "Explain this step", "Ask Quack"]);
  });

  it("known right: nothing to check, and no hint to give", () => {
    expect(menu({ kind: "verified" })).toEqual(["Explain this step", "Ask Quack"]);
  });

  it("known wrong: a hint first, labelled by what's actually been shown", () => {
    expect(menu({ kind: "mistake", hintsSeen: 0, explained: false })[0]).toBe("Give me a hint");
    expect(menu({ kind: "mistake", hintsSeen: 1, explained: false })[0]).toBe("Another hint");
    expect(menu({ kind: "mistake", hintsSeen: 2, explained: false })[0]).toBe("Explain this step");
    expect(menu({ kind: "mistake", hintsSeen: 2, explained: true })).toEqual(["Ask Quack"]);
  });

  it("a check saying something's wrong offers a first hint, not another one", () => {
    expect(menu(unknown, answer("incorrect"), "check")[0]).toBe("Give me a hint");
  });

  it("goes first hint, another hint, explain", () => {
    expect(menu(unknown, answer("incorrect", 1), "hint")[0]).toBe("Another hint");
    expect(menu(unknown, answer("incorrect", 2), "hint")[0]).toBe("Explain this step");
    expect(menu(unknown, answer("incorrect", 3), "explain")).toEqual(["Ask Quack"]);
  });

  it("right working is never offered a hint", () => {
    expect(menu(unknown, answer("correct"), "check")).toEqual(["Explain this step", "Ask Quack"]);
    expect(menu(unknown, answer("correct"), "explain")).toEqual(["Ask Quack"]);
  });

  it("a line it can't read asks for it to be written again, and doesn't offer more of the same", () => {
    expect(menu(unknown, answer("unclear"), "check")).toEqual(["Ask Quack"]);
  });

  it("too much to look at offers nothing", () => {
    expect(askMenu({ standing: unknown, reply: null, lastAction: null, tooMuch: true })).toEqual([]);
  });
});
