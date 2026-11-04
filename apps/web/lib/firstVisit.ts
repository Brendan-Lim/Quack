// Whether to ask a student, once, which of Quack's two things they'd like to do first.
import { fetchLearnHome } from "./learn";
import { loadNotebook, notebookKeyFor } from "./persist";
import type { LearnHome } from "./types";

export type StartingMode = "notebook" | "learn";

export const choiceKey = (userId: string) => `quack.mode-chosen.${userId}`;

// Someone who has been here before: a lesson under way, a level mastered, or notes on this device.
export function hasBeenHere(home: LearnHome | null, notebookStrokes: number): boolean {
  if (notebookStrokes > 0) return true;
  if (!home) return false;
  return Boolean(home.continueLearning) || home.paths.some((path) => (path.mastered ?? 0) > 0);
}

type Deps = {
  storage: Pick<Storage, "getItem" | "setItem">;
  learnHome: () => Promise<LearnHome>;
  notebookStrokes: (userId: string) => Promise<number>;
  // How long to wait for the server before not asking. Signing in mustn't wait on this.
  waitMs?: number;
};

const ASK_WAIT_MS = 2500;

const defaults = (): Deps => ({
  storage: localStorage,
  learnHome: fetchLearnHome,
  notebookStrokes: async (userId) => {
    const loaded = await loadNotebook(notebookKeyFor(userId));
    return loaded.status === "found" ? loaded.saved.strokes.length : 0;
  },
});

// Asked once per student on this device, and never of someone who's clearly been here before. If the server can't
// say, it isn't asked: a returning student must never be stopped at the door to choose again.
export async function shouldAskWhereToStart(userId: string, deps: Deps = defaults()): Promise<boolean> {
  if (deps.storage.getItem(choiceKey(userId))) return false;
  try {
    const late = new Promise<never>((_, reject) => setTimeout(() => reject(new Error("too slow")), deps.waitMs ?? ASK_WAIT_MS));
    const [home, strokes] = await Promise.race([Promise.all([deps.learnHome(), deps.notebookStrokes(userId).catch(() => 0)]), late]);
    if (hasBeenHere(home, strokes)) {
      deps.storage.setItem(choiceKey(userId), "returning");
      return false;
    }
    return true;
  } catch {
    return false;
  }
}

export function rememberChoice(userId: string, mode: StartingMode, storage: Pick<Storage, "setItem"> = localStorage): void {
  storage.setItem(choiceKey(userId), mode);
}
