// The physical iPad session of 2026-11-04, afternoon. `Solve 5x − 7 = 23`, answered `5x = 29`, `x = 29/5`.
// The strokes themselves weren't kept. Each stroke here is one connected piece of ink measured from the
// recording's frame at 02:45 (screen pixels, 1440 × 1920), so the shapes and gaps are the real ones. The
// fraction bar keeps its measured path, as its slope is what matters. Every other stroke is a zigzag through
// its measured box, so nothing but the bar is straight.
import { describe, expect, it } from "vitest";
import { groupIntoLines } from "@/lib/lines";
import { stepLabel } from "@/lib/mistakes";
import type { Mistake, PenStroke } from "@/lib/types";

let written = 0;
const zigzag = (id: string, minX: number, minY: number, maxX: number, maxY: number): PenStroke => {
  const at = written++;
  const height = maxY - minY;
  return {
    id,
    points: [
      { x: minX, y: minY, timestamp: at },
      { x: maxX, y: minY + height / 3, timestamp: at },
      { x: minX, y: minY + (2 * height) / 3, timestamp: at },
      { x: maxX, y: maxY, timestamp: at },
    ],
    color: "#000",
    width: 3,
    createdAt: at,
  };
};
const path = (id: string, points: [number, number][]): PenStroke => {
  const at = written++;
  return { id, points: points.map(([x, y]) => ({ x, y, timestamp: at })), color: "#000", width: 3, createdAt: at };
};

// Rising to the right, 76 wide and 23 tall: too steep for a bar's bounding box, which wants it four times as wide.
const SLANTED_BAR: [number, number][] = [[301, 842], [313, 838], [325, 836], [337, 835], [349, 832], [361, 828], [373, 822], [377, 819]];

function question(): PenStroke[] {
  return [
    zigzag("q-5", 138, 599, 165, 644), zigzag("q-x", 169, 618, 208, 650), zigzag("q-minus", 218, 610, 241, 617),
    zigzag("q-7", 242, 588, 271, 636), zigzag("q-eq1", 291, 594, 308, 600), zigzag("q-eq2", 293, 602, 313, 612),
    zigzag("q-2", 325, 579, 365, 617), zigzag("q-3", 362, 567, 407, 626),
  ];
}
function wrongStep(): PenStroke[] {
  return [
    zigzag("s-5", 172, 709, 203, 754), zigzag("s-x", 203, 730, 239, 762), zigzag("s-eq1", 245, 716, 264, 720),
    zigzag("s-eq2", 248, 726, 267, 732), zigzag("s-2", 277, 703, 322, 745), zigzag("s-9", 324, 695, 353, 742),
  ];
}
const FRACTION = ["a-num-2", "a-num-9", "a-bar", "a-den-5"];
function fractionAnswer(bar: [number, number][] = SLANTED_BAR): PenStroke[] {
  return [
    zigzag("a-x", 197, 816, 240, 849), zigzag("a-eq1", 253, 807, 271, 820), zigzag("a-eq2", 258, 819, 277, 832),
    zigzag("a-num-2", 293, 783, 339, 819), zigzag("a-num-9", 339, 774, 361, 805), path("a-bar", bar),
    zigzag("a-den-5", 329, 838, 351, 869),
  ];
}
function page(answer = fractionAnswer()): PenStroke[] {
  written = 0;
  return [...question(), ...wrongStep(), ...answer];
}

const ids = (line: { strokes: PenStroke[] }) => line.strokes.map((stroke) => stroke.id).sort();

