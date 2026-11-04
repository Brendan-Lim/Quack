// Group loose strokes into written lines using geometry alone.

import { strokeBounds, unionBounds } from "./geometry";
import type { Bounds, PenStroke } from "./types";

export type TextLine = { bounds: Bounds; strokes: PenStroke[] };

const TALL_STROKE_FACTOR = 4;
const FLAT_STROKES_FACTOR = 4;
const MIN_FULL_SIZE_STROKES = 3;
const FRACTION_GAP_FACTOR = 0.6;
const SMALL_STROKE_FACTOR = 0.7;
const MARK_WIDTH_FACTOR = 1.5;
const MARK_REACH_FACTOR = 0.8;
const CARRIAGE_RETURN_FACTOR = 2;
const RUN_GAP_FACTOR = 5;
const RUN_JUMP_FACTOR = 1.6;
const MIN_VOTES_TO_MOVE = 3;
const MIN_SHARE_TO_MOVE = 0.5;
const MAX_MOVE_FACTOR = 1;

type Placed = { stroke: PenStroke; bounds: Bounds };

function belongs(line: Bounds, stroke: Bounds): boolean {
  const center = (stroke.minY + stroke.maxY) / 2;
  if (center >= line.minY && center <= line.maxY) return true;
  const overlap = Math.min(line.maxY, stroke.maxY) - Math.max(line.minY, stroke.minY);
  const smaller = Math.min(line.maxY - line.minY, stroke.maxY - stroke.minY);
  return smaller > 0 && overlap / smaller >= 0.5;
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)] ?? 0;
}

// How tall this writer's writing is. A line like 1 ÷ 4 = 0 has more flat strokes and dots than
// digits, and going by those made every digit "tall": they then fell out of the line altogether.
// One or two very tall strokes are a margin rule or a big bracket, which say nothing about this.
function typicalHeight(heights: number[]): number {
  const middle = median(heights);
  const upper = [...heights].sort((a, b) => a - b)[Math.floor(heights.length * 0.75)] ?? 0;
  const fullSize = heights.filter((value) => value * FLAT_STROKES_FACTOR >= upper);
  return middle * FLAT_STROKES_FACTOR >= upper || fullSize.length < MIN_FULL_SIZE_STROKES ? middle : median(fullSize);
}

const height = (bounds: Bounds) => bounds.maxY - bounds.minY;
const width = (bounds: Bounds) => bounds.maxX - bounds.minX;
const centerY = (bounds: Bounds) => (bounds.minY + bounds.maxY) / 2;

const isBarShape = (bounds: Bounds, lineHeight: number) => height(bounds) < lineHeight * 0.35 && height(bounds) * 4 < width(bounds);
const centerX = (bounds: Bounds) => (bounds.minX + bounds.maxX) / 2;

// A fraction bar drawn quickly on an iPad rises to the right. On 2026-11-04 the bar of x = 29/5 was 76 wide and
// 23 tall, so by its box it wasn't a bar. A slanted bar is told by its path: one straight stroke, no steeper than
// 30°, long enough to span a digit. A slash is steeper, and a digit isn't straight. A stroke of two points has no
// path to judge, so only its box counts, as before.
const SLANTED_BAR_MAX_SLOPE = Math.tan((30 * Math.PI) / 180);
const SLANTED_BAR_MIN_LENGTH = 0.6;
const SLANTED_BAR_MAX_HEIGHT = 0.6;
const SLANTED_BAR_STRAIGHTNESS = 0.12;

// A bar from its left end to its right.
type Chord = { x0: number; y0: number; x1: number; y1: number };

