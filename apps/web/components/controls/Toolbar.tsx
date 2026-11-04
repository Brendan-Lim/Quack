"use client";

import { Check, Eraser, Eye, EyeOff, Grid3x3, ImageUp, Lasso, Pen, Redo2, Rows3, Square, Trash2, Undo2, Volume2, VolumeX } from "lucide-react";
import { useRef, type ReactNode } from "react";
import type { TaskContext } from "@/lib/types";
import type { Paper, Tool } from "../notebook/NotebookCanvas";
import { Duck } from "../duck/Duck";
import { toolClass } from "@/lib/toolClass";

export const PEN_COLORS = [
  { value: "#262626", name: "Black" },
  { value: "#2F5FB3", name: "Blue" },
  { value: "#B5482B", name: "Red" },
];
export const PEN_WIDTHS = [
  { value: 2, name: "Fine" },
  { value: 3, name: "Medium" },
  { value: 5, name: "Thick" },
];
const TASKS: { value: TaskContext; short: string; label: string }[] = [
  { value: "auto", short: "Auto", label: "Quack works out what each line is doing" },
  { value: "simplify", short: "Simplify", label: "You're simplifying" },
  { value: "solve", short: "Solve", label: "You're solving" },
];
const PAPERS: { value: Paper; name: string; icon: ReactNode }[] = [
  { value: "grid", name: "Grid paper", icon: <Grid3x3 size={18} aria-hidden /> },
  { value: "ruled", name: "Ruled paper", icon: <Rows3 size={18} aria-hidden /> },
  { value: "plain", name: "Plain paper", icon: <Square size={18} aria-hidden /> },
];

type Props = {
  tool: Tool;
  color: string;
  width: number;
  paper: Paper;
  canUndo: boolean;
  canRedo: boolean;
  monitoring: boolean;
  muted: boolean;
  taskContext: TaskContext;
  onTaskContext: (next: TaskContext) => void;
  onTool: (tool: Tool) => void;
  onColor: (color: string) => void;
  onWidth: (width: number) => void;
  onPaper: (paper: Paper) => void;
  onUndo: () => void;
  onRedo: () => void;
  onClear: () => void;
  onMonitoring: () => void;
  onMuted: () => void;
  onCheck: () => void;
  onImport: (file: File) => void;
  accountActions?: ReactNode;
};

function IconButton({ label, active, disabled, onClick, children, testId }: { label: string; active?: boolean; disabled?: boolean; onClick: () => void; children: ReactNode; testId?: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={active}
      disabled={disabled}
      data-testid={testId}
      onClick={onClick}
      // The same selected look as the lesson's tools: `toolClass`, keyed on a class rather than aria-pressed.
      className={toolClass(active, "tool-compact")}
    >
      {children}
    </button>
  );
}

const Divider = () => <span aria-hidden className="mx-0.5 h-5 w-px shrink-0 bg-ink/10" />;

