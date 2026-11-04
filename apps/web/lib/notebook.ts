// Notebook state as pure functions. Every edit raises `version`, and analysis results are
// matched against it, so a slow reply can never apply to writing that has since changed.

import { boundsOfStrokes, intersects, strokeBounds, unionBounds } from "./geometry";
import type { Bounds, PenStroke } from "./types";

// Strokes are stored in these units whatever the screen, so resizing or moving between
// devices never moves the ink.
export const PAGE_WIDTH = 1000;

type Operation = { type: "add"; strokes: PenStroke[] } | { type: "remove"; strokes: PenStroke[] };

export type NotebookState = {
  strokes: PenStroke[];
  version: number;
  undoStack: Operation[];
  redoStack: Operation[];
  // Where the page has changed since the last analysis began. Separate areas stay separate,
  // because their union could cover private writing that didn't change.
  dirty: Bounds[];
};

export const emptyNotebook: NotebookState = {
  strokes: [],
  version: 0,
  undoStack: [],
  redoStack: [],
  dirty: [],
};

function addDirty(dirty: Bounds[], area: Bounds | null): Bounds[] {
  if (!area) return dirty;
  let merged = area;
  const rest: Bounds[] = [];
  for (const existing of dirty) {
    if (intersects({ ...merged, minX: -Infinity, maxX: Infinity }, existing)) merged = unionBounds(merged, existing)!;
    else rest.push(existing);
  }
  return [...rest, merged];
}

function apply(state: NotebookState, op: Operation): Pick<NotebookState, "strokes" | "version" | "dirty"> {
  const changed = boundsOfStrokes(op.strokes);
  const ids = new Set(op.strokes.map((stroke) => stroke.id));
  return {
    strokes:
      op.type === "add"
        ? [...state.strokes, ...op.strokes].sort((a, b) => a.createdAt - b.createdAt)
        : state.strokes.filter((stroke) => !ids.has(stroke.id)),
    version: state.version + 1,
    dirty: addDirty(state.dirty, changed),
  };
}

function inverse(op: Operation): Operation {
  return { type: op.type === "add" ? "remove" : "add", strokes: op.strokes };
}

function commit(state: NotebookState, op: Operation): NotebookState {
  return { ...state, ...apply(state, op), undoStack: [...state.undoStack, op], redoStack: [] };
}

export function addStroke(state: NotebookState, stroke: PenStroke): NotebookState {
  return commit(state, { type: "add", strokes: [stroke] });
}

export function removeStrokes(state: NotebookState, ids: Iterable<string>): NotebookState {
  const wanted = new Set(ids);
  const strokes = state.strokes.filter((stroke) => wanted.has(stroke.id));
  return strokes.length ? commit(state, { type: "remove", strokes }) : state;
}

export function clearPage(state: NotebookState): NotebookState {
  return state.strokes.length ? commit(state, { type: "remove", strokes: state.strokes }) : state;
}

export function undo(state: NotebookState): NotebookState {
  const op = state.undoStack.at(-1);
  if (!op) return state;
  return {
    ...state,
    ...apply(state, inverse(op)),
    undoStack: state.undoStack.slice(0, -1),
    redoStack: [...state.redoStack, op],
  };
}

export function redo(state: NotebookState): NotebookState {
  const op = state.redoStack.at(-1);
  if (!op) return state;
  return {
    ...state,
    ...apply(state, op),
    redoStack: state.redoStack.slice(0, -1),
    undoStack: [...state.undoStack, op],
  };
}

export function takeDirty(state: NotebookState): { state: NotebookState; dirty: Bounds[] } {
  return { state: { ...state, dirty: [] }, dirty: state.dirty };
}

// Put areas back after an analysis was abandoned, so their changes are still checked later.
export function restoreDirty(state: NotebookState, dirty: Bounds[]): NotebookState {
  return { ...state, dirty: dirty.reduce(addDirty, state.dirty) };
}

export function pageHeight(strokes: PenStroke[], viewportWorldHeight: number): number {
  const bottom = strokes.reduce((lowest, stroke) => Math.max(lowest, strokeBounds(stroke).maxY), 0);
  return Math.max(bottom + viewportWorldHeight * 0.75, viewportWorldHeight);
}
