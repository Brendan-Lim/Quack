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

const object = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const bounds = (value: unknown): value is Bounds => object(value) &&
  finite(value.minX) && finite(value.minY) && finite(value.maxX) && finite(value.maxY) &&
  value.minX <= value.maxX && value.minY <= value.maxY;

// IndexedDB is an input boundary too: old app versions, failed imports and extensions can leave
// malformed values. A failed validation must preserve the stored value for recovery.
export function isSavedNotebook(value: unknown): value is SavedNotebook {
  if (!object(value) || !Number.isSafeInteger(value.version) || (value.version as number) < 0 || !Array.isArray(value.strokes)) return false;
  const ids = new Set<string>();
  for (const stroke of value.strokes) {
    if (!object(stroke) || typeof stroke.id !== "string" || !stroke.id || ids.has(stroke.id) ||
      typeof stroke.color !== "string" || !finite(stroke.width) || stroke.width < 0 || !finite(stroke.createdAt) ||
      !Array.isArray(stroke.points) || !stroke.points.length) return false;
    ids.add(stroke.id);
    for (const point of stroke.points) {
      if (!object(point) || !finite(point.x) || !finite(point.y) || !finite(point.timestamp) ||
        (point.pressure !== undefined && (!finite(point.pressure) || point.pressure < 0 || point.pressure > 1))) return false;
    }
  }
  if (value.dirty !== undefined && (!Array.isArray(value.dirty) || !value.dirty.every(bounds))) return false;
  if (value.seen !== undefined && (!object(value.seen) || !Object.values(value.seen).every((item) =>
    object(item) && typeof item.dismissed === "boolean" && (item.area === null || bounds(item.area))))) return false;
  return true;
}

// Failing to read is not the same as having nothing saved. Treating it as empty would let the
// next stroke overwrite a student's notes.
export async function loadNotebook(key: string = KEY): Promise<LoadResult> {
  try {
    const saved = await get<unknown>(key, db());
    if (saved === undefined) return { status: "empty" };
    return isSavedNotebook(saved) ? { status: "found", saved } : { status: "failed" };
  } catch {
    return { status: "failed" };
  }
}

export async function saveNotebook(saved: SavedNotebook): Promise<boolean> {
  if (!isSavedNotebook(saved)) return false;
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
  if (!isSavedNotebook(saved) || (known !== null && (!Number.isSafeInteger(known) || known < 0 || saved.version < known))) return "failed";
  let conflict = false;
  try {
    await update<SavedNotebook>(
      key,
      (current) => {
        conflict = current === undefined ? known !== null : !isSavedNotebook(current) || current.version !== known;
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
