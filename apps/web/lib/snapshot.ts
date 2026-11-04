// Decide which part of the page to check, and turn it into an image.
//
// Only recent lines are sent, both to keep latency and cost down and because students
// write personal things in notebooks that have no business leaving the device. Lines that
// aren't chosen are never drawn, so writing between two chosen places stays private.

import { expand, inkLength, intersects, strokeBounds, unionBounds } from "./geometry";
import { groupIntoLines, type TextLine } from "./lines";
import { strokePath } from "./ink";
import type { Bounds, PenStroke } from "./types";

export const CONTEXT_LINES = 3;
export const MAX_GAP_IN_LINE_HEIGHTS = 3;
export const MIN_INK_TO_ANALYZE = 40;
export const MAX_LINES = 14;
const CROP_PADDING = 24;
const BAND_GAP = 70;
const MAX_EDGE_PX = 1600;
const TARGET_SCALE = 1.5;
// With known mistakes filling the crop, new writing still gets this many lines of it.
const LINES_RESERVED_FOR_CHANGES = 4;
// Beyond a blank line or so. Only then can a gap on the page say that lines don't follow on.
const SPACED_IN_LINE_HEIGHTS = 2.5;

export type Band = { bounds: Bounds; strokes: PenStroke[] };
// One written line in the working being checked. The id names exactly these strokes.
// `joinsAbove`: the line above is close enough to be context. `spaced`: a wide gap above it.
export type ScopeLine = { id: string; bounds: Bounds; strokes: PenStroke[]; joinsAbove: boolean; spaced: boolean };
// `overflow` is the changed writing that didn't fit, to go in a later check.
export type Region = { bands: Band[]; strokes: PenStroke[]; lineCount: number; overflow: Bounds[]; lines: ScopeLine[]; pageLineCount?: number };
// What goes in the image, and what the server is told about every line in the working.
export type ImagePlan = { image: Region; inkLines: { id: string; inImage: boolean; spaced: boolean }[] };

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)] ?? 0;
}

function gapBetween(a: TextLine, b: TextLine): number {
  return b.bounds.minY - a.bounds.maxY;
}

function nearestLines(lines: TextLine[], dirty: Bounds, maxGap: number): number[] {
  // Nothing is left where the page changed, which means something was erased. Check the lines
  // around the gap, as the erased line may have been the mistake, but only if they're close.
  const below = lines.findIndex((line) => line.bounds.minY >= dirty.maxY);
  const candidates = below === -1 ? [lines.length - 1] : below === 0 ? [0] : [below - 1, below];
  return candidates.filter((index) => {
    const line = lines[index];
    const gap = line.bounds.minY >= dirty.maxY ? line.bounds.minY - dirty.maxY : dirty.minY - line.bounds.maxY;
    return gap <= maxGap;
  });
}

