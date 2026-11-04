// The second physical iPad session of 2026-11-02. Stroke boxes here are measured from its
// screenshots, as the strokes themselves weren't kept, so they stand in for the real ink.
import { describe, expect, it } from "vitest";
import { groupIntoLines } from "@/lib/lines";
import { buildFixture } from "@/lib/qaFixture";
import { lineId } from "@/lib/snapshot";
import type { PenStroke } from "@/lib/types";

let written = 0;
const box = (id: string, x: number, y: number, width: number, height: number): PenStroke => ({
  id,
  points: [{ x, y, timestamp: written }, { x: x + width, y: y + height, timestamp: written + 1 }],
  color: "#000",
  width: 0,
  createdAt: written++,
});

const lineOf = (lines: ReturnType<typeof groupIntoLines>, id: string) => lines.findIndex((line) => line.strokes.some((stroke) => stroke.id === id));

// `∫ x dx = 2`, its answer rubbed out and rewritten as x²/2 + C, as in screenshots 6 to 8. The rewrite is
// taller than the line, and its + and C sit level with the fraction bar, below the rest.
function rewrittenIntegral(): PenStroke[] {
  written = 0;
  return [
    box("integral", 565, 130, 30, 115),
    box("x1", 620, 165, 38, 70), box("x2", 622, 195, 30, 40),
    box("d-stem", 700, 140, 6, 105), box("d-bowl", 688, 215, 20, 30),
    box("dx1", 705, 215, 60, 33), box("dx2", 708, 218, 45, 28),
    box("eq1", 780, 193, 32, 4), box("eq2", 782, 211, 30, 4),
    box("num1", 930, 195, 65, 50), box("num2", 935, 200, 55, 45), box("power", 996, 168, 26, 27),
    box("bar", 928, 250, 117, 6), box("denominator", 970, 285, 62, 43),
    box("plus-across", 1097, 252, 42, 4), box("plus-down", 1116, 228, 4, 44),
    box("C", 1152, 225, 98, 110),
  ];
}

describe("a tall answer written into a line", () => {
  it("keeps x²/2 + C on the line with ∫ x dx =", () => {
    const lines = groupIntoLines(rewrittenIntegral());
    expect(lines).toHaveLength(1);
  });

  it("does the same without the + C, as a bar on its own line", () => {
    const strokes = rewrittenIntegral().filter((stroke) => !["plus-across", "plus-down", "C"].includes(stroke.id));
    expect(groupIntoLines(strokes)).toHaveLength(1);
  });

  it("and with the + C rubbed out and written again, as it's the same shapes in a later order", () => {
    const strokes = rewrittenIntegral();
    const later = [...strokes.filter((stroke) => !["plus-across", "plus-down", "C"].includes(stroke.id)), ...strokes.filter((stroke) => ["plus-across", "plus-down", "C"].includes(stroke.id))];
    expect(groupIntoLines(later)).toHaveLength(1);
  });

  it("doesn't take in the line written under it", () => {
    // Screenshot 2: ∫ x dx = x²/2 + C, with d/dx(cos(x)) = -sin(x) on the next line.
    written = 0;
    const page = [
      box("integral", 540, 75, 30, 120), box("x", 585, 165, 45, 40), box("d-stem", 683, 80, 6, 105), box("dx", 670, 165, 60, 20),
      box("eq1", 748, 148, 27, 4), box("eq2", 750, 160, 25, 4),
      box("num", 808, 85, 50, 35), box("power", 855, 70, 25, 22), box("bar", 805, 147, 65, 5), box("den", 820, 183, 40, 39),
      box("plus-across", 908, 155, 42, 4), box("plus-down", 927, 135, 4, 42), box("C", 968, 125, 45, 75),
      box("d", 565, 255, 20, 45), box("dbar", 533, 312, 77, 5), box("dx2", 533, 330, 70, 30),
      box("open", 665, 255, 30, 125), box("cos", 738, 310, 107, 45), box("x2", 850, 315, 40, 35), box("close", 930, 255, 40, 125),
      box("eq3", 985, 300, 25, 4), box("eq4", 987, 313, 23, 4), box("minus", 1030, 300, 25, 4), box("sin", 1060, 270, 110, 70), box("x3", 1200, 290, 100, 60),
    ];
    const lines = groupIntoLines(page);
    expect(lines).toHaveLength(2);
    expect(lineOf(lines, "C")).toBe(lineOf(lines, "integral"));
    expect(lineOf(lines, "den")).toBe(lineOf(lines, "integral"));
    expect(lineOf(lines, "dx2")).toBe(lineOf(lines, "cos"));
  });

  it("leaves two close lines of working with minus signs apart", () => {
    written = 0;
    const page = [
      box("a-x", 100, 100, 30, 40), box("a-minus", 140, 120, 20, 4), box("a-3", 170, 100, 25, 40), box("a-eq", 205, 118, 25, 6), box("a-5", 240, 100, 25, 40),
      box("b-2", 100, 150, 25, 40), box("b-x", 128, 150, 28, 40), box("b-minus", 165, 170, 20, 4), box("b-6", 195, 150, 25, 40), box("b-eq", 230, 168, 25, 6), box("b-10", 265, 150, 40, 40),
    ];
    expect(groupIntoLines(page)).toHaveLength(2);
  });
});

describe("a QA fixture", () => {
  it("keeps only what was circled, grouped as checks group it, with nothing that identifies the student", () => {
    const strokes = rewrittenIntegral();
    const other = box("elsewhere", 100, 600, 30, 40);
    const fixture = buildFixture([...strokes, other], strokes.map((stroke) => stroke.id), 7, null, new Date(0));
    expect(fixture.scope).toBe("circled");
    expect(fixture.strokes.map((stroke) => stroke.id)).not.toContain("elsewhere");
    expect(fixture.lines).toHaveLength(1);
    // Played back, the same strokes make the same line, with the same id a check would have used.
    expect(groupIntoLines(fixture.strokes).map((line) => lineId(line.strokes))).toEqual(fixture.lines.map((line) => line.id));
    const text = JSON.stringify(fixture);
    expect(text).not.toMatch(/session|token|authori[sz]ation|email/i);
  });

  it("is the whole page when nothing is circled", () => {
    const fixture = buildFixture(rewrittenIntegral(), null, 7);
    expect(fixture.scope).toBe("page");
  });
});
