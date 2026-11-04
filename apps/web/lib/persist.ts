// Notes stay in the browser. Strokes and the version are saved, undo history is not.

import { createStore, del, get, set, update, type UseStore } from "idb-keyval";
import type { Bounds, PenStroke } from "./types";

// What the student has already been told, so that a reload can't quack about it again.
export type SeenMistakes = Record<string, { dismissed: boolean; area: Bounds | null }>;

// `dirty` is writing not yet checked, so a reload doesn't forget to check it.
export type SavedNotebook = { strokes: PenStroke[]; version: number; seen?: SeenMistakes; dirty?: Bounds[] };

export type WriteOutcome = "saved" | "failed" | "conflict";

export type LoadResult = { status: "found"; saved: SavedNotebook } | { status: "empty" } | { status: "failed" };

const KEY = "notebook";
let store: UseStore | null = null;

function db(): UseStore {
  store ??= createStore("quack", "notes");
  return store;
}

// Failing to read is not the same as having nothing saved. Treating it as empty would let the
// next stroke overwrite a student's notes.
export async function loadNotebook(key: string = KEY): Promise<LoadResult> {
  try {
    const saved = await get<SavedNotebook>(key, db());
    return saved ? { status: "found", saved } : { status: "empty" };
  } catch {
    return { status: "failed" };
  }
}

export async function saveNotebook(saved: SavedNotebook): Promise<boolean> {
  try {
    await set(KEY, saved, db());
    return true;
  } catch {
    return false;
  }
}

// A second tab holds its own copy of the notes, and writing that copy over the save would lose the
// other tab's work. So the write only goes ahead if the save is still the version we last knew,
// checked in one transaction so that nothing can slip in between. `known` is null if there was none.
export async function writeNotebook(saved: SavedNotebook, known: number | null, key: string = KEY): Promise<WriteOutcome> {
  let conflict = false;
  try {
    await update<SavedNotebook>(
      key,
      (current) => {
        conflict = current !== undefined && current.version !== known;
        return conflict ? current! : saved;
      },
      db(),
    );
    return conflict ? "conflict" : "saved";
  } catch {
    return "failed";
  }
}

// Each account has its own notes on this device, under its own key.
export function notebookKeyFor(userId: string): string {
  return `notebook:${userId}`;
}

// Notes from before accounts stay exactly where they are until the student says what to do with them.
export async function hasNotesFromBeforeAccounts(): Promise<boolean> {
  const result = await loadNotebook();
  return result.status === "found" && result.saved.strokes.length > 0;
}

// Only into an account with nothing saved, so that it can't overwrite anything.
export async function bringInNotes(userId: string): Promise<boolean> {
  const earlier = await loadNotebook();
  if (earlier.status !== "found") return false;
  const outcome = await writeNotebook(earlier.saved, null, notebookKeyFor(userId));
  if (outcome !== "saved") return false;
  await del(KEY, db());
  return true;
}

export async function clearSaved(): Promise<void> {
  await del(KEY, db());
}

// `crypto.randomUUID` only exists on secure origins, and an iPad on the local network
// reaches the dev server over plain HTTP.
export function randomId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

let sessionId: string | null = null;

// One per page load. The notebook version only rises within a load, while a stored session
// could outlive the version it was paired with, and every check would then look stale.
export function getSessionId(): string {
  sessionId ??= randomId();
  return sessionId;
}
