import { describe, expect, it, vi } from "vitest";
import { AuthProblem, LocalAuth, SupabaseAuth } from "@/lib/auth";
import { eventsFrom, LearningQueue } from "@/lib/learning";
import type { AnalyzeResponse, LearningEventIn, Mistake } from "@/lib/types";

class MemoryStorage {
  private items = new Map<string, string>();
  getItem = (key: string) => this.items.get(key) ?? null;
  setItem = (key: string, value: string) => void this.items.set(key, value);
  removeItem = (key: string) => void this.items.delete(key);
}

const mistake = (id: string, isNew: boolean): Mistake => ({
  id,
  category: "arithmetic",
  errorType: "arithmetic_error",
  operationType: "arithmetic",
  previousStep: "2 * 3",
  currentStep: "5",
  verified: true,
  region: null,
  lineId: null,
  problemId: null,
  parsedOperation: "multiplication",
  isNew,
});

const reply = (fields: Partial<AnalyzeResponse>) => ({ mistakes: [], resolvedMistakeIds: [], ...fields }) as unknown as AnalyzeResponse;

describe("what a reply says happened", () => {
  it("only counts a mistake when it's new to the student", () => {
    expect(eventsFrom(reply({ mistakes: [mistake("m1", true), mistake("m2", false)] }))).toEqual([{ eventType: "mistake_detected", mistakeId: "m1" }]);
  });

  it("calls a mistake that's gone a correction", () => {
    expect(eventsFrom(reply({ resolvedMistakeIds: ["m1"] }))).toEqual([{ eventType: "correction_verified", mistakeId: "m1" }]);
  });

  it("doesn't call another wrong try at the same line a correction", () => {
    const events = eventsFrom(reply({ resolvedMistakeIds: ["m1"], mistakes: [mistake("m2", true)] }), { m2: "m1" });
    expect(events).toEqual([
      { eventType: "mistake_invalidated", mistakeId: "m1", reason: "replaced" },
      { eventType: "mistake_detected", mistakeId: "m2" },
    ]);
  });
});

describe("the event queue", () => {
  const event = { eventType: "mistake_detected" as const, mistakeId: "m1" };

  it("keeps events until the server has them, across a reload", async () => {
    const storage = new MemoryStorage();
    const failing = vi.fn().mockRejectedValue(new Error("offline"));
    const queue = new LearningQueue("q", () => "s1", failing, storage);
    queue.record(event);
    await queue.flush();
    queue.dispose();
    expect(queue.pending).toBe(1);

    const sent: LearningEventIn[][] = [];
    const reloaded = new LearningQueue("q", () => "s2", async (events) => (sent.push(events), { refused: [] }), storage);
    expect(reloaded.pending).toBe(1);
    await reloaded.flush();
    reloaded.dispose();
    expect(reloaded.pending).toBe(0);
    // Still the session it happened in, not the one current when it was finally sent.
    expect(sent[0][0].sessionId).toBe("s1");
    expect(storage.getItem("q")).toBe("[]");
  });

  it("sends the same id on a retry, so the server can tell", async () => {
    const seen: string[] = [];
    let fail = true;
    const queue = new LearningQueue(
      "q",
      () => "s",
      async (events) => {
        seen.push(events[0].id);
        if (fail) throw new Error("lost");
        return { refused: [] };
      },
      new MemoryStorage(),
    );
    queue.record(event);
    await queue.flush();
    fail = false;
    await queue.flush();
    queue.dispose();
    expect(seen).toHaveLength(2);
    expect(seen[0]).toBe(seen[1]);
  });

  it("drops what the server refuses, as asking again won't change that", async () => {
    const queue = new LearningQueue("q", () => "s", async (events) => ({ refused: events.map(({ id }) => ({ id, reason: "unknown_mistake" })) }), new MemoryStorage());
    queue.record(event);
    await queue.flush();
    queue.dispose();
    expect(queue.pending).toBe(0);
  });

  it("files practice events under the practice session", async () => {
    const sent: LearningEventIn[] = [];
    const queue = new LearningQueue("q", () => "notebook", async (events) => (sent.push(...events), { refused: [] }), new MemoryStorage());
    queue.forSession("practice").record(event);
    await queue.flush();
    queue.dispose();
    expect(sent[0].sessionId).toBe("practice");
  });

  it("copes with storage that's corrupt", () => {
    const storage = new MemoryStorage();
    storage.setItem("q", "{not json");
    const queue = new LearningQueue("q", () => "s", async () => ({ refused: [] }), storage);
    expect(queue.pending).toBe(0);
  });
});

describe("local accounts", () => {
  it("keeps the session across a reload and forgets it on signing out", async () => {
    const storage = new MemoryStorage();
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ userId: "u1", accessToken: "t1" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const auth = new LocalAuth(storage);
    const { user, confirmEmail } = await auth.signUp("a@example.com", "long enough");
    expect(user).toEqual({ id: "u1", email: "a@example.com" });
    expect(confirmEmail).toBe(false);

    const again = new LocalAuth(storage);
    expect(await again.restore()).toEqual({ id: "u1", email: "a@example.com" });
    expect(await again.accessToken()).toBe("t1");
    await again.signOut();
    expect(await again.restore()).toBeNull();
    expect(await again.accessToken()).toBeNull();
    vi.unstubAllGlobals();
  });

  it("says what was wrong, and doesn't pretend to reset a password by email", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "There's already an account with that email." }), { status: 400 })));
    const auth = new LocalAuth(new MemoryStorage());
    await expect(auth.signUp("a@example.com", "long enough")).rejects.toThrow("already an account");
    await expect(auth.requestPasswordReset()).rejects.toBeInstanceOf(AuthProblem);
    vi.unstubAllGlobals();
  });
});