describe("x = 29/5 written with a slanted bar, from the iPad on 2026-11-04", () => {
  it("is three lines, not five", () => {
    // On the iPad this was five lines: 29, x = with the bar, and 5 each on their own. The two readings put the
    // same words in different lines, didn't agree, and Quack said it couldn't read the writing for two minutes.
    const lines = groupIntoLines(page());
    expect(lines).toHaveLength(3);
    expect(ids(lines[0])).toEqual(question().map((stroke) => stroke.id).sort());
    expect(ids(lines[1])).toEqual(wrongStep().map((stroke) => stroke.id).sort());
    expect(ids(lines[2])).toEqual(["a-x", "a-eq1", "a-eq2", ...FRACTION].sort());
  });

  it("is the same whichever order the fraction was written in", () => {
    written = 0;
    const answer = fractionAnswer();
    const reordered = [answer[0], answer[1], answer[2], answer[5], answer[6], answer[3], answer[4]];
    const lines = groupIntoLines([...question(), ...wrongStep(), ...reordered]);
    expect(lines).toHaveLength(3);
    expect(ids(lines[2])).toEqual(["a-x", "a-eq1", "a-eq2", ...FRACTION].sort());
  });

  it("is also one line with a level bar, beside x =", () => {
    // A level bar is a bar by its box. But beside `x =`, the bar's line isn't the bar alone, and the numerator and
    // denominator are lines above and below it, which neither existing rule put back.
    const level = groupIntoLines(page(fractionAnswer([[301, 830], [340, 830], [377, 830]])));
    expect(level).toHaveLength(3);
    expect(ids(level[2])).toContain("a-den-5");
    expect(ids(level[2])).toContain("a-num-9");
  });

  it("is one line when the fraction is the whole line", () => {
    written = 0;
    const alone = [zigzag("n-2", 293, 783, 339, 819), zigzag("n-9", 339, 774, 361, 805), path("bar", SLANTED_BAR), zigzag("d-5", 329, 838, 351, 869)];
    expect(groupIntoLines([...question(), ...wrongStep(), ...alone])).toHaveLength(3);
  });
});

describe("what a slanted bar mustn't join", () => {
  it("leaves alone a line just above that has more in it than a numerator", () => {
    // `5x = 29` written right on top of `x = 29/5`: its 2 and 9 sit over the bar, but so does the rest of the
    // line, beside it. That's a line of working, not a numerator.
    written = 0;
    const cramped = [
      zigzag("s-5", 172, 755, 203, 790), zigzag("s-x", 203, 765, 239, 795), zigzag("s-eq1", 245, 770, 264, 774),
      zigzag("s-eq2", 248, 780, 267, 785), zigzag("s-2", 300, 760, 330, 795), zigzag("s-9", 335, 758, 360, 795),
    ];
    const answer = [zigzag("a-x", 197, 816, 240, 849), zigzag("a-eq1", 253, 807, 271, 820), zigzag("a-eq2", 258, 819, 277, 832), path("a-bar", SLANTED_BAR), zigzag("a-den-5", 329, 838, 351, 869)];
    const lines = groupIntoLines([...question(), ...cramped, ...answer]);
    expect(lines.some((line) => line.strokes.some((stroke) => stroke.id === "s-5") && line.strokes.some((stroke) => stroke.id === "a-bar"))).toBe(false);
  });

  it("doesn't join an underline to the lines either side of it", () => {
    // A slanted underline under `5x = 29`, then `x = 6`. The underline has nothing beside it, so it isn't a bar in
    // the middle of a line, and the line under it is too far down to be a denominator.
    written = 0;
    const underline = path("underline", [[170, 778], [240, 775], [320, 771], [360, 768]]);
    const next = [zigzag("b-x", 197, 830, 240, 863), zigzag("b-eq1", 253, 835, 271, 840), zigzag("b-eq2", 255, 848, 275, 853), zigzag("b-6", 300, 825, 325, 865)];
    const lines = groupIntoLines([...question(), ...wrongStep(), underline, ...next]);
    const answerLine = lines.find((line) => line.strokes.some((stroke) => stroke.id === "b-6"))!;
    expect(answerLine.strokes.some((stroke) => stroke.id === "s-5")).toBe(false);
  });

  it("doesn't treat a steep stroke as a bar", () => {
    // `29 / 5` written inline, with a slash: that's one line already, and the slash is no bar.
    written = 0;
    const inline = [
      zigzag("i-x", 197, 816, 240, 849), zigzag("i-eq1", 253, 820, 271, 826), zigzag("i-eq2", 255, 832, 275, 838),
      zigzag("i-2", 290, 812, 315, 850), zigzag("i-9", 318, 812, 343, 850), path("slash", [[350, 855], [362, 830], [374, 805]]), zigzag("i-5", 380, 812, 405, 850),
    ];
    const lines = groupIntoLines([...question(), ...wrongStep(), ...inline]);
    expect(lines).toHaveLength(3);
  });
});

