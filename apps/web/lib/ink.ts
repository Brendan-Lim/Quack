import { getStroke } from "perfect-freehand";
import type { PenStroke, Point } from "./types";

const cache = new WeakMap<PenStroke, Path2D>();

function outline(points: Point[], width: number, complete: boolean): number[][] {
  const hasPressure = points.some((point) => point.pressure !== undefined);
  return getStroke(
    points.map((point) => [point.x, point.y, point.pressure ?? 0.5]),
    {
      size: width,
      thinning: hasPressure ? 0.55 : 0.35,
      smoothing: 0.55,
      streamline: 0.4,
      simulatePressure: !hasPressure,
      last: complete,
    },
  );
}

function toPathData(shape: number[][]): string {
  if (shape.length < 2) return "";
  const average = (a: number, b: number) => (a + b) / 2;
  let data = `M${shape[0][0].toFixed(2)} ${shape[0][1].toFixed(2)} Q`;
  for (let i = 0; i < shape.length; i++) {
    const [x0, y0] = shape[i];
    const [x1, y1] = shape[(i + 1) % shape.length];
    data += `${x0.toFixed(2)} ${y0.toFixed(2)} ${average(x0, x1).toFixed(2)} ${average(y0, y1).toFixed(2)} `;
  }
  return data + "Z";
}

export function strokePath(stroke: PenStroke): Path2D {
  let path = cache.get(stroke);
  if (!path) {
    path = new Path2D(toPathData(outline(stroke.points, stroke.width, true)));
    cache.set(stroke, path);
  }
  return path;
}

export function livePath(points: Point[], width: number): Path2D {
  return new Path2D(toPathData(outline(points, width, false)));
}
