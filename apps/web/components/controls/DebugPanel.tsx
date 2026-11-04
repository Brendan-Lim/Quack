"use client";

import { useEffect, useState } from "react";
import type { AnalysisTrace } from "@/hooks/useQuack";
import type { LineReadingTrace } from "@/lib/types";

type Props = { duckState: string; version: number; strokes: number; trace: AnalysisTrace | null; onExport: () => void };

function describeReading(name: string, reading: LineReadingTrace | null): string {
  if (!reading) return `${name}: none`;
  const doubt = [reading.legibility === "clear" ? "" : reading.legibility, ...reading.alternatives.map((text) => `or ${text}`)].filter(Boolean).join(", ");
  return `${name}: ${reading.math || `(${reading.kind})`}${doubt ? ` [${doubt}]` : ""}`;
}

// Development inspection, opened with ?debug or the ` key.
export function DebugPanel({ duckState, version, strokes, trace, onExport }: Props) {
  const [spend, setSpend] = useState<string>("n/a");

  useEffect(() => {
    if (process.env.NODE_ENV === "production") return;
    let cancelled = false;
    void fetch("/api/dev/usage")
      .then((response) => (response.ok ? response.json() : null))
      .then((usage) => {
        if (!cancelled && usage) setSpend(`$${usage.project_spend_usd.toFixed(4)} of $${usage.project_ceiling_usd}`);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [trace]);

  if (process.env.NODE_ENV === "production") return null;

  const result = trace?.result;
  return (
    <aside data-testid="debug" className="fixed top-16 left-3 z-30 max-h-[60vh] w-72 overflow-auto rounded-xl bg-ink/90 p-3 font-mono text-[11px] leading-relaxed text-paper">
      <div>duck: {duckState}</div>
      <div>version: {version} · strokes: {strokes}</div>
      <div>estimated spend: {spend}</div>
      {/* Saved on this device only. Circle something first to keep just that. */}
      <button type="button" data-testid="export-fixture" onClick={onExport} className="mt-1 min-h-8 rounded-md bg-paper/15 px-2 hover:bg-paper/25">
        Export QA fixture
      </button>
      {result && trace ? (
        <>
          <hr className="my-2 border-paper/20" />
          <div>status: {result.status}{result.stale ? " (stale)" : ""}</div>
          <div>trigger: {trace.trigger} · v{trace.version}</div>
          {result.reason && <div>reason: {result.reason}</div>}
          {result.modelIssue && <div>issue: {result.modelIssue.category}</div>}
          <div>quack: {String(result.intervention.shouldQuack)}</div>
          {result.mistakes.map((mistake) => (
            <div key={mistake.id}>error: {mistake.errorType} ({mistake.parsedOperation}){mistake.isNew ? " new" : ""}</div>
          ))}
          <div>lines known: {result.settledLineIds.length}{result.unreadLineIds.length ? ` · unread: ${result.unreadLineIds.length}` : ""}</div>
          <hr className="my-2 border-paper/20" />
          <div>snapshot {trace.snapshotMs.toFixed(0)}ms · round trip {trace.roundTripMs.toFixed(0)}ms</div>
          <div>read {result.timings.interpretMs.toFixed(0)}ms · verify {result.timings.verifyMs.toFixed(1)}ms</div>
          <div>{result.timings.modelRequests} model calls · ${result.timings.costUsd.toFixed(5)}{result.timings.cacheHit ? " · cached" : ""}</div>
          {result.timings.calls.map((call, index) => (
            <div key={index} className="opacity-80">
              {call.purpose} {call.effort}: {call.cached ? "shared" : `${call.ms.toFixed(0)}ms, ${call.reasoningTokens}/${call.outputTokens} tokens${call.attempts > 1 ? `, ${call.attempts} attempts` : ""}`}
            </div>
          ))}
          <div className="opacity-80">
            {Object.entries(result.timings.stages).map(([stage, at]) => `${stage} ${at.toFixed(0)}`).join(" · ")}
          </div>
          <hr className="my-2 border-paper/20" />
          {trace.dropped && <div data-testid="debug-dropped">reply dropped: stale_or_invalidated (ink changed)</div>}
          {trace.linesOutOfScope > 0 && <div>not_scanned: {trace.linesOutOfScope} other lines on the page</div>}
          {result.trace?.length ? (
            result.trace.map((line) => (
              <div key={line.position} data-testid="debug-line" className="mb-1">
                <div>{line.position + 1}. <span data-testid="debug-disposition">{line.disposition}</span> <span className="opacity-60">{line.lineId ?? "no line id"} v{line.snapshotVersion}</span></div>
                <div className="pl-3 opacity-80">{line.used || "(no maths)"}{line.remembered ? " · remembered" : ""}{line.settled ? " · settled" : ""}</div>
                {(line.first || line.second) && (
                  <div className="pl-3 opacity-60">{describeReading("1st", line.first)} {describeReading("2nd", line.second)}</div>
                )}
                {(line.verdict || line.reason) && <div className="pl-3 opacity-60">{line.verdict}{line.reason ? ` (${line.reason})` : ""}{line.parsedOperation ? ` ${line.parsedOperation}` : ""}</div>}
              </div>
            ))
          ) : (
            result.lines.map((line, index) => (
              <div key={index}>{index + 1}. {line.text} <span className="opacity-60">[{line.kind}, {line.legibility}]</span></div>
            ))
          )}
        </>
      ) : (
        <div className="mt-2 opacity-60">no analysis yet</div>
      )}
    </aside>
  );
}
