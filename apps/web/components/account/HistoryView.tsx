"use client";

import { useEffect, useState } from "react";
import { cachedRecaps, fetchHistory } from "@/lib/account";
import type { SessionHistoryItem } from "@/lib/types";

const when = (seconds: number) => new Date(seconds * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

export function HistoryView({ userId, onOpen, onBack }: { userId: string; onOpen: (id: string) => void; onBack: () => void }) {
  const [items, setItems] = useState<SessionHistoryItem[] | null>(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    fetchHistory().then(
      (history) => setItems(history.sessions),
      () => {
        // What's on this device can still be read.
        setOffline(true);
        setItems(
          cachedRecaps(userId).map((recap) => ({
            sessionId: recap.sessionId,
            startedAt: recap.startedAt,
            finishedAt: recap.finishedAt,
            found: recap.glance.found,
            corrected: recap.glance.corrected,
            topicLabels: recap.topics.flatMap((group) => group.topics.map((topic) => topic.label)),
          })),
        );
      },
    );
  }, [userId]);

  return (
    <div data-testid="history" className="fixed inset-0 z-40 overflow-y-auto bg-paper">
      <main className="mx-auto flex w-full max-w-2xl flex-col gap-4 p-4 sm:p-8">
        <header className="flex items-center justify-between gap-3">
          <h1 className="font-display text-2xl font-bold tracking-tight">Past recaps</h1>
          <button type="button" data-testid="history-back" onClick={onBack} className="min-h-11 rounded-xl px-4 font-medium ring-1 ring-ink/15 hover:bg-ink/5">Back to my notebook</button>
        </header>
        {offline && <p data-testid="history-offline" className="text-[14px] text-muted">I can&apos;t reach the server, so this is what&apos;s saved on this device.</p>}
        {items?.length === 0 && <p data-testid="history-empty" className="text-[15px] text-muted">Nothing here yet. Finish a session and I&apos;ll keep its recap here.</p>}
        <ul className="flex flex-col gap-2">
          {items?.map((item) => (
            <li key={item.sessionId}>
              <button type="button" data-testid="history-item" onClick={() => onOpen(item.sessionId)} className="flex min-h-11 w-full flex-col gap-0.5 rounded-2xl bg-white p-3 text-left ring-1 ring-ink/5 hover:bg-duck/10">
                <span className="flex flex-wrap items-baseline justify-between gap-x-3">
                  <span className="font-medium">{when(item.finishedAt)}</span>
                  <span className="text-[13px] text-muted">{item.corrected} of {item.found} corrected</span>
                </span>
                <span className="text-[13px] text-muted">{item.topicLabels.length ? item.topicLabels.join(", ") : "Nothing to fix that time"}</span>
              </button>
            </li>
          ))}
        </ul>
      </main>
    </div>
  );
}