// `keep` holds the lines of mistakes still on the page. They're always included, because "no
// error here" can only clear a mistake if the check could see it.
export function selectRegion(strokes: PenStroke[], dirty: Bounds[], keep: Bounds[] = []): Region | null {
  if (!strokes.length) return null;
  const lines = groupIntoLines(strokes);
  if (!lines.length) return null;
  const lineHeight = median(lines.map((line) => line.bounds.maxY - line.bounds.minY));
  const maxGap = lineHeight * MAX_GAP_IN_LINE_HEIGHTS;

  const changed = new Set<number>();
  for (const area of dirty) {
    const hits = lines.flatMap((line, index) => (intersects(line.bounds, expand(area, 4)) ? [index] : []));
    (hits.length ? hits : nearestLines(lines, area, maxGap)).forEach((index) => changed.add(index));
  }
  const kept = new Set<number>();
  for (const area of keep) {
    lines.forEach((line, index) => {
      if (intersects(line.bounds, area)) kept.add(index);
    });
  }

  // The crop has a limit, and the lower lines are the more recent ones. Never cut a known mistake
  // to make room for context, and don't let known mistakes starve what the student just wrote.
  const newestFirst = (a: number, b: number) => b - a;
  const newWork = [...changed].filter((index) => !kept.has(index)).sort(newestFirst);
  const reserved = Math.min(newWork.length, LINES_RESERVED_FOR_CHANGES);
  const editedFirst = (a: number, b: number) => Number(changed.has(b)) - Number(changed.has(a)) || b - a;
  const core = new Set([...kept].sort(editedFirst).slice(0, MAX_LINES - reserved));
  for (const index of newWork) {
    if (core.size >= MAX_LINES) break;
    core.add(index);
  }
  if (!core.size) return null;

  // Context around what changed: lines above for working that continues, lines below because
  // an edit to earlier working can affect them. Nearest first, in case the budget runs out.
  const chosen = new Set(core);
  for (let reach = 1; reach <= CONTEXT_LINES; reach++) {
    for (const index of [...core]) {
      if (chosen.size >= MAX_LINES) break;
      const up = index - reach;
      if (up >= 0 && isConnected(lines, up, index, maxGap)) chosen.add(up);
      const down = index + reach;
      if (down < lines.length && chosen.size < MAX_LINES && isConnected(lines, index, down, maxGap)) chosen.add(down);
    }
  }

  const ordered = [...chosen].sort((a, b) => a - b);
  const scope = ordered.map((index, position): ScopeLine => {
    const gap = index > 0 ? gapBetween(lines[index - 1], lines[index]) : 0;
    return {
      id: lineId(lines[index].strokes),
      bounds: lines[index].bounds,
      strokes: lines[index].strokes,
      joinsAbove: position > 0 && ordered[position - 1] === index - 1 && gap <= maxGap,
      spaced: gap > lineHeight * SPACED_IN_LINE_HEIGHTS,
    };
  });
  const bands = bandsOf(scope);
  const overflow = [...changed].filter((index) => !chosen.has(index)).map((index) => lines[index].bounds);
  return { bands, strokes: bands.flatMap((band) => band.strokes), lineCount: ordered.length, overflow, lines: scope, pageLineCount: lines.length };
}

// Some lines picked out by other means, the way a check would have them.
export function regionOfLines(lines: ScopeLine[]): Region {
  const bands = bandsOf(lines);
  return { bands, strokes: bands.flatMap((band) => band.strokes), lineCount: lines.length, overflow: [], lines };
}

function bandsOf(lines: ScopeLine[]): Band[] {
  const bands: Band[] = [];
  lines.forEach((line, position) => {
    const last = bands.at(-1);
    if (last && position > 0 && line.joinsAbove) {
      last.bounds = unionBounds(last.bounds, line.bounds)!;
      last.strokes.push(...line.strokes);
    } else {
      bands.push({ bounds: line.bounds, strokes: [...line.strokes] });
    }
  });
  return bands;
}

// Names exactly these strokes, in any order. Undoing an edit brings back the same id.
export function lineId(strokes: PenStroke[]): string {
  const hash = (seed: number) => {
    let value = seed;
    for (const id of strokes.map((stroke) => stroke.id).sort()) {
      for (let i = 0; i < id.length; i++) value = Math.imul(value ^ id.charCodeAt(i), 0x01000193);
      value = Math.imul(value ^ 0x2c, 0x01000193);
    }
    return (value >>> 0).toString(36);
  };
  return `${hash(0x811c9dc5)}${hash(0x1b873593)}`;
}

// Only what hasn't been read and agreed on needs drawing, with the line above it, which says how it
// relates to that line. Everything else is remembered on the server by its id. When nothing needs
// reading the image goes unread, and the server checks from memory.
export function planImage(region: Region, settled: ReadonlySet<string>, readAll = false): ImagePlan {
  const lines = region.lines;
  const drawn = new Set<number>();
  lines.forEach((line, position) => {
    if (readAll || !settled.has(line.id)) {
      drawn.add(position);
      if (line.joinsAbove) drawn.add(position - 1);
    }
  });
  const inkLines = lines.map((line, position) => ({ id: line.id, inImage: drawn.has(position), spaced: line.spaced }));
  const picked = drawn.size ? lines.filter((_, position) => drawn.has(position)) : lines.slice(-1);
  const inImage = picked.map((line, position) => ({
    ...line,
    joinsAbove: position > 0 && line.joinsAbove && lines[lines.indexOf(line) - 1] === picked[position - 1],
  }));
  const bands = bandsOf(inImage);
  return {
    image: { bands, strokes: bands.flatMap((band) => band.strokes), lineCount: inImage.length, overflow: [], lines: inImage },
    inkLines,
  };
}

