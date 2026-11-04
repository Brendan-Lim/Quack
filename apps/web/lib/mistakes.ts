// Where each mistake is on the page, and what that means for what's marked on it.
//
// The image sent for a check, the line a mistake was found on, and what's highlighted are three
// different things. A mistake is tied to the strokes of one written line, and its highlight is
// worked out from those strokes as they are now, in page coordinates, so scrolling and resizing
// can't move it off them. With nothing to tie it to, nothing is highlighted: a highlight on the
// wrong line is worse than none, and the duck still names the step.

import { joinsLine } from "./freshness";
import { expand, strokeBounds, unionBounds } from "./geometry";
import type { DuckModel } from "./duckMachine";
import { activeMistakes } from "./duckMachine";
import type { ScopeLine } from "./snapshot";
import type { Bounds, Mistake, PenStroke } from "./types";

// `version` is the notebook version the mistake was last seen on this line at.
export type LineAnchor = { lineId: string; strokeIds: string[]; bounds: Bounds; version: number };
// "unread" is a line Quack couldn't read, outlined so the student knows which one to rewrite. Never a verdict.
export type Marker = { id: string; bounds: Bounds; kind: "selected" | "other" | "pending" | "unread" };

const MARKER_PADDING = 6;

export function anchorOnLine(mistake: Mistake, lines: readonly ScopeLine[], version = 0): LineAnchor | null {
  const line = lines.find((candidate) => candidate.id === mistake.lineId);
  return line ? { lineId: line.id, strokeIds: line.strokes.map((stroke) => stroke.id), bounds: line.bounds, version } : null;
}

export function anchorBounds(anchor: LineAnchor, strokes: readonly PenStroke[]): Bounds | null {
  const ids = new Set(anchor.strokeIds);
  return strokes.reduce<Bounds | null>((all, stroke) => (ids.has(stroke.id) ? unionBounds(all, strokeBounds(stroke)) : all), null);
}

// Nearly all of a line has been rubbed out, with a stray mark or two the eraser missed. Those marks
// aren't that line any more, so a mistake shouldn't stay open and highlighted on them.
const MOSTLY_ERASED_SHARE = 0.25;
const MIN_STROKES_TO_JUDGE = 4;

export function isMostlyErased(anchor: LineAnchor, strokes: readonly PenStroke[]): boolean {
  if (anchor.strokeIds.length < MIN_STROKES_TO_JUDGE) return false;
  const ids = new Set(strokes.map((stroke) => stroke.id));
  const left = anchor.strokeIds.filter((id) => ids.has(id)).length;
  return left / anchor.strokeIds.length <= MOSTLY_ERASED_SHARE;
}

export function markersFor(model: DuckModel, anchors: ReadonlyMap<string, LineAnchor>, strokes: readonly PenStroke[]): Marker[] {
  return activeMistakes(model).flatMap((record): Marker[] => {
    const anchor = anchors.get(record.mistake.id);
    const bounds = anchor && anchorBounds(anchor, strokes);
    if (!bounds) return [];
    if (record.suspended) return [{ id: record.mistake.id, bounds: expand(bounds, MARKER_PADDING), kind: "pending" }];
    if (record.dismissed) return [];
    return [{ id: record.mistake.id, bounds: expand(bounds, MARKER_PADDING), kind: record.mistake.id === model.mistake?.id ? "selected" : "other" }];
  });
}

// Where a marker in page coordinates is on screen.
export function markerRect(bounds: Bounds, scale: number, scrollTop: number) {
  return {
    left: bounds.minX * scale,
    top: bounds.minY * scale - scrollTop,
    width: (bounds.maxX - bounds.minX) * scale,
    height: (bounds.maxY - bounds.minY) * scale,
  };
}

// Which mistakes an edit touches: their own line, and only that.
export function touchedBy(
  added: readonly Bounds[],
  removedIds: ReadonlySet<string>,
  anchors: ReadonlyMap<string, LineAnchor>,
): string[] {
  return [...anchors.entries()].flatMap(([id, anchor]) => {
    const lost = anchor.strokeIds.some((strokeId) => removedIds.has(strokeId));
    return lost || added.some((change) => joinsLine(change, anchor.bounds)) ? [id] : [];
  });
}

