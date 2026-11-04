// Which strokes a drawn loop goes around, worked out in page coordinates, so that scrolling, resizing
// and the screen's density can't change the answer. Nothing here touches the notes.

import { groupIntoLines } from "./lines";
import { lineId, regionOfLines, type Region, type ScopeLine } from "./snapshot";
import type { AskAction, AskResponse, InkLine, PenStroke } from "./types";

export type LassoPoint = { x: number; y: number };

// Less than this, in page units, was a tap or a slip of the pen rather than a circle.
const MIN_LASSO_AREA = 150;
const MIN_LASSO_POINTS = 4;
// A stroke counts if about half of it is inside, so a loop may clip the end of a neighbouring stroke.
const SHARE_INSIDE = 0.5;
// A long straight stroke can have just two points, which says nothing about what lies between them.
const SAMPLE_SPACING = 6;
export const MAX_SELECTED_LINES = 8;
const CONTEXT_ABOVE = 2;
const CONTEXT_GAP_IN_LINE_HEIGHTS = 3;

function area(polygon: readonly LassoPoint[]): number {
  let total = 0;
  for (let i = 0; i < polygon.length; i++) {
    const a = polygon[i];
    const b = polygon[(i + 1) % polygon.length];
    total += a.x * b.y - b.x * a.y;
  }
  return Math.abs(total) / 2;
}

export function isLasso(polygon: readonly LassoPoint[]): boolean {
  return polygon.length >= MIN_LASSO_POINTS && area(polygon) >= MIN_LASSO_AREA;
}

// An open loop counts as closed, as it's what the student meant by it. It's counted by winding, so
// going round twice is still round it.
export function pointInLasso(point: LassoPoint, polygon: readonly LassoPoint[]): boolean {
  let winding = 0;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[j];
    const b = polygon[i];
    const side = (b.x - a.x) * (point.y - a.y) - (point.x - a.x) * (b.y - a.y);
    if (a.y <= point.y && b.y > point.y && side > 0) winding++;
    else if (a.y > point.y && b.y <= point.y && side < 0) winding--;
  }
  return winding !== 0;
}

function samples(stroke: PenStroke): LassoPoint[] {
  const out: LassoPoint[] = [];
  stroke.points.forEach((point, index) => {
    const next = stroke.points[index + 1];
    out.push(point);
    if (!next) return;
    const steps = Math.floor(Math.hypot(next.x - point.x, next.y - point.y) / SAMPLE_SPACING);
    for (let step = 1; step < steps; step++) {
      out.push({ x: point.x + ((next.x - point.x) * step) / steps, y: point.y + ((next.y - point.y) * step) / steps });
    }
  });
  return out;
}

export function strokesInLasso(strokes: readonly PenStroke[], polygon: readonly LassoPoint[]): string[] {
  if (!isLasso(polygon)) return [];
  return strokes
    .filter((stroke) => {
      const points = samples(stroke);
      if (!points.length) return false;
      return points.filter((point) => pointInLasso(point, polygon)).length / points.length >= SHARE_INSIDE;
    })
    .map((stroke) => stroke.id);
}

export type SelectedWorking = {
  // The circled lines, and the lines above that they may follow from.
  region: Region;
  selectedLineIds: string[];
  // Those where the loop only took in part of what's written.
  partialLineIds: string[];
  // Too much to ask about at once.
  tooMuch: boolean;
};

// A whole line goes by the same id as it has in a check, so what's remembered of it can be used. Part
// of a line is only that part. A line above is there as context, and doesn't count as circled.
export function selectedWorking(strokes: readonly PenStroke[], ids: readonly string[]): SelectedWorking | null {
  const chosen = new Set(ids);
  const lines = groupIntoLines([...strokes]);
  const heights = lines.map((line) => line.bounds.maxY - line.bounds.minY).sort((a, b) => a - b);
  const lineHeight = heights[Math.floor(heights.length / 2)] ?? 0;
  const circled: ScopeLine[] = [];
  const partialLineIds: string[] = [];
  let firstIndex = -1;
  lines.forEach((line, index) => {
    const inside = line.strokes.filter((stroke) => chosen.has(stroke.id));
    if (!inside.length) return;
    if (firstIndex < 0) firstIndex = index;
    const whole = inside.length === line.strokes.length;
    if (!whole) partialLineIds.push(lineId(inside));
    circled.push({
      id: lineId(inside),
      bounds: whole ? line.bounds : boundsOf(inside),
      strokes: inside,
      joinsAbove: circled.length > 0,
      spaced: false,
    });
  });
  if (!circled.length) return null;
  const above: ScopeLine[] = [];
  for (let index = firstIndex - 1, count = 0; index >= 0 && count < CONTEXT_ABOVE; index--, count++) {
    const gap = (above[0]?.bounds ?? circled[0].bounds).minY - lines[index].bounds.maxY;
    if (gap > lineHeight * CONTEXT_GAP_IN_LINE_HEIGHTS) break;
    above.unshift({ id: lineId(lines[index].strokes), bounds: lines[index].bounds, strokes: lines[index].strokes, joinsAbove: above.length > 0, spaced: false });
  }
  if (above.length) circled[0] = { ...circled[0], joinsAbove: true };
  return {
    region: regionOfLines([...above, ...circled]),
    selectedLineIds: circled.map((line) => line.id),
    partialLineIds,
    tooMuch: circled.length > MAX_SELECTED_LINES,
  };
}

