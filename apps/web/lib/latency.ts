// When each stage of a check happened, to find where the time goes. In memory only, never sent
// anywhere. Live tests and the debug panel read it from `window.__quackLatency`.

export type Stage =
  | "stroke_end"
  | "debounce_start"
  | "debounce_end"
  | "check_skipped"
  | "check_overlapping"
  | "region_selected"
  | "image_rendered"
  | "request_sent"
  | "response_received"
  | "result_applied"
  | "result_dropped"
  | "line_look_again"
  | "alert_shown"
  | "sound"
  | "hint_requested"
  | "hint_shown"
  | "mistake_touched"
  | "mistake_selected"
  | "alert_cleared";

export type Mark = { stage: Stage; at: number; id?: string; detail?: Record<string, unknown> };

const MAX_MARKS = 1000;
const marks: Mark[] = [];
let sequence = 0;

export function mark(stage: Stage, id?: string, detail?: Record<string, unknown>) {
  marks.push({ stage, at: performance.now(), ...(id ? { id } : {}), ...(detail ? { detail } : {}) });
  if (marks.length > MAX_MARKS) marks.splice(0, marks.length - MAX_MARKS);
  if (typeof window !== "undefined") (window as unknown as { __quackLatency: Mark[] }).__quackLatency = marks;
}

export function latencyMarks(): readonly Mark[] {
  return marks;
}

export function newRequestId(): string {
  sequence += 1;
  return `r${Date.now().toString(36)}-${sequence}`;
}