function slantedBar(stroke: PenStroke, lineHeight: number): Chord | null {
  const { points } = stroke;
  if (points.length < 3 || lineHeight <= 0) return null;
  const first = points[0];
  const last = points[points.length - 1];
  const dx = last.x - first.x;
  const dy = last.y - first.y;
  const length = Math.hypot(dx, dy);
  if (length < lineHeight * SLANTED_BAR_MIN_LENGTH || Math.abs(dy) > Math.abs(dx) * SLANTED_BAR_MAX_SLOPE) return null;
  if (height(strokeBounds(stroke)) >= lineHeight * SLANTED_BAR_MAX_HEIGHT) return null;
  let furthest = 0;
  for (const point of points) furthest = Math.max(furthest, Math.abs(dy * (point.x - first.x) - dx * (point.y - first.y)) / length);
  if (furthest > Math.max(length * SLANTED_BAR_STRAIGHTNESS, 2)) return null;
  return dx >= 0 ? { x0: first.x, y0: first.y, x1: last.x, y1: last.y } : { x0: last.x, y0: last.y, x1: first.x, y1: first.y };
}

// A level bar by its box, or a slanted one by its path.
function barOf(stroke: PenStroke, lineHeight: number): Chord | null {
  const bounds = strokeBounds(stroke);
  if (isBarShape(bounds, lineHeight)) return { x0: bounds.minX, y0: centerY(bounds), x1: bounds.maxX, y1: centerY(bounds) };
  return slantedBar(stroke, lineHeight);
}

// How high the bar is at x, held level past its ends.
function barYAt(bar: Chord, x: number): number {
  if (bar.x1 === bar.x0) return (bar.y0 + bar.y1) / 2;
  const along = Math.min(1, Math.max(0, (x - bar.x0) / (bar.x1 - bar.x0)));
  return bar.y0 + (bar.y1 - bar.y0) * along;
}

// A line's band grows as strokes join it, so an earlier mark can end up inside a later band.
function mergeOverlapping(lines: TextLine[]): TextLine[] {
  const merged = [...lines];
  for (let i = 0; i < merged.length; i++) {
    for (let j = merged.length - 1; j > i; j--) {
      if (belongs(merged[i].bounds, merged[j].bounds) || belongs(merged[j].bounds, merged[i].bounds)) {
        merged[i] = {
          bounds: unionBounds(merged[i].bounds, merged[j].bounds)!,
          strokes: [...merged[i].strokes, ...merged[j].strokes],
        };
        merged.splice(j, 1);
        i = -1;
        break;
      }
    }
  }
  return merged;
}

// A stacked fraction's bar lands on a "line" of its own, with numerator and denominator on
// the lines either side. Rejoin them, or a crop could start on a bar that reads as a minus.
function joinFractions(lines: TextLine[], lineHeight: number): TextLine[] {
  const joined = [...lines];
  for (let i = 1; i < joined.length - 1; i++) {
    const bar = joined[i];
    // One long flat stroke, or a straight one rising gently. A line of ordinary writing is never just that.
    const isBar = bar.strokes.length === 1 && barOf(bar.strokes[0], lineHeight) !== null;
    if (!isBar) continue;
    const above = joined[i - 1];
    const below = joined[i + 1];
    const gap = lineHeight * FRACTION_GAP_FACTOR;
    if (bar.bounds.minY - above.bounds.maxY > gap || below.bounds.minY - bar.bounds.maxY > gap) continue;
    const overlapsAbove = above.bounds.maxX >= bar.bounds.minX && above.bounds.minX <= bar.bounds.maxX;
    const overlapsBelow = below.bounds.maxX >= bar.bounds.minX && below.bounds.minX <= bar.bounds.maxX;
    if (!overlapsAbove || !overlapsBelow) continue;
    const bounds = unionBounds(unionBounds(above.bounds, bar.bounds), below.bounds)!;
    joined.splice(i - 1, 3, { bounds, strokes: [...above.strokes, ...bar.strokes, ...below.strokes] });
    i--;
  }
  return joined;
}

