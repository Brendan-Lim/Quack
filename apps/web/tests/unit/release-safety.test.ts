import "fake-indexeddb/auto";
import { createStore, del, get, set } from "idb-keyval";
import { afterEach, describe, expect, it, vi } from "vitest";
import { analyze, ApiError, sendJson, setAccount, setStudySession } from "@/lib/api";
import { isSavedNotebook, loadNotebook, writeNotebook, type SavedNotebook } from "@/lib/persist";
import { AnalysisScheduler, RequestTimedOut } from "@/lib/scheduler";
import { NotebookStore } from "@/lib/store";
import type { AnalyzeResponse, PenStroke } from "@/lib/types";

const ink: PenStroke = {
  id: "s1", color: "#000", width: 2, createdAt: 1,
  points: [{ x: 1, y: 2, timestamp: 1, pressure: .5 }],
};
const valid = (): SavedNotebook => ({ strokes: [structuredClone(ink)], version: 1 });
const db = createStore("quack", "notes");
let sequence = 0;
const key = () => `release-safety-${sequence++}`;

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  setAccount(async () => null, () => {});
  setStudySession(() => null);
});

describe("saved notebook integrity", () => {
  const invalid: [string, unknown][] = [
    ["null", null], ["non-object", []], ["missing version", { strokes: [] }],
    ["negative version", { ...valid(), version: -1 }],
    ["fractional version", { ...valid(), version: 1.5 }],
    ["unsafe version", { ...valid(), version: Number.MAX_SAFE_INTEGER + 1 }],
    ["invalid strokes", { version: 1, strokes: [null] }],
    ["empty stroke", { version: 1, strokes: [{ ...ink, points: [] }] }],
    ["duplicate ids", { version: 1, strokes: [ink, ink] }],
    ["invalid coordinates", { version: 1, strokes: [{ ...ink, points: [{ x: NaN, y: 1, timestamp: 0 }] }] }],
    ["infinite width", { version: 1, strokes: [{ ...ink, width: Infinity }] }],
    ["negative width", { version: 1, strokes: [{ ...ink, width: -1 }] }],
    ["missing timestamp", { version: 1, strokes: [{ ...ink, points: [{ x: 1, y: 1 }] }] }],
    ["invalid pressure", { version: 1, strokes: [{ ...ink, points: [{ x: 1, y: 1, timestamp: 1, pressure: 2 }] }] }],
    ["reversed bounds", { ...valid(), dirty: [{ minX: 10, maxX: 0, minY: 0, maxY: 10 }] }],
    ["invalid seen record", { ...valid(), seen: { m: { dismissed: "yes", area: null } } }],
  ];

  it.each(invalid)("preserves a corrupt value: %s", async (_label, stored) => {
    const name = key();
    await set(name, stored, db);
    expect(isSavedNotebook(stored)).toBe(false);
    expect(await loadNotebook(name)).toEqual({ status: "failed" });
    expect(await writeNotebook(valid(), null, name)).toBe("conflict");
    expect(await get(name, db)).toEqual(stored);
  });

  it("accepts older notebooks without optional metadata", async () => {
    const name = key();
    await set(name, valid(), db);
    expect(await loadNotebook(name)).toEqual({ status: "found", saved: valid() });
  });

  it("reports a genuinely absent save as empty", async () => {
    expect(await loadNotebook(key())).toEqual({ status: "empty" });
  });

  it("does not resurrect notes deleted by another tab", async () => {
    const name = key();
    await set(name, valid(), db);
    await del(name, db);
    expect(await writeNotebook({ ...valid(), version: 2 }, 1, name)).toBe("conflict");
    expect(await get(name, db)).toBeUndefined();
  });

  it("allows exactly one of two concurrent first writers", async () => {
    const name = key();
    const results = await Promise.all([
      writeNotebook(valid(), null, name),
      writeNotebook({ strokes: [], version: 2 }, null, name),
    ]);
    expect(results.sort()).toEqual(["conflict", "saved"]);
  });

  it("rejects invalid writes before they replace good notes", async () => {
    const name = key();
    await set(name, valid(), db);
    expect(await writeNotebook({ ...valid(), version: NaN }, 1, name)).toBe("failed");
    expect(await writeNotebook({ ...valid(), version: 0 }, 1, name)).toBe("failed");
    expect(await get(name, db)).toEqual(valid());
  });

  it("keeps saving disabled when hydration found corruption", async () => {
    const name = key();
    const corrupted = { strokes: "recover this", version: 1 };
    await set(name, corrupted, db);
    const notebook = new NotebookStore({ key: name });
    expect((await notebook.hydrate()).status).toBe("failed");
    notebook.add(ink);
    notebook.flush();
    expect(await get(name, db)).toEqual(corrupted);
  });
});

