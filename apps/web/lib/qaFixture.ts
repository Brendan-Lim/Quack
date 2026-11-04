// A copy of some working and what Quack made of it, to replay a problem found on a real iPad. The
// second iPad session of 2026-11-02 had only screenshots, so line grouping had to be measured from them.
//
// Development only. It's made when someone presses Export in the debug panel, and saved as a file on
// this device. It's never sent anywhere. It has no session id or sign-in, as those are what keep a
// student's notes private, and only the circled strokes if something is circled.

import type { AnalysisTrace } from "@/hooks/useQuack";
import { latencyMarks, type Mark } from "./latency";
import { groupIntoLines } from "./lines";
import { lineId } from "./snapshot";
import type { AskResponse, Bounds, PenStroke } from "./types";

export type QaFixture = {
  kind: "quack-qa-fixture";
  version: 1;
  exportedAt: string;
  notebookVersion: number;
  scope: "circled" | "page";
  strokes: PenStroke[];
  // As `groupIntoLines` puts them now, with the ids checks use, to compare with `checks`.
  lines: { id: string; strokeIds: string[]; bounds: Bounds }[];
  // The last few replies, oldest first. With QUACK_ENV=development each has both readings of each line.
  checks: AnalysisTrace[];
  circledReply: AskResponse | null;
  marks: Mark[];
};

const MAX_CHECKS = 20;
const checks: AnalysisTrace[] = [];

export function keepCheck(trace: AnalysisTrace): void {
  if (process.env.NODE_ENV === "production") return;
  checks.push(trace);
  if (checks.length > MAX_CHECKS) checks.splice(0, checks.length - MAX_CHECKS);
}

export function buildFixture(
  strokes: readonly PenStroke[],
  circled: readonly string[] | null,
  notebookVersion: number,
  circledReply: AskResponse | null = null,
  now: Date = new Date(),
): QaFixture {
  const chosen = circled?.length ? strokes.filter((stroke) => circled.includes(stroke.id)) : [...strokes];
  const lines = groupIntoLines([...chosen]).map((line) => ({ id: lineId(line.strokes), strokeIds: line.strokes.map((stroke) => stroke.id), bounds: line.bounds }));
  return {
    kind: "quack-qa-fixture",
    version: 1,
    exportedAt: now.toISOString(),
    notebookVersion,
    scope: circled?.length ? "circled" : "page",
    strokes: chosen,
    lines,
    checks: [...checks],
    circledReply,
    marks: [...latencyMarks()],
  };
}

export function downloadFixture(fixture: QaFixture): void {
  if (process.env.NODE_ENV === "production") return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(fixture, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `quack-qa-${fixture.exportedAt.replace(/[:.]/g, "-")}.json`;
  link.click();
  URL.revokeObjectURL(url);
}