// A tall answer like x²/2 + C written into a line can split it differently: the + and C sit level with
// the bar, so the bar and denominator go with them, and the numerator stays above with ∫ x dx =. That
// was the iPad session of 2026-11-02, and a reading of either half says nothing. A bar whose line has
// only one of its numerator and denominator, with the other just across in the next line, and other
// writing level with the bar, is one line. Writing that's only beside a bar is no reason to join.
function joinSplitFractions(lines: TextLine[], lineHeight: number): TextLine[] {
  const joined = [...lines];
  const gap = lineHeight * FRACTION_GAP_FACTOR;
  for (let i = 0; i < joined.length - 1; i++) {
    const pair = [joined[i], joined[i + 1]];
    const strokes = pair.flatMap((line) => line.strokes.map((stroke) => ({ stroke, bounds: strokeBounds(stroke), line })));
    const splits = strokes.some((bar) => {
      if (!isBarShape(bar.bounds, lineHeight)) return false;
      const over = (item: (typeof strokes)[number]) => item !== bar && centerX(item.bounds) >= bar.bounds.minX && centerX(item.bounds) <= bar.bounds.maxX;
      const numerator = strokes.filter((item) => over(item) && item.bounds.maxY <= bar.bounds.minY && bar.bounds.minY - item.bounds.maxY <= gap);
      const denominator = strokes.filter((item) => over(item) && item.bounds.minY >= bar.bounds.maxY && item.bounds.minY - bar.bounds.maxY <= gap);
      if (!numerator.length || !denominator.length) return false;
      // A whole fraction on the bar's own line, like a stacked d/dx, needs nothing from the next one.
      const own = (item: (typeof strokes)[number]) => item.line === bar.line;
      if (numerator.some(own) && denominator.some(own)) return false;
      const parts = [bar, ...numerator, ...denominator];
      const level = centerY(bar.bounds);
      return strokes.some(
        (item) => !parts.includes(item) && ((item.bounds.minY < level && item.bounds.maxY > level) || (isBarShape(item.bounds, lineHeight) && Math.abs(centerY(item.bounds) - level) <= lineHeight * 0.25)),
      );
    });
    if (!splits) continue;
    joined.splice(i, 2, { bounds: unionBounds(pair[0].bounds, pair[1].bounds)!, strokes: [...pair[0].strokes, ...pair[1].strokes] });
    i--;
  }
  return joined;
}

// `x = 29/5`, the bar beside x = on a line of its own, and the numerator and denominator each a line above
// and below. That was the iPad on 2026-11-04: five lines for three, and the two readings put the same words in
// different lines, so neither could be used. Neither rule above puts it back: the bar isn't alone on its line,
// and the numerator and denominator aren't next to each other. A line either side is joined only if all it holds
// is just over the bar (or just under it) and within its ends. And there has to be writing beside the bar: an
// underline has nothing beside it.
const BAR_END_REACH = 0.4;
const BAR_SIDE_SLACK = 0.15;
const MAX_STRADDLING = 2;

function joinStackedFractions(lines: TextLine[], lineHeight: number): TextLine[] {
  const joined = [...lines];
  const gap = lineHeight * FRACTION_GAP_FACTOR;
  const reach = lineHeight * BAR_END_REACH;
  const slack = lineHeight * BAR_SIDE_SLACK;
  for (let i = 0; i < joined.length; i++) {
    const own = joined[i];
    const above: TextLine | undefined = joined[i - 1];
    const below: TextLine | undefined = joined[i + 1];
    const span = own.strokes.reduce<{ start: number; end: number } | null>((found, stroke) => {
      if (found) return found;
      const bar = barOf(stroke, lineHeight);
      if (!bar) return null;
      const side = (other: PenStroke): "over" | "under" | null => {
        const bounds = strokeBounds(other);
        if (centerX(bounds) < bar.x0 - reach || centerX(bounds) > bar.x1 + reach) return null;
        const level = barYAt(bar, centerX(bounds));
        if (bounds.maxY <= level + slack && level - bounds.maxY <= gap) return "over";
        if (bounds.minY >= level - slack && bounds.minY - level <= gap) return "under";
        return null;
      };
      const rest = own.strokes.filter((other) => other !== stroke);
      // Writing beside the bar, level with it, like x =. Writing on the line below an underline isn't beside it.
      const level = (other: PenStroke) => {
        const bounds = strokeBounds(other);
        const at = barYAt(bar, centerX(bounds));
        return side(other) === null && bounds.minY < at && bounds.maxY > at;
      };
      if (!rest.some(level)) return null;
      // A flat stroke beside the bar that reaches into its line, like the top of the = in x = 29/5, which went with
      // the 29. Only a stroke or two like that: in tight writing, a whole line of glyphs can reach into the next.
      const straddles = (other: PenStroke) => {
        const bounds = strokeBounds(other);
        return side(other) === null && height(bounds) < lineHeight * 0.35 && bounds.maxY > own.bounds.minY && bounds.minY < own.bounds.maxY;
      };
      const only = (line: TextLine | undefined, where: "over" | "under") =>
        Boolean(line?.strokes.some((other) => side(other) === where)) &&
        line!.strokes.every((other) => side(other) === where || straddles(other)) &&
        line!.strokes.filter(straddles).length <= MAX_STRADDLING;
      const numerator = only(above, "over");
      const denominator = only(below, "under");
      if (!numerator && !denominator) return null;
      const hasOver = numerator || rest.some((other) => side(other) === "over");
      const hasUnder = denominator || rest.some((other) => side(other) === "under");
      return hasOver && hasUnder ? { start: numerator ? i - 1 : i, end: denominator ? i + 1 : i } : null;
    }, null);
    if (!span) continue;
    const parts = joined.slice(span.start, span.end + 1);
    joined.splice(span.start, parts.length, {
      bounds: parts.reduce<Bounds | null>((all, line) => unionBounds(all, line.bounds), null)!,
      strokes: parts.flatMap((line) => line.strokes),
    });
    i = span.start - 1;
  }
  return joined;
}