// Against a stand-in for Supabase's client. Whether a real project behaves this way isn't shown here.
describe("Supabase accounts", () => {
  const session = { access_token: "jwt", user: { id: "u1", email: "a@example.com" } };

  const fake = (overrides: Record<string, unknown> = {}) =>
    ({
      getSession: async () => ({ data: { session }, error: null }),
      signUp: async () => ({ data: { user: session.user, session }, error: null }),
      signInWithPassword: async () => ({ data: { user: session.user }, error: null }),
      signOut: async () => ({ error: null }),
      resetPasswordForEmail: async () => ({ error: null }),
      updateUser: async () => ({ error: null }),
      onAuthStateChange: () => ({ data: { subscription: { unsubscribe() {} } } }),
      ...overrides,
    }) as never;

  it("restores a session and gives its token", async () => {
    const auth = new SupabaseAuth(fake());
    expect(await auth.restore()).toEqual({ id: "u1", email: "a@example.com" });
    expect(await auth.accessToken()).toBe("jwt");
  });

  it("waits for email confirmation when there's no session yet", async () => {
    const auth = new SupabaseAuth(fake({ signUp: async () => ({ data: { user: { id: "u1" }, session: null }, error: null }) }));
    expect(await auth.signUp("a@example.com", "long enough")).toEqual({ user: null, confirmEmail: true });
  });

  it("passes on what went wrong", async () => {
    const auth = new SupabaseAuth(fake({ signInWithPassword: async () => ({ data: { user: null }, error: { message: "Invalid login credentials" } }) }));
    await expect(auth.signIn("a@example.com", "wrong")).rejects.toThrow("Invalid login credentials");
  });

  it("tells the app when someone has come back to reset their password", () => {
    let callback: (event: string) => void = () => {};
    const auth = new SupabaseAuth(
      fake({
        onAuthStateChange: (listener: (event: string) => void) => {
          callback = listener;
          return { data: { subscription: { unsubscribe() {} } } };
        },
      }),
    );
    const heard = vi.fn();
    auth.onRecovery(heard);
    callback("SIGNED_IN");
    expect(heard).not.toHaveBeenCalled();
    callback("PASSWORD_RECOVERY");
    expect(heard).toHaveBeenCalledOnce();
  });
});

// The real client library, with only the network replaced by what its API documents.
describe("the real Supabase client", () => {
  const SESSION = {
    access_token: "header.payload.signature",
    token_type: "bearer",
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    refresh_token: "refresh",
    user: { id: "user-1", aud: "authenticated", role: "authenticated", email: "a@example.com", app_metadata: {}, user_metadata: {}, created_at: "2026-10-31T00:00:00Z" },
  };
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  const CONFIG = { mode: "supabase" as const, supabaseUrl: "https://project.supabase.co", supabaseAnonKey: "public-key" };

  it("works through this interface: sign up, restore, token, sign out, reset", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url.replace(CONFIG.supabaseUrl, ""));
      if (url.includes("/auth/v1/signup")) return json(SESSION);
      if (url.includes("/auth/v1/token")) return json(SESSION);
      if (url.includes("/auth/v1/logout")) return new Response(null, { status: 204 });
      if (url.includes("/auth/v1/recover")) return json({});
      return json({});
    });
    const { createAuthClient } = await import("@/lib/auth");
    const auth = (await createAuthClient(CONFIG))!;
    expect(auth.mode).toBe("supabase");

    const { user, confirmEmail } = await auth.signUp("a@example.com", "long enough");
    expect(user).toEqual({ id: "user-1", email: "a@example.com" });
    expect(confirmEmail).toBe(false);
    expect(await auth.accessToken()).toBe(SESSION.access_token);
    expect(await auth.restore()).toEqual({ id: "user-1", email: "a@example.com" });

    await auth.requestPasswordReset("a@example.com");
    await auth.signOut();
    expect(await auth.accessToken()).toBeNull();
    expect(calls.some((call) => call.startsWith("/auth/v1/signup"))).toBe(true);
    expect(calls.some((call) => call.startsWith("/auth/v1/recover"))).toBe(true);
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("reports a refused sign-in in the provider's words", async () => {
    vi.stubGlobal("fetch", async () => json({ error_code: "invalid_credentials", msg: "Invalid login credentials" }, 400));
    const { createAuthClient } = await import("@/lib/auth");
    const auth = (await createAuthClient(CONFIG))!;
    await expect(auth.signIn("a@example.com", "wrong")).rejects.toThrow("Invalid login credentials");
    vi.unstubAllGlobals();
    localStorage.clear();
  });
});