describe("HTTP failures and account isolation", () => {
  it.each([400, 401, 403, 413, 422, 429, 500, 503])("rejects HTTP %i without accepting its body", async (status) => {
    const signedOut = vi.fn();
    setAccount(async () => "token", signedOut);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("private server error", { status })));
    await expect(sendJson("GET", "/api/test")).rejects.toMatchObject({ status });
    expect(signedOut).toHaveBeenCalledTimes(status === 401 ? 1 : 0);
  });

  it("passes the abort signal and signed-in token to fetch", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"ok":true}'));
    vi.stubGlobal("fetch", fetchMock);
    setAccount(async () => "test-token", () => {});
    await sendJson("POST", "/api/test", { value: 1 }, controller.signal);
    expect(fetchMock).toHaveBeenCalledWith("/api/test", expect.objectContaining({
      signal: controller.signal, body: '{"value":1}',
      headers: { authorization: "Bearer test-token", "content-type": "application/json" },
    }));
  });

  it("does not send an old token after sign-out", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);
    setAccount(async () => "old-token", () => {});
    await sendJson("GET", "/api/test");
    setAccount(async () => null, () => {});
    await sendJson("GET", "/api/test");
    expect(fetchMock.mock.calls[1][1].headers).toEqual({});
  });

  it("includes the current study session in analysis requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);
    setStudySession(() => "study-1");
    await analyze("ink-session", 3, "data:image/png;base64,", "manual", ["m1"]);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({ studySessionId: "study-1", snapshotVersion: 3 });
  });

  it("returns a typed error without echoing sensitive response content", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("secret", { status: 500 })));
    await expect(sendJson("GET", "/api/test")).rejects.toBeInstanceOf(ApiError);
    await expect(sendJson("GET", "/api/test")).rejects.not.toThrow("secret");
  });
});

describe("scheduler cancellation and lifecycle", () => {
  function setup() {
    vi.useFakeTimers();
    const deliveries: ((response: AnalyzeResponse) => void)[] = [];
    const send = vi.fn(() =>
      new Promise<AnalyzeResponse>((resolve) => deliveries.push(resolve)));
    const restore = vi.fn();
    const onResult = vi.fn();
    const onError = vi.fn();
    const onBusyChange = vi.fn();
    const scheduler = new AnalysisScheduler({
      getVersion: () => 1,
      capture: () => ({ hash: "ink", version: 1, image: "data:", restore }),
      send, onResult, onError, onBusyChange, requestTimeoutMs: 100,
    });
    const result = { snapshotVersion: 1, stale: false, modelIssue: null } as AnalyzeResponse;
    return { scheduler, send, restore, onResult, onError, onBusyChange, deliveries, result };
  }

  it("times out even if the transport ignores abort, then retries", async () => {
    const h = setup();
    h.scheduler.checkNow();
    await vi.advanceTimersByTimeAsync(100);
    expect(h.onError).toHaveBeenCalledWith(expect.any(RequestTimedOut), "manual");
    expect(h.restore).toHaveBeenCalledOnce();
    expect(h.onBusyChange).toHaveBeenLastCalledWith(false);
    await vi.advanceTimersByTimeAsync(2000);
    expect(h.send).toHaveBeenCalledTimes(2);
    h.scheduler.dispose();
    await vi.advanceTimersByTimeAsync(0);
  });

  it("does not apply a late response after disposal", async () => {
    const h = setup();
    h.scheduler.checkNow();
    h.scheduler.dispose();
    h.deliveries[0](h.result);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(h.onResult).not.toHaveBeenCalled();
    expect(h.onError).not.toHaveBeenCalled();
    expect(h.send).toHaveBeenCalledOnce();
    expect(h.onBusyChange).toHaveBeenLastCalledWith(false);
  });

  it("cannot be restarted after disposal", async () => {
    const h = setup();
    h.scheduler.dispose();
    h.scheduler.checkNow();
    h.scheduler.notifyEdit();
    h.scheduler.setEnabled(true);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(h.send).not.toHaveBeenCalled();
  });

  it("turning monitoring off clears a queued manual request", async () => {
    const h = setup();
    h.scheduler.notifyEdit();
    await vi.advanceTimersByTimeAsync(800);
    h.scheduler.checkNow();
    h.scheduler.setEnabled(false);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(h.send).toHaveBeenCalledOnce();
    expect(h.onError).not.toHaveBeenCalled();
    h.scheduler.dispose();
  });

  it("re-enabling immediately after cancellation starts one new run", async () => {
    const h = setup();
    h.scheduler.checkNow();
    h.scheduler.setEnabled(false);
    h.scheduler.setEnabled(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(h.send).toHaveBeenCalledTimes(2);
    h.deliveries[0](h.result);
    await vi.advanceTimersByTimeAsync(0);
    expect(h.onResult).not.toHaveBeenCalled();
    h.deliveries[1](h.result);
    await vi.advanceTimersByTimeAsync(0);
    expect(h.onResult).toHaveBeenCalledOnce();
    h.scheduler.dispose();
  });
});
