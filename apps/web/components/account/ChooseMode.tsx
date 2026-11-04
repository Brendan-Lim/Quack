"use client";

import { ArrowRight, BookOpen, NotebookPen } from "lucide-react";
import type { StartingMode } from "@/lib/firstVisit";
import { Duck } from "../duck/Duck";

// Once, after signing up and calibrating: Quack does two things, and here they are side by side. Either can be
// switched to later from the top of every screen, so this isn't a commitment and needs no explaining.
export function ChooseMode({ onChoose }: { onChoose: (mode: StartingMode) => void }) {
  return (
    <main data-testid="choose-mode" className="notebook-scroll flex h-dvh flex-col overflow-y-auto bg-paper">
      <div className="question-in mx-auto flex w-full max-w-4xl flex-1 flex-col justify-center gap-6 px-4 py-8 sm:px-6">
        <div className="flex items-center gap-4">
          <span className="flex size-[76px] shrink-0 items-center justify-center rounded-full bg-duck-soft"><Duck state="hint" size={62} /></span>
          <div className="min-w-0">
            <h1 className="font-display text-[28px] leading-tight font-bold tracking-tight sm:text-[34px]">What would you like to do?</h1>
            <p className="mt-1 text-[15.5px] text-ink-soft">You can switch between them any time, from the top of the screen.</p>
          </div>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <button type="button" data-testid="choose-notebook" onClick={() => onChoose("notebook")} className="card pressable group flex flex-col gap-3 p-5 text-left sm:p-6">
            <span className="flex size-14 items-center justify-center rounded-2xl bg-ink text-paper"><NotebookPen size={26} aria-hidden /></span>
            <span className="font-display text-[24px] leading-tight font-bold">My Notebook</span>
            <span className="text-[16px] leading-snug text-ink-soft">Have a maths question of your own? Write it here, and Quack will help you check your working.</span>
            <span className="mt-auto flex items-center gap-1.5 pt-2 text-[15px] font-semibold">
              Open my notebook
              <ArrowRight size={18} aria-hidden className="transition-transform group-hover:translate-x-0.5" />
            </span>
          </button>

          <button type="button" data-testid="choose-learn" onClick={() => onChoose("learn")} className="pressable group flex flex-col gap-3 rounded-[var(--radius-card)] bg-duck p-5 text-left shadow-[0_4px_0_var(--color-duck-edge)] sm:p-6">
            <span className="flex size-14 items-center justify-center rounded-2xl bg-surface/80 text-ink"><BookOpen size={26} aria-hidden /></span>
            <span className="font-display text-[24px] leading-tight font-bold">Learn with Quack</span>
            <span className="text-[16px] leading-snug text-ink">Pick a topic, follow short lessons and practise with your handwriting as you level up.</span>
            <span className="mt-auto flex items-center gap-1.5 pt-2 text-[15px] font-semibold">
              Start learning
              <ArrowRight size={18} aria-hidden className="transition-transform group-hover:translate-x-0.5" />
            </span>
          </button>
        </div>
      </div>
    </main>
  );
}
