// The account's side of the API, and what's kept on this device so it opens without a connection.

import { sendJson } from "./api";
import type {
  CalibrationExport,
  CalibrationState,
  CurrentSession,
  DeleteAccountResult,
  MistakeEntryDetail,
  MistakeNotebook,
  PenStroke,
  PracticeCheckResponse,
  PracticeOptions,
  PracticeProblem,
  PracticeReveal,
  PracticeSummary,
  PracticeTopic,
  Profile,
  Recap,
  RecapDetail,
  SessionHistory,
  InkLine,
  Trigger,
} from "./types";

export const fetchProfile = () => sendJson<Profile>("GET", "/api/profile");
export const fetchCalibration = () => sendJson<CalibrationState>("GET", "/api/calibration");
export const saveSample = (promptId: string, strokes: readonly PenStroke[], canvasWidth: number, image: string) =>
  sendJson<CalibrationState>("PUT", `/api/calibration/samples/${promptId}`, { strokes, canvasWidth, image });
export const exportCalibration = () => sendJson<CalibrationExport>("GET", "/api/calibration/export");
export const deleteCalibration = () => sendJson<Profile>("DELETE", "/api/calibration");
export const restartCalibration = () => sendJson<CalibrationState>("POST", "/api/calibration/restart");
export const deleteLearning = () => sendJson<{ deleted: boolean }>("DELETE", "/api/account/learning");
export const deleteAccount = () => sendJson<DeleteAccountResult>("DELETE", "/api/account");

export const currentSession = () => sendJson<CurrentSession>("POST", "/api/sessions/current");
export const finishSession = (id: string) => sendJson<Recap>("POST", `/api/sessions/${id}/finish`);
export const fetchHistory = () => sendJson<SessionHistory>("GET", "/api/sessions");
export const fetchRecap = (id: string) => sendJson<RecapDetail>("GET", `/api/sessions/${id}/recap`);

export const fetchPracticeTopics = () => sendJson<PracticeOptions>("GET", "/api/practice/topics");
export const startPractice = (topic: PracticeTopic | null, originSessionId: string | null, sourceMistakeId: string | null = null) =>
  sendJson<PracticeProblem>("POST", "/api/practice/sessions", { topic, originSessionId, sourceMistakeId });

export const fetchMistakes = (sessionId?: string | null) =>
  sendJson<MistakeNotebook>("GET", `/api/mistakes${sessionId ? `?session=${encodeURIComponent(sessionId)}` : ""}`);
export const fetchMistake = (id: string) => sendJson<MistakeEntryDetail>("GET", `/api/mistakes/${encodeURIComponent(id)}`);
export const deleteMistake = (id: string) => sendJson<{ deleted: boolean }>("DELETE", `/api/mistakes/${encodeURIComponent(id)}`);
export const nextProblem = (sessionId: string) => sendJson<PracticeProblem>("POST", `/api/practice/sessions/${sessionId}/next`);
export const finishPractice = (sessionId: string) => sendJson<PracticeSummary>("POST", `/api/practice/sessions/${sessionId}/finish`);
export const revealAnswer = (problemId: string) => sendJson<PracticeReveal>("POST", `/api/practice/problems/${problemId}/reveal`);

export function checkPracticeAnswer(problemId: string) {
  return (
    sessionId: string,
    snapshotVersion: number,
    image: string,
    trigger: Trigger,
    acknowledgedMistakeIds: string[],
    signal?: AbortSignal,
    outOfViewMistakeIds: string[] = [],
    requestId?: string,
    inkLines?: InkLine[],
    attempt = 0,
  ) =>
    sendJson<PracticeCheckResponse>(
      "POST",
      `/api/practice/problems/${problemId}/check`,
      { sessionId, snapshotVersion, image, trigger, acknowledgedMistakeIds, outOfViewMistakeIds, requestId, inkLines, attempt },
      signal,
    );
}

function read<T>(key: string, fallback: T): T {
  try {
    return (JSON.parse(localStorage.getItem(key) ?? "null") as T | null) ?? fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Only a convenience. The server still has it.
  }
}

// So that a student who has calibrated is never sent back to it just because the server can't be reached.
export const cachedProfile = (userId: string) => read<Profile | null>(`quack.profile.${userId}`, null);
export const cacheProfile = (profile: Profile) => write(`quack.profile.${profile.userId}`, profile);

const MAX_CACHED_RECAPS = 20;

// Recaps on this device, so they can be read with no connection at all.
export const cachedRecaps = (userId: string) => read<Recap[]>(`quack.recaps.v2.${userId}`, []);

// So that My Mistakes opens with no connection too. It's what the server last said, and no more.
export const cachedMistakes = (userId: string) => read<MistakeNotebook | null>(`quack.mistakes.${userId}`, null);
export const cacheMistakes = (userId: string, notebook: MistakeNotebook) => write(`quack.mistakes.${userId}`, notebook);

// Which strokes each mistake was on, on this device, for showing it again. Strokes aren't copied, so
// erasing them or deleting notes takes the picture with them.
const MAX_INK_REMEMBERED = 200;
export const rememberedInk = (userId: string) => read<Record<string, string[]>>(`quack.mistake-ink.${userId}`, {});
export function rememberInk(userId: string, mistakeId: string, strokeIds: string[]) {
  const all = rememberedInk(userId);
  delete all[mistakeId];
  all[mistakeId] = strokeIds;
  const kept = Object.entries(all).slice(-MAX_INK_REMEMBERED);
  write(`quack.mistake-ink.${userId}`, Object.fromEntries(kept));
}

export function cacheRecap(userId: string, recap: Recap) {
  const others = cachedRecaps(userId).filter((item) => item.sessionId !== recap.sessionId);
  write(`quack.recaps.v2.${userId}`, [recap, ...others].slice(0, MAX_CACHED_RECAPS));
}

export function forgetCachedLearning(userId: string) {
  try {
    localStorage.removeItem(`quack.recaps.${userId}`);
    localStorage.removeItem(`quack.recaps.v2.${userId}`);
    localStorage.removeItem(`quack.mistakes.${userId}`);
    localStorage.removeItem(`quack.mistake-ink.${userId}`);
    localStorage.removeItem(`quack.events.${userId}`);
  } catch {
    // Nothing more can be done about storage that can't be reached.
  }
}