// Which of the lines in a check hold what each mistake was found on, as they are now. A new stroke
// on that line is part of it, and so is whatever's left of it when part has been rubbed out.
export function linesHoldingMistakes(ids: readonly string[], anchors: ReadonlyMap<string, LineAnchor>, lines: readonly ScopeLine[]): Record<string, string[]> {
  const found: Record<string, string[]> = {};
  for (const id of ids) {
    const anchor = anchors.get(id);
    if (!anchor) continue;
    const strokes = new Set(anchor.strokeIds);
    const holding = lines.filter((line) => line.strokes.some((stroke) => strokes.has(stroke.id))).map((line) => line.id);
    if (holding.length) found[id] = holding;
  }
  return found;
}

// A new mistake on the strokes of one just gone is the student's next try at the same line. It's
// not a fix, so no "That's it", and not a new problem, so no quack.
export function replacements(mistakes: readonly Mistake[], resolved: readonly string[], anchors: ReadonlyMap<string, LineAnchor>, lines: readonly ScopeLine[]): Record<string, string> {
  const found: Record<string, string> = {};
  for (const mistake of mistakes) {
    if (!mistake.isNew) continue;
    const line = lines.find((candidate) => candidate.id === mistake.lineId);
    if (!line) continue;
    const ids = new Set(line.strokes.map((stroke) => stroke.id));
    const earlier = resolved.find((id) => anchors.get(id)?.strokeIds.some((strokeId) => ids.has(strokeId)));
    if (earlier) found[mistake.id] = earlier;
  }
  return found;
}

// A mistake's line was erased, probably to be written out again. Until a check has seen what's
// there now, nothing can be said about it. This remembers where it was, for a while.
export type ErasedLine = { bounds: Bounds; at: number; mistakeId?: string };
export const ERASED_LINE_MS = 60_000;

export function rewrittenLine(erased: ErasedLine, lines: readonly ScopeLine[], now: number): ScopeLine | null {
  if (now - erased.at > ERASED_LINE_MS) return null;
  return lines.find((line) => joinsLine(line.bounds, erased.bounds)) ?? null;
}

const SUPERSCRIPTS: Record<string, string> = { "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹" };
const SUBSCRIPTS: Record<string, string> = { "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉" };

// Maths as a student would write it: x² rather than x^2, and × rather than *.
export function prettyMath(text: string): string {
  return text
    .replace(/\^(\d+)/g, (_, digits: string) => [...digits].map((digit) => SUPERSCRIPTS[digit]).join(""))
    .replace(/_(\d+)/g, (_, digits: string) => [...digits].map((digit) => SUBSCRIPTS[digit]).join(""))
    .replace(/\s\*\s/g, " × ");
}

// A step from one line to the next. Joining two equations with `=` wrote a chain that isn't true: on the iPad on
// 2026-11-04 the hint panel said "5x − 7 = 23 = 5x = 29". An equation step is shown as one line becoming the next.
const RELATION = /[=<>≤≥≠≈]/;

export function stepLabel(mistake: Mistake): string {
  const previous = mistake.previousStep.trim();
  const current = mistake.currentStep.trim();
  if (!previous) return prettyMath(current);
  if (mistake.category === "equation_solving" || RELATION.test(previous) || RELATION.test(current)) {
    return `${prettyMath(previous)}  →  ${prettyMath(current)}`;
  }
  return prettyMath(`${previous} = ${current}`);
}

// A line that's been written on since isn't the line that was found wrong, so it has to be read again.
export function strokesUnderMistakes(ids: readonly string[], anchors: ReadonlyMap<string, LineAnchor>, open: readonly string[], linesOnPage: ReadonlySet<string>): string | null {
  const circled = new Set(ids);
  return open.find((id) => {
    const anchor = anchors.get(id);
    return anchor && linesOnPage.has(anchor.lineId) && anchor.strokeIds.some((stroke) => circled.has(stroke));
  }) ?? null;
}