export function Toolbar(props: Props) {
  const { tool, color, width, paper, canUndo, canRedo, monitoring, muted, onTool, onColor, onWidth, onPaper, onUndo, onRedo, onClear, onMonitoring, onMuted, onCheck, onImport, accountActions, taskContext, onTaskContext } = props;
  const file = useRef<HTMLInputElement>(null);
  const nextPaper = PAPERS[(PAPERS.findIndex((item) => item.value === paper) + 1) % PAPERS.length];
  const currentPaper = PAPERS.find((item) => item.value === paper)!;
  const currentTask = TASKS.find((item) => item.value === taskContext)!;
  const nextTask = TASKS[(TASKS.indexOf(currentTask) + 1) % TASKS.length];

  return (
    // On a phone the row can wrap, so My Notebook | Learn gets a line of its own and the pen stays in reach.
    <header className="z-10 flex min-h-14 shrink-0 flex-wrap items-center gap-1 border-b border-ink/5 bg-paper pr-2 pl-2 sm:h-14 sm:flex-nowrap sm:pr-3 sm:pl-3">
      {/* Writing tools scroll sideways on narrow screens. Watching, sound and Check stay put. */}
      <div data-testid="tools" className="toolbar-tools flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
      <div className="mr-1 flex shrink-0 items-center gap-1 pr-1">
        <Duck state="idle" size={30} />
        <span className="hidden font-display text-lg font-bold tracking-tight lg:inline">Quack</span>
      </div>
      <Divider />
      <IconButton label="Pen" active={tool === "pen"} onClick={() => onTool("pen")} testId="tool-pen"><Pen size={18} aria-hidden /></IconButton>
      <IconButton label="Eraser" active={tool === "eraser"} onClick={() => onTool("eraser")} testId="tool-eraser"><Eraser size={18} aria-hidden /></IconButton>
      <IconButton label="Circle some working to ask Quack about it" active={tool === "lasso"} onClick={() => onTool(tool === "lasso" ? "pen" : "lasso")} testId="tool-lasso"><Lasso size={18} aria-hidden /></IconButton>
      <Divider />
      {PEN_COLORS.map((item) => (
        <button key={item.value} type="button" aria-label={`${item.name} pen`} title={`${item.name} pen`} aria-pressed={color === item.value} onClick={() => onColor(item.value)} className="flex size-9 shrink-0 items-center justify-center rounded-lg hover:bg-ink/5 pointer-coarse:size-11">
          <span className={`block size-[18px] rounded-full ${color === item.value ? "ring-2 ring-offset-2 ring-offset-paper" : ""}`} style={{ backgroundColor: item.value, ["--tw-ring-color" as string]: item.value }} />
        </button>
      ))}
      <Divider />
      {PEN_WIDTHS.map((item) => (
        <button key={item.value} type="button" aria-label={`${item.name} pen`} title={`${item.name} pen`} aria-pressed={width === item.value} onClick={() => onWidth(item.value)} className={toolClass(width === item.value, "tool-compact")}>
          <span className="block rounded-full bg-ink" style={{ width: item.value * 2.4, height: item.value * 2.4 }} />
        </button>
      ))}
      <Divider />
      <IconButton label="Undo" disabled={!canUndo} onClick={onUndo} testId="undo"><Undo2 size={18} aria-hidden /></IconButton>
      <IconButton label="Redo" disabled={!canRedo} onClick={onRedo} testId="redo"><Redo2 size={18} aria-hidden /></IconButton>
      <IconButton label={`${currentPaper.name}. Switch to ${nextPaper.name.toLowerCase()}`} onClick={() => onPaper(nextPaper.value)} testId="paper">{currentPaper.icon}</IconButton>
      <IconButton label="Clear page" onClick={onClear} testId="clear"><Trash2 size={18} aria-hidden /></IconButton>
      <Divider />
      <button
        type="button"
        data-testid="task-context"
        data-context={taskContext}
        aria-label={`${currentTask.label}. Switch to: ${nextTask.label.toLowerCase()}`}
        title={currentTask.label}
        onClick={() => onTaskContext(nextTask.value)}
        className={`flex h-9 shrink-0 items-center rounded-lg px-2 text-[13px] font-medium whitespace-nowrap pointer-coarse:h-11 ${taskContext === "auto" ? "text-ink/70 hover:bg-ink/5" : "bg-duck/60 text-ink"}`}
      >
        {currentTask.short}
      </button>

      </div>

      <Divider />
      <IconButton label={monitoring ? "Quack is watching. Turn off." : "Quack is not watching. Turn on."} active={monitoring} onClick={onMonitoring} testId="monitoring">
        {monitoring ? <Eye size={18} aria-hidden /> : <EyeOff size={18} aria-hidden />}
      </IconButton>
      <IconButton label={muted ? "Sound off. Turn on." : "Sound on. Turn off."} onClick={onMuted} testId="sound">
        {muted ? <VolumeX size={18} aria-hidden /> : <Volume2 size={18} aria-hidden />}
      </IconButton>
      <IconButton label="Import an image" onClick={() => file.current?.click()} testId="import"><ImageUp size={18} aria-hidden /></IconButton>
      <input
        ref={file}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        className="hidden"
        data-testid="import-input"
        onChange={(event) => {
          const chosen = event.target.files?.[0];
          if (chosen) onImport(chosen);
          event.target.value = "";
        }}
      />
      <button type="button" data-testid="check" onClick={onCheck} title="Check my work" aria-label="Check my work" className="ml-1 flex h-9 shrink-0 items-center gap-1.5 rounded-lg bg-ink px-3 text-[13px] font-semibold text-paper transition-opacity hover:opacity-85 pointer-coarse:h-11">
        <Check size={16} aria-hidden />
        <span className="hidden whitespace-nowrap lg:inline">Check my work</span>
      </button>
      {accountActions}
    </header>
  );
}