function rebuilt(lines: TextLine[]): TextLine[] {
  return lines
    .filter((line) => line.strokes.length)
    .map((line) => ({ strokes: line.strokes, bounds: line.strokes.reduce<Bounds | null>((all, stroke) => unionBounds(all, strokeBounds(stroke)), null)! }));
}

// Strokes the pen wrote in one go, left to right. Coming back to the left, or a jump up or down,
// starts another. Strokes arrive in the order they were written.
function runsOf(placed: Placed[], typical: number): Placed[][] {
  const runs: Placed[][] = [];
  let previous: Placed | null = null;
  for (const item of placed) {
    const carriageReturn = previous !== null && item.bounds.minX < previous.bounds.minX - typical * CARRIAGE_RETURN_FACTOR;
    const farAcross = previous !== null && item.bounds.minX - previous.bounds.maxX > typical * RUN_GAP_FACTOR;
    const farDown = previous !== null && Math.abs(centerY(item.bounds) - centerY(previous.bounds)) > typical * RUN_JUMP_FACTOR;
    if (previous === null || carriageReturn || farAcross || farDown) runs.push([item]);
    else runs.at(-1)!.push(item);
    previous = item;
  }
  return runs;
}

// Writing doesn't stay inside tidy bands. A raised x² overlaps the line above as much as its own,
// and the d of a stacked d/dx can sit wholly inside the line above. The order of writing settles
// it: a stroke written among one line's strokes belongs with them, if it's close enough to.
function followTheOrderOfWriting(lines: TextLine[], placed: Placed[], typical: number): TextLine[] {
  const lineOf = new Map<PenStroke, TextLine>();
  for (const line of lines) for (const stroke of line.strokes) lineOf.set(stroke, line);
  let moved = false;
  for (const run of runsOf(placed, typical)) {
    const votes = new Map<TextLine, number>();
    for (const { stroke, bounds } of run) {
      const line = lineOf.get(stroke);
      // Only full-size writing votes. A mark or a superscript is what's being placed.
      if (line && height(bounds) >= typical * SMALL_STROKE_FACTOR) votes.set(line, (votes.get(line) ?? 0) + 1);
    }
    const [home, count] = [...votes.entries()].sort((a, b) => b[1] - a[1])[0] ?? [null, 0];
    if (!home || count < MIN_VOTES_TO_MOVE || count / run.length < MIN_SHARE_TO_MOVE) continue;
    // Its bounds are worked out from the strokes that voted for it, not ones about to leave.
    const anchored = run.filter(({ stroke }) => lineOf.get(stroke) === home).reduce<Bounds | null>((all, item) => unionBounds(all, item.bounds), null)!;
    for (const { stroke, bounds } of run) {
      const line = lineOf.get(stroke);
      if (!line || line === home) continue;
      const away = bounds.maxY < anchored.minY ? anchored.minY - bounds.maxY : bounds.minY > anchored.maxY ? bounds.minY - anchored.maxY : 0;
      if (away > typical * MAX_MOVE_FACTOR) continue;
      line.strokes = line.strokes.filter((other) => other !== stroke);
      home.strokes.push(stroke);
      lineOf.set(stroke, home);
      moved = true;
    }
  }
  return moved ? rebuilt(lines) : lines;
}

