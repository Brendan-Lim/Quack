import type { Bounds, PenStroke, Point } from "./types";

export function strokeBounds(stroke: PenStroke): Bounds {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const { x, y } of stroke.points) {
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  const pad = stroke.width / 2;
  return { minX: minX - pad, minY: minY - pad, maxX: maxX + pad, maxY: maxY + pad };
}

export function unionBounds(a: Bounds | null, b: Bounds | null): Bounds | null {
  if (!a) return b;
  if (!b) return a;
  return {
    minX: Math.min(a.minX, b.minX),
    minY: Math.min(a.minY, b.minY),
    maxX: Math.max(a.maxX, b.maxX),
    maxY: Math.max(a.maxY, b.maxY),
  };
}

export function boundsOfStrokes(strokes: PenStroke[]): Bounds | null {
  return strokes.reduce<Bounds | null>((all, stroke) => unionBounds(all, strokeBounds(stroke)), null);
}

export function intersects(a: Bounds, b: Bounds): boolean {
  return a.minX <= b.maxX && b.minX <= a.maxX && a.minY <= b.maxY && b.minY <= a.maxY;
}

export function expand(bounds: Bounds, by: number): Bounds {
  return { minX: bounds.minX - by, minY: bounds.minY - by, maxX: bounds.maxX + by, maxY: bounds.maxY + by };
}

function distanceToSegment(p: { x: number; y: number }, a: Point, b: Point): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lengthSquared = dx * dx + dy * dy;
  const t =
    lengthSquared === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lengthSquared));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

export function strokeHit(stroke: PenStroke, p: { x: number; y: number }, radius: number): boolean {
  const reach = radius + stroke.width / 2;
  if (stroke.points.length === 1) return Math.hypot(p.x - stroke.points[0].x, p.y - stroke.points[0].y) <= reach;
  for (let i = 1; i < stroke.points.length; i++) {
    if (distanceToSegment(p, stroke.points[i - 1], stroke.points[i]) <= reach) return true;
  }
  return false;
}

export function inkLength(strokes: PenStroke[]): number {
  let total = 0;
  for (const { points } of strokes) {
    for (let i = 1; i < points.length; i++) {
      total += Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
    }
  }
  return total;
}
