"use client";

import { useState } from "react";
import { bringInNotes } from "@/lib/persist";
import { Duck } from "../duck/Duck";

// Notes written before accounts existed. They're only moved when the student says, and only stay put otherwise.
export function BringInNotes({ userId, onDone }: { userId: string; onDone: (declined: boolean) => void }) {
  const [problem, setProblem] = useState(false);

  return (
    <main className="flex min-h-dvh flex-col items-center justify-center bg-paper p-6">
      <div className="flex w-full max-w-md flex-col gap-4 text-center">
        <div className="flex justify-center"><Duck state="idle" size={72} /></div>
        <h1 className="font-display text-2xl font-bold">You have notes on this device</h1>
        <p className="text-[15px] leading-snug text-muted">They were written before you had an account. You can keep working on them in this account, or leave them where they are for now. Either way, nothing gets deleted.</p>
        {problem && <p role="alert" className="text-sm text-alert">This account already has notes on this device, so nothing was moved.</p>}
        <div className="flex flex-col gap-2 text-[15px] sm:flex-row sm:justify-center">
          <button
            type="button"
            data-testid="bring-in-notes"
            className="min-h-11 rounded-xl bg-ink px-4 py-2.5 font-semibold text-paper hover:opacity-85"
            onClick={() => void bringInNotes(userId).then((moved) => (moved ? onDone(false) : setProblem(true)))}
          >
            Bring them in
          </button>
          <button type="button" data-testid="leave-notes" className="min-h-11 rounded-xl px-4 py-2.5 font-medium ring-1 ring-ink/15 hover:bg-ink/5" onClick={() => onDone(true)}>
            Not now
          </button>
        </div>
      </div>
    </main>
  );
}