function boundsOf(strokes: readonly PenStroke[]) {
  const xs = strokes.flatMap((stroke) => stroke.points.map((point) => point.x));
  const ys = strokes.flatMap((stroke) => stroke.points.map((point) => point.y));
  return { minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys) };
}

export function allDrawn(lines: readonly InkLine[]): InkLine[] {
  return lines.map((line) => ({ ...line, inImage: true }));
}

// What's known about what's circled before anything is asked. See docs/CIRCLE_TO_ASK.md.
export type AskStanding = { kind: "unknown" | "verified" } | { kind: "mistake"; hintsSeen: number; explained: boolean };
export type AskMenuItem = { action: AskAction | "open_question"; label: string; testId: string };

const CHECK_ITEM: AskMenuItem = { action: "check", label: "Check selection", testId: "ask-check" };
const EXPLAIN_ITEM: AskMenuItem = { action: "explain", label: "Explain this step", testId: "ask-explain" };
const ASK_ITEM: AskMenuItem = { action: "open_question", label: "Ask Quack", testId: "ask-open-question" };

function hintItem(seen: number): AskMenuItem {
  return { action: "hint", label: seen ? "Another hint" : "Give me a hint", testId: "ask-hint" };
}

// Only what would tell the student something they don't know yet, most useful first. The wording of
// the hint button goes by what's actually been shown, and a check's answer isn't a hint.
export function askMenu(input: { standing: AskStanding; reply: AskResponse | null; lastAction: AskAction | null; tooMuch: boolean }): AskMenuItem[] {
  const { standing, reply, lastAction, tooMuch } = input;
  if (tooMuch || reply?.clarification) return [];
  if (!reply) {
    if (standing.kind === "mistake") {
      if (standing.explained) return [ASK_ITEM];
      return standing.hintsSeen < 2 ? [hintItem(standing.hintsSeen), EXPLAIN_ITEM, ASK_ITEM] : [EXPLAIN_ITEM, ASK_ITEM];
    }
    return standing.kind === "verified" ? [EXPLAIN_ITEM, ASK_ITEM] : [CHECK_ITEM, EXPLAIN_ITEM, ASK_ITEM];
  }
  const explainedAlready = lastAction === "explain";
  if (reply.verdict === "incorrect") {
    if (reply.level === 3 || explainedAlready) return [ASK_ITEM];
    if (reply.level === 2) return [EXPLAIN_ITEM, ASK_ITEM];
    return [hintItem(reply.level === 1 ? 1 : 0), EXPLAIN_ITEM, ASK_ITEM];
  }
  if (["correct", "unchecked", "unsupported"].includes(reply.verdict) && !explainedAlready) return [EXPLAIN_ITEM, ASK_ITEM];
  return [ASK_ITEM];
}

export type ScreenRect = { left: number; top: number; right: number; bottom: number };

const PANEL_GAP = 12;
const SIDE_GUTTER = 12;

// Below what's circled if there's room, then above, and otherwise over the page's corner. It never
// runs off the screen. The student is there to read what's circled, so it's kept clear of that.
export function placePanel(circled: ScreenRect, viewport: { width: number; height: number }, panel: { width: number; height: number }) {
  const left = Math.max(SIDE_GUTTER, Math.min(circled.left, viewport.width - panel.width - SIDE_GUTTER));
  const below = circled.bottom + PANEL_GAP;
  const above = circled.top - PANEL_GAP - panel.height;
  if (below + panel.height <= viewport.height - SIDE_GUTTER) return { left, top: below };
  if (above >= SIDE_GUTTER) return { left, top: above };
  return { left, top: Math.max(SIDE_GUTTER, viewport.height - panel.height - SIDE_GUTTER) };
}