// A small mark near a line's writing belongs to it, even with no overlap: a raised 2, a dot.
function attachMarks(lines: TextLine[], typical: number): TextLine[] {
  const marks = lines.filter((line) => line.strokes.length === 1 && isMark(line.bounds, typical));
  const writing = lines.filter((line) => !marks.includes(line));
  let moved = false;
  for (const mark of marks) {
    const beside = writing.flatMap((line) =>
      line.strokes.flatMap((stroke) => {
        const bounds = strokeBounds(stroke);
        const across = mark.bounds.minX > bounds.maxX ? mark.bounds.minX - bounds.maxX : bounds.minX > mark.bounds.maxX ? bounds.minX - mark.bounds.maxX : 0;
        const up = mark.bounds.maxY < bounds.minY ? bounds.minY - mark.bounds.maxY : mark.bounds.minY > bounds.maxY ? mark.bounds.minY - bounds.maxY : 0;
        return across <= typical * MARK_REACH_FACTOR && up <= typical * MARK_REACH_FACTOR ? [{ line, distance: across + up * 2 }] : [];
      }),
    );
    const nearest = beside.sort((a, b) => a.distance - b.distance)[0];
    if (!nearest) continue;
    nearest.line.strokes.push(...mark.strokes);
    mark.strokes = [];
    moved = true;
  }
  return moved ? rebuilt(lines) : lines;
}

function isMark(bounds: Bounds, typical: number): boolean {
  return height(bounds) < typical * SMALL_STROKE_FACTOR && width(bounds) < typical * MARK_WIDTH_FACTOR;
}

export function groupIntoLines(strokes: PenStroke[]): TextLine[] {
  const written: Placed[] = strokes.map((stroke) => ({ stroke, bounds: strokeBounds(stroke) }));
  const placed = [...written].sort((a, b) => a.bounds.minY + a.bounds.maxY - (b.bounds.minY + b.bounds.maxY));

  // A margin rule or a tall bracket would merge everything it passes into one huge "line".
  const typical = typicalHeight(placed.map((item) => height(item.bounds)));
  const isTall = (item: Placed) => typical > 0 && height(item.bounds) > typical * TALL_STROKE_FACTOR;

  const lines: TextLine[] = [];
  for (const { stroke, bounds } of placed.filter((item) => !isTall(item))) {
    const line = lines.find((candidate) => belongs(candidate.bounds, bounds));
    if (line) {
      line.strokes.push(stroke);
      line.bounds = unionBounds(line.bounds, bounds)!;
    } else {
      lines.push({ bounds, strokes: [stroke] });
    }
  }
  const body = written.filter((item) => !isTall(item));
  const arranged = mergeOverlapping(followTheOrderOfWriting(attachMarks(lines, typical), body, typical));
  const merged = arranged.sort((a, b) => a.bounds.minY - b.bounds.minY);
  // A fraction's bar, and its denominator, can each be a line of one stroke, and on a short page they'd
  // pull this down until the fraction couldn't be put back together.
  const full = merged.filter((line) => line.strokes.length > 1);
  const lineHeight = median((full.length ? full : merged).map((line) => height(line.bounds)));
  const result = joinStackedFractions(joinSplitFractions(joinFractions(merged, lineHeight), lineHeight), lineHeight);

  for (const { stroke, bounds } of placed.filter(isTall)) {
    const center = (bounds.minY + bounds.maxY) / 2;
    const home = result.find((line) => center >= line.bounds.minY && center <= line.bounds.maxY);
    // It stays attached to a line only if it's within one. It never widens the line's band.
    if (home) home.strokes.push(stroke);
  }
  return result;
}
