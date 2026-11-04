import { RequestTimedOut } from "./scheduler";
import type { AnalyzeResponse, AskAction, AskResponse, AssistanceResponse, CheckContext, InkLine, ModelIssue, Trigger, VoiceResponse } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

// Who's signed in, and what they're working on. Unset when accounts are off.
let accessToken: () => Promise<string | null> = async () => null;
let onSignedOut: () => void = () => {};
let studySessionId: () => string | null = () => null;

export function setAccount(token: () => Promise<string | null>, signedOut: () => void) {
  accessToken = token;
  onSignedOut = signedOut;
}

export function setStudySession(current: () => string | null) {
  studySessionId = current;
}

async function authHeaders(): Promise<Record<string, string>> {
  const token = await accessToken();
  return token ? { authorization: `Bearer ${token}` } : {};
}

function checked(path: string, response: Response): Response {
  if (response.status === 401) onSignedOut();
  if (!response.ok) throw new ApiError(response.status, `${path} failed with ${response.status}`);
  return response;
}

export async function sendJson<T>(method: "GET" | "POST" | "PUT" | "DELETE", path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: { ...(body === undefined ? {} : { "content-type": "application/json" }), ...(await authHeaders()) },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  return (await checked(path, response).json()) as T;
}

function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  return sendJson("POST", path, body, signal);
}

export function analyze(
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
  context?: CheckContext & { mistakeLineIds?: Record<string, string[]> },
): Promise<AnalyzeResponse> {
  return postJson(
    "/api/analyze",
    { sessionId, snapshotVersion, image, trigger, acknowledgedMistakeIds, outOfViewMistakeIds, requestId, inkLines, attempt, studySessionId: studySessionId(), ...context },
    signal,
  );
}

export function askAboutSelection(
  body: { sessionId: string; action: AskAction; question?: string; level: 1 | 2; image?: string; inkLines: InkLine[]; selectedLineIds: string[]; partialLineIds: string[]; mistakeId?: string } & Partial<CheckContext>,
  signal?: AbortSignal,
): Promise<AskResponse> {
  return postJson("/api/ask", body, signal);
}

export function requestHint(sessionId: string, mistakeId: string, level: 1 | 2): Promise<AssistanceResponse> {
  return postJson("/api/hint", { sessionId, mistakeId, level, studySessionId: studySessionId() });
}

export function requestExplanation(sessionId: string, mistakeId: string, question?: string): Promise<AssistanceResponse> {
  return postJson("/api/explain", { sessionId, mistakeId, question: question || null, studySessionId: studySessionId() });
}

export async function sendVoice(sessionId: string, mistakeId: string | null, wav: Blob): Promise<VoiceResponse> {
  const form = new FormData();
  form.append("sessionId", sessionId);
  if (mistakeId) form.append("mistakeId", mistakeId);
  const studySession = studySessionId();
  if (studySession) form.append("studySessionId", studySession);
  form.append("audio", wav, "recording.wav");
  const response = await fetch("/api/voice", { method: "POST", body: form, headers: await authHeaders() });
  return (await checked("/api/voice", response).json()) as VoiceResponse;
}

export function issueFromError(error: unknown): ModelIssue {
  if (error instanceof RequestTimedOut) {
    return { category: "timeout", message: "Quack took too long to check that. It will try again when you write more." };
  }
  if (error instanceof ApiError && error.status === 429) {
    return { category: "rate_limited", message: "You're checking faster than Quack can keep up." };
  }
  if (error instanceof ApiError && error.status === 404) {
    return { category: "unknown_mistake", message: "Quack lost track of that step. Tap Check my work to look again." };
  }
  if (error instanceof ApiError && [400, 413, 422].includes(error.status)) {
    return { category: "invalid_request", message: "Quack couldn't use that image." };
  }
  return { category: "network", message: "Quack can't reach its server. Your notes are safe." };
}

export function clearMistakes(sessionId: string, snapshotVersion: number, acknowledgedMistakeIds: string[]): Promise<unknown> {
  return postJson("/api/clear", { sessionId, snapshotVersion, acknowledgedMistakeIds });
}

export async function fetchHealth(): Promise<{ status: string; modelConfigured: boolean; environment: string }> {
  const response = await fetch("/api/health");
  if (!response.ok) throw new ApiError(response.status, "health check failed");
  return response.json();
}
