// Does a reply still describe the page? Writing goes on while a check runs, so the answer is
// decided by which ink changed, not by whether anything did.
//
// - Nothing changed near a reported mistake, and nothing changed where the image was taken from:
//   the whole reply stands.
// - Only lines well below the mistake changed: the alert stands, because the student has moved on
//   and later writing can't change whether that step is wrong. What the reply says was fixed is
//   dropped, as the student may have just rewritten it.
// - Anything changed on the mistake's line, near it, or above it in the image: nothing stands.
//   Earlier lines are what the step came from, such as a definition two lines up, and the
//   lines just below may be the student's own correction, which only a reading that includes
//   them can recognise.
//
// Lines are compared as full-width bands, since writing appended at the end of a line is outside
// the line's old bounds but still changes what it says.

import { strokeBounds } from "./geometry";
import type { AnalyzeResponse, Bounds, Mistake, PenStroke } from "./types";

export type Freshness = "current" | "alert_only" | "stale";

// Ink this close above or below a line counts as part of it, such as a superscript or a crossing out.
const LINE_MARGIN = 8;
// How far from a mistake's line ink counts as next to it: its own height and a half, or three
// lines of the ruled paper, as small writing leaves gaps much bigger than itself.
const NEIGHBOURING_LINES = 1.5;
const NEIGHBOURING_RULED = 3 * 44;

export function changedInk(before: readonly PenStroke[], after: readonly PenStroke[]): Bounds[] {
  if (before === after) return [];
  const was = new Set(before.map((stroke) => stroke.id));
  const is = new Set(after.map((stroke) => stroke.id));
  return [
    ...after.filter((stroke) => !was.has(stroke.id)),
    ...before.filter((stroke) => !is.has(stroke.id)),
  ].map(strokeBounds);
}

export function touchesBand(changes: readonly Bounds[], area: Bounds, margin = LINE_MARGIN): boolean {
  return changes.some((change) => change.minY <= area.maxY + margin && change.maxY >= area.minY - margin);
}

// `mistake` is where the reply says the mistake is, if it reports one. `null` as the spot means it
// reports one but can't say where.
export function judgeFreshness(
  changes: readonly Bounds[],
  crop: readonly Bounds[],
  mistake: { spot: Bounds | null } | null,
): Freshness {
  if (!changes.length) return "current";
  if (mistake) {
    if (!mistake.spot) return "stale";
    const { spot } = mistake;
    const reach = Math.max((spot.maxY - spot.minY) * NEIGHBOURING_LINES, NEIGHBOURING_RULED);
    if (touchesBand(changes, spot, reach)) return "stale";
    const inImage = changes.filter((change) => crop.some((band) => touchesBand([change], band)));
    if (!inImage.length) return "current";
    return inImage.some((change) => change.minY < spot.minY) ? "stale" : "alert_only";
  }
  if (!crop.some((band) => touchesBand(changes, band))) return "current";
  return "stale";
}

// A new stroke has joined a line, as opposed to being merely near it. Tight handwriting leaves the
// tall strokes of one line overlapping the next, and that says nothing about the line next door.
export function joinsLine(change: Bounds, line: Bounds): boolean {
  const center = (change.minY + change.maxY) / 2;
  if (center >= line.minY && center <= line.maxY) return true;
  const overlap = Math.min(change.maxY, line.maxY) - Math.max(change.minY, line.minY);
  const smaller = Math.min(change.maxY - change.minY, line.maxY - line.minY);
  return smaller > 0 && overlap / smaller >= 0.5;
}

// How much of a reply still stands, a mistake at a time. One mistake's line being edited says
// nothing about another's. What's fixed is only trusted if nothing it could depend on moved.
export function judgeReply(
  result: AnalyzeResponse,
  changes: readonly Bounds[],
  crop: readonly Bounds[],
  spotOf: (mistake: Mistake) => Bounds | null,
): { result: AnalyzeResponse; complete: boolean } | null {
  const mistakes = result.status === "confirmed_error" ? result.mistakes : [];
  if (!mistakes.length) {
    return judgeFreshness(changes, crop, result.status === "confirmed_error" ? { spot: null } : null) === "current" ? { result, complete: true } : null;
  }
  const verdicts = mistakes.map((mistake) => judgeFreshness(changes, crop, { spot: spotOf(mistake) }));
  const kept = mistakes.filter((_, index) => verdicts[index] !== "stale");
  if (!kept.length) return null;
  if (verdicts.every((verdict) => verdict === "current")) return { result, complete: true };
  const shown = kept.find((mistake) => mistake.id === result.mistake?.id) ?? kept.find((mistake) => mistake.isNew) ?? kept[0];
  return {
    result: {
      ...result,
      mistake: shown,
      mistakes: kept,
      resolvedMistakeIds: [],
      intervention: { ...result.intervention, shouldQuack: kept.some((mistake) => mistake.isNew) },
    },
    complete: false,
  };
}
