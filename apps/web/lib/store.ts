// Holds notebook state outside React, so that drawing never waits on a render.

import {
  addStroke,
  clearPage,
  emptyNotebook,
  redo,
  removeStrokes,
  restoreDirty,
  takeDirty,
  undo,
  type NotebookState,
} from "./notebook";
import { loadNotebook, writeNotebook, type SeenMistakes } from "./persist";
import type { Bounds, PenStroke } from "./types";

const SAVE_DELAY_MS = 400;
const SAVE_RETRY_MS = 5000;

export type HydrateResult = { status: "found" | "empty" | "failed"; seen: SeenMistakes; unchecked: boolean };
export type SaveIssue = "failed" | "conflict";

export class NotebookStore {
  private state: NotebookState = emptyNotebook;
  private listeners = new Set<() => void>();
  private saveTimer: ReturnType<typeof setTimeout> | null = null;
  private editListener: (() => void) | null = null;
  private readonly persist: boolean;
  private readonly key: string | undefined;
  private seen: SeenMistakes = {};
  private viewport: Bounds | null = null;
  // Nothing may be written until the saved notebook has been read. Otherwise an early stroke
  // replaces it, and a failed read must never let an empty page overwrite the save.
  private canSave = false;
  private hydration: Promise<HydrateResult> | null = null;
  private savedVersion: number | null = null;
  private writing = false;
  private writeAgain = false;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private saveListener: ((issue: SaveIssue | null) => void) | null = null;

  constructor({ persist = true, key }: { persist?: boolean; key?: string } = {}) {
    this.persist = persist;
    this.key = key;
  }

  getState = () => this.state;

  setEditListener(listener: (() => void) | null) {
    this.editListener = listener;
  }

  setSaveListener(listener: ((issue: SaveIssue | null) => void) | null) {
    this.saveListener = listener;
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  private notify() {
    this.listeners.forEach((listener) => listener());
  }

  private update(next: NotebookState) {
    const edited = next.version !== this.state.version;
    this.state = next;
    this.notify();
    if (edited) {
      this.scheduleSave();
      this.editListener?.();
    }
  }

  hydrate(): Promise<HydrateResult> {
    if (!this.persist) return Promise.resolve({ status: "empty", seen: {}, unchecked: false });
    this.hydration ??= this.load();
    return this.hydration;
  }

  private async load(): Promise<HydrateResult> {
    const result = await loadNotebook(this.key);
    if (result.status === "failed") return { status: "failed", seen: {}, unchecked: false };
    this.canSave = true;
    if (result.status === "empty") {
      if (this.state.version > 0) this.scheduleSave();
      return { status: "empty", seen: {}, unchecked: false };
    }

    // Strokes drawn while the save was loading go on top of it, and the version only rises.
    const { saved } = result;
    const early = this.state.strokes;
    const hadEarlyWork = this.state.version > 0;
    this.savedVersion = saved.version;
    this.state = {
      ...restoreDirty(this.state, saved.dirty ?? []),
      strokes: [...saved.strokes, ...early],
      version: saved.version + this.state.version,
    };
    this.seen = saved.seen ?? {};
    this.notify();
    if (hadEarlyWork) {
      this.scheduleSave();
      this.editListener?.();
    }
    return { status: "found", seen: this.seen, unchecked: Boolean(saved.dirty?.length) };
  }

  setSeen(seen: SeenMistakes) {
    this.seen = seen;
    this.scheduleSave();
  }

  private async write() {
    // One at a time, because each write checks the save against the one before it.
    if (this.writing) {
      this.writeAgain = true;
      return;
    }
    this.writing = true;
    do {
      this.writeAgain = false;
      const { strokes, version, dirty } = this.state;
      const outcome = await writeNotebook({ strokes, version, seen: this.seen, dirty }, this.savedVersion, this.key);
      if (outcome === "saved") {
        this.savedVersion = version;
        this.saveListener?.(null);
      } else if (outcome === "conflict") {
        // Stop here. This tab's copy is out of date, and the other tab's work matters as much.
        this.canSave = false;
        this.saveListener?.("conflict");
      } else {
        this.saveListener?.("failed");
        // One retry waiting at a time, however many saves have failed.
        this.retryTimer ??= setTimeout(() => {
          this.retryTimer = null;
          this.scheduleSave();
        }, SAVE_RETRY_MS);
      }
    } while (this.writeAgain && this.canSave);
    this.writing = false;
  }

  private scheduleSave() {
    if (!this.persist || !this.canSave) return;
    if (this.saveTimer !== null) clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => {
      this.saveTimer = null;
      void this.write();
    }, SAVE_DELAY_MS);
  }

  // Called when the page is being hidden or closed, when a pending save might never fire.
  flush() {
    if (this.saveTimer === null || !this.canSave) return;
    clearTimeout(this.saveTimer);
    this.saveTimer = null;
    void this.write();
  }

  add = (stroke: PenStroke) => this.update(addStroke(this.state, stroke));
  remove = (ids: Iterable<string>) => this.update(removeStrokes(this.state, ids));
  clear = () => this.update(clearPage(this.state));
  undo = () => this.update(undo(this.state));
  redo = () => this.update(redo(this.state));

  // Start again with nothing to undo. Used between collection samples.
  reset() {
    this.state = { ...emptyNotebook, version: this.state.version + 1 };
    this.notify();
  }

  // Reading the dirty areas is not an edit, so it does not notify anyone.
  takeDirty(): Bounds[] {
    const { state, dirty } = takeDirty(this.state);
    this.state = state;
    return dirty;
  }

  restoreDirty(dirty: Bounds[]) {
    this.state = restoreDirty(this.state, dirty);
  }

  setViewport(viewport: Bounds | null) {
    this.viewport = viewport;
  }

  getViewport(): Bounds | null {
    return this.viewport;
  }
}