describe("the step a hint is about", () => {
  const mistake = (category: string, previousStep: string, currentStep: string) =>
    ({ id: "m", category, errorType: "solution_set_changed", operationType: "equation", previousStep, currentStep, verified: true, region: null, lineId: null, problemId: null, parsedOperation: "", isNew: true }) as Mistake;

  it("shows an equation step as one line becoming the next", () => {
    // On the iPad: "Before your change: 5x - 7 = 23 = 5x = 29", a chain of equals that isn't true.
    expect(stepLabel(mistake("equation_solving", "5x - 7 = 23", "5x = 29"))).toBe("5x - 7 = 23  →  5x = 29");
  });

  it("does the same for any step between two equations", () => {
    expect(stepLabel(mistake("algebra", "2x = 10", "x = 4"))).toBe("2x = 10  →  x = 4");
  });

  it("still joins an expression to what it became with =", () => {
    expect(stepLabel(mistake("arithmetic", "3 * 4", "10"))).toBe("3 × 4 = 10");
  });
});

describe("asking where to start, on a first visit", async () => {
  const { shouldAskWhereToStart, hasBeenHere, choiceKey } = await import("@/lib/firstVisit");
  const memory = () => {
    const items = new Map<string, string>();
    return { getItem: (key: string) => items.get(key) ?? null, setItem: (key: string, value: string) => void items.set(key, value), items };
  };
  const algebra = (mastered: number) => ({ id: "algebra_foundations", title: "Algebra", tagline: "", status: "available", mastered, total: 7, preview: [] });
  const home = (mastered = 0, resume = false) =>
    ({ paths: [algebra(mastered)], continueLearning: resume ? { courseId: "a", courseTitle: "A", levelId: "l", levelTitle: "L", levelNumber: 1 } : null }) as never;

  it("asks someone new", async () => {
    const storage = memory();
    expect(await shouldAskWhereToStart("u", { storage, learnHome: async () => home(), notebookStrokes: async () => 0 })).toBe(true);
  });

  it("doesn't ask someone with a lesson under way, a level mastered, or notes, and remembers", async () => {
    for (const [given, strokes] of [[home(0, true), 0], [home(2), 0], [home(), 5]] as const) {
      const storage = memory();
      expect(await shouldAskWhereToStart("u", { storage, learnHome: async () => given, notebookStrokes: async () => strokes })).toBe(false);
      expect(storage.items.get(choiceKey("u"))).toBe("returning");
    }
  });

  it("asks once", async () => {
    const storage = memory();
    storage.setItem(choiceKey("u"), "learn");
    expect(await shouldAskWhereToStart("u", { storage, learnHome: async () => home(), notebookStrokes: async () => 0 })).toBe(false);
  });

  it("doesn't stop anyone at the door when the server can't be reached", async () => {
    const storage = memory();
    const offline = async () => { throw new Error("offline"); };
    expect(await shouldAskWhereToStart("u", { storage, learnHome: offline, notebookStrokes: async () => 0 })).toBe(false);
    expect(hasBeenHere(null, 0)).toBe(false);
  });

  it("doesn't hold up signing in when the server is slow", async () => {
    const storage = memory();
    const never = () => new Promise<never>(() => {});
    expect(await shouldAskWhereToStart("u", { storage, learnHome: never, notebookStrokes: async () => 0, waitMs: 20 })).toBe(false);
  });
});