// A line that was drawn and couldn't be settled, because the two readings differ or either is
// unsure, may be hiding a mistake. Nothing else would bring it back: once the student has moved on
// it drops out of what's checked. So it's looked at again, a few times, and then left alone, as a
// scribble never will settle and each look costs money.
export const MAX_LINE_RECHECKS = 2;

export function linesToLookAgainAt(inkLines: readonly { id: string; inImage: boolean }[], settled: ReadonlySet<string>, tries: ReadonlyMap<string, number>): string[] {
  return inkLines.filter((line) => line.inImage && !settled.has(line.id) && (tries.get(line.id) ?? 0) < MAX_LINE_RECHECKS).map((line) => line.id);
}

// Which try this is, for the most-tried line being drawn. Above zero, it gets new readings.
export function attemptFor(inkLines: readonly { id: string; inImage: boolean }[], tries: ReadonlyMap<string, number>): number {
  return Math.max(0, ...inkLines.filter((line) => line.inImage).map((line) => tries.get(line.id) ?? 0));
}

// Could this image show everything written in this area? If not, it can't say the area is fixed.
export function regionCovers(region: Region, strokes: PenStroke[], area: Bounds): boolean {
  const included = new Set(region.strokes);
  return strokes.every((stroke) => included.has(stroke) || !intersects(strokeBounds(stroke), area));
}

function isConnected(lines: TextLine[], from: number, to: number, maxGap: number): boolean {
  for (let index = from; index < to; index++) {
    if (gapBetween(lines[index], lines[index + 1]) > maxGap) return false;
  }
  return true;
}

export function isWorthAnalyzing(region: Region): boolean {
  return inkLength(region.strokes) >= MIN_INK_TO_ANALYZE;
}

type Layout = { width: number; height: number; originX: number; offsets: number[] };

// Bands are stacked with a fixed gap, so far-apart lines don't shrink everything else.
function layoutOf(region: Region): Layout {
  const originX = Math.min(...region.bands.map((band) => band.bounds.minX)) - CROP_PADDING;
  const right = Math.max(...region.bands.map((band) => band.bounds.maxX)) + CROP_PADDING;
  const offsets: number[] = [];
  let y = CROP_PADDING;
  for (const band of region.bands) {
    offsets.push(y);
    y += band.bounds.maxY - band.bounds.minY + BAND_GAP;
  }
  return { width: right - originX, height: y - BAND_GAP + CROP_PADDING, originX, offsets };
}

export function snapshotScale(width: number, height: number): number {
  return Math.min(TARGET_SCALE, MAX_EDGE_PX / Math.max(width, height, 1));
}

export function imageSize(region: Region): { width: number; height: number } {
  const layout = layoutOf(region);
  const scale = snapshotScale(layout.width, layout.height);
  return { width: Math.ceil(layout.width * scale), height: Math.ceil(layout.height * scale) };
}

// A stable fingerprint of the ink, to skip checking a page that has not really changed
// (draw a stray mark, undo it).
export function hashStrokes(strokes: PenStroke[]): string {
  let hash = 0x811c9dc5;
  const mix = (value: number) => {
    hash ^= value | 0;
    hash = Math.imul(hash, 0x01000193);
  };
  for (const stroke of strokes) {
    for (const point of stroke.points) {
      mix(Math.round(point.x * 2));
      mix(Math.round(point.y * 2));
    }
    mix(-1);
  }
  return (hash >>> 0).toString(16);
}

// Ink only, on plain white. The paper's ruling is left out because it can read as minus signs.
export function renderSnapshot(region: Region, targetDocument: Document = document): string {
  const layout = layoutOf(region);
  const scale = snapshotScale(layout.width, layout.height);
  const canvas = targetDocument.createElement("canvas");
  canvas.width = Math.ceil(layout.width * scale);
  canvas.height = Math.ceil(layout.height * scale);
  const context = canvas.getContext("2d");
  if (!context) throw new Error("2D canvas unavailable");
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  region.bands.forEach((band, index) => {
    context.setTransform(scale, 0, 0, scale, -layout.originX * scale, (layout.offsets[index] - band.bounds.minY) * scale);
    for (const stroke of band.strokes) {
      context.fillStyle = stroke.color;
      context.fill(strokePath(stroke));
    }
  });
  return canvas.toDataURL("image/png");
}
