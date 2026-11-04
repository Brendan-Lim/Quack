"use client";

import { BookOpen, NotebookPen } from "lucide-react";

export type Mode = "notebook" | "learn";

type Props = {
  mode: Mode;
  onNotebook: () => void;
  onLearn: () => void;
  // In a crowded header the labels shorten, and below 1024px the icons go. It's never only icons.
  compact?: boolean;
};

// Quack's two things to do, in the same place on every screen: My Notebook, for your own maths, and Learn, for
// lessons. The one you're in is yellow, like a chosen tool. Switching never ends anything: the notebook and the
// lesson under way both stay as they were, to come back to.
export function ModeSwitch({ mode, onNotebook, onLearn, compact = false }: Props) {
  const segment = (active: boolean) =>
    `flex min-h-10 items-center gap-1.5 rounded-xl px-3 whitespace-nowrap transition-colors pointer-coarse:min-h-11 ${
      active ? "bg-duck text-ink shadow-[inset_0_-2px_0_var(--color-duck-edge)]" : "text-ink-soft hover:bg-ink/5 hover:text-ink"
    }`;
  return (
    <nav aria-label="Notebook or lessons" data-testid="mode-switch" data-mode={mode} className="flex shrink-0 rounded-2xl bg-paper-deep p-1 text-[14px] font-semibold ring-1 ring-line">
      <button
        type="button"
        data-testid={mode === "notebook" ? "mode-notebook" : "to-notebook"}
        aria-current={mode === "notebook" ? "page" : undefined}
        onClick={mode === "notebook" ? undefined : onNotebook}
        className={segment(mode === "notebook")}
      >
        <NotebookPen size={17} aria-hidden className={compact ? "hidden lg:block" : undefined} />
        <span>
          <span className={compact ? "hidden xl:inline" : "hidden min-[420px]:inline"}>My </span>Notebook
        </span>
      </button>
      <button
        type="button"
        data-testid={mode === "learn" ? "mode-learn" : "open-learn"}
        aria-current={mode === "learn" ? "page" : undefined}
        onClick={mode === "learn" ? undefined : onLearn}
        className={segment(mode === "learn")}
      >
        <BookOpen size={17} aria-hidden className={compact ? "hidden lg:block" : undefined} />
        Learn
      </button>
    </nav>
  );
}
