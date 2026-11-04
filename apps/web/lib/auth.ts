// Signing in. Supabase holds the passwords and the session, including refreshing it. The local client
// is for development without a project, and the app says so wherever it's in use.

import { ApiError, sendJson } from "./api";
import type { AuthConfig, LocalSession } from "./types";

export type AuthUser = { id: string; email: string | null };
export type SignUpResult = { user: AuthUser | null; confirmEmail: boolean };

export class AuthProblem extends Error {}

export interface AuthClient {
  readonly mode: "local" | "supabase";
  restore(): Promise<AuthUser | null>;
  signUp(email: string, password: string): Promise<SignUpResult>;
  signIn(email: string, password: string): Promise<AuthUser>;
  signOut(): Promise<void>;
  requestPasswordReset(email: string): Promise<void>;
  updatePassword(password: string): Promise<void>;
  accessToken(): Promise<string | null>;
  // Called with `recovery` when someone has come back from a password reset email.
  onRecovery(listener: () => void): () => void;
}

const LOCAL_KEY = "quack.local-account";
type LocalStored = LocalSession & { email: string };

export class LocalAuth implements AuthClient {
  readonly mode = "local" as const;

  constructor(private readonly storage: Pick<Storage, "getItem" | "setItem" | "removeItem"> = localStorage) {}

  private stored(): LocalStored | null {
    try {
      return JSON.parse(this.storage.getItem(LOCAL_KEY) ?? "null") as LocalStored | null;
    } catch {
      return null;
    }
  }

  async restore(): Promise<AuthUser | null> {
    const stored = this.stored();
    return stored && { id: stored.userId, email: stored.email };
  }

  private async enter(path: string, email: string, password: string): Promise<AuthUser> {
    let session: LocalSession;
    try {
      session = await post(path, { email, password });
    } catch (error) {
      throw error instanceof AuthProblem ? error : new AuthProblem("Quack can't reach its server just now. Try again in a moment.");
    }
    this.storage.setItem(LOCAL_KEY, JSON.stringify({ ...session, email }));
    return { id: session.userId, email };
  }

  async signUp(email: string, password: string): Promise<SignUpResult> {
    return { user: await this.enter("/api/auth/local/register", email, password), confirmEmail: false };
  }

  signIn(email: string, password: string): Promise<AuthUser> {
    return this.enter("/api/auth/local/login", email, password);
  }

  async signOut(): Promise<void> {
    this.storage.removeItem(LOCAL_KEY);
  }

  async requestPasswordReset(): Promise<void> {
    throw new AuthProblem("Local test accounts can't reset a password, as there's no email behind them. Create another one.");
  }

  async updatePassword(): Promise<void> {
    throw new AuthProblem("Local test accounts can't change their password.");
  }

  async accessToken(): Promise<string | null> {
    return this.stored()?.accessToken ?? null;
  }

  onRecovery(): () => void {
    return () => {};
  }
}

// `sendJson` throws away the detail of a refusal, and here that's the point of asking.
async function post(path: string, body: unknown): Promise<LocalSession> {
  const response = await fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok) {
    const detail = await response.json().then((json: { detail?: unknown }) => json.detail, () => null);
    throw new AuthProblem(typeof detail === "string" ? detail : "That didn't work. Check the details and try again.");
  }
  return (await response.json()) as LocalSession;
}

type SupabaseAuthApi = {
  getSession(): Promise<{ data: { session: { access_token: string; user: { id: string; email?: string } } | null }; error: unknown }>;
  signUp(input: { email: string; password: string; options?: { emailRedirectTo?: string } }): Promise<{
    data: { user: { id: string; email?: string } | null; session: unknown };
    error: { message: string } | null;
  }>;
  signInWithPassword(input: { email: string; password: string }): Promise<{
    data: { user: { id: string; email?: string } | null };
    error: { message: string } | null;
  }>;
  signOut(): Promise<{ error: { message: string } | null }>;
  resetPasswordForEmail(email: string, options?: { redirectTo?: string }): Promise<{ error: { message: string } | null }>;
  updateUser(input: { password: string }): Promise<{ error: { message: string } | null }>;
  onAuthStateChange(callback: (event: string) => void): { data: { subscription: { unsubscribe(): void } } };
};

export class SupabaseAuth implements AuthClient {
  readonly mode = "supabase" as const;

  constructor(private readonly auth: SupabaseAuthApi) {}

  private origin(): string | undefined {
    return typeof window === "undefined" ? undefined : window.location.origin;
  }

  async restore(): Promise<AuthUser | null> {
    const { data } = await this.auth.getSession();
    const user = data.session?.user;
    return user ? { id: user.id, email: user.email ?? null } : null;
  }

  async signUp(email: string, password: string): Promise<SignUpResult> {
    const { data, error } = await this.auth.signUp({ email, password, options: { emailRedirectTo: this.origin() } });
    if (error) throw new AuthProblem(error.message);
    // With email confirmation on, there's no session until the link has been followed.
    if (!data.session || !data.user) return { user: null, confirmEmail: true };
    return { user: { id: data.user.id, email: data.user.email ?? email }, confirmEmail: false };
  }

  async signIn(email: string, password: string): Promise<AuthUser> {
    const { data, error } = await this.auth.signInWithPassword({ email, password });
    if (error || !data.user) throw new AuthProblem(error?.message ?? "That email and password don't match.");
    return { id: data.user.id, email: data.user.email ?? email };
  }

  async signOut(): Promise<void> {
    const { error } = await this.auth.signOut();
    if (error) throw new AuthProblem(error.message);
  }

  async requestPasswordReset(email: string): Promise<void> {
    const { error } = await this.auth.resetPasswordForEmail(email, { redirectTo: this.origin() });
    if (error) throw new AuthProblem(error.message);
  }

  async updatePassword(password: string): Promise<void> {
    const { error } = await this.auth.updateUser({ password });
    if (error) throw new AuthProblem(error.message);
  }

  async accessToken(): Promise<string | null> {
    // Refreshes it first if it's about to run out.
    const { data } = await this.auth.getSession();
    return data.session?.access_token ?? null;
  }

  onRecovery(listener: () => void): () => void {
    const { data } = this.auth.onAuthStateChange((event) => {
      if (event === "PASSWORD_RECOVERY") listener();
    });
    return () => data.subscription.unsubscribe();
  }
}

const CONFIG_KEY = "quack.auth-config";

// Remembered, so that a server that's down doesn't turn an account's notebook into an anonymous one.
export async function loadAuthConfig(): Promise<AuthConfig | null> {
  try {
    const config = await sendJson<AuthConfig>("GET", "/api/auth/config");
    localStorage.setItem(CONFIG_KEY, JSON.stringify(config));
    return config;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return { mode: "off", supabaseUrl: null, supabaseAnonKey: null };
    try {
      return JSON.parse(localStorage.getItem(CONFIG_KEY) ?? "null") as AuthConfig | null;
    } catch {
      return null;
    }
  }
}

export async function createAuthClient(config: AuthConfig): Promise<AuthClient | null> {
  if (config.mode === "local") return new LocalAuth();
  if (config.mode !== "supabase" || !config.supabaseUrl || !config.supabaseAnonKey) return null;
  const { createClient } = await import("@supabase/supabase-js");
  const client = createClient(config.supabaseUrl, config.supabaseAnonKey, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
  });
  return new SupabaseAuth(client.auth as unknown as SupabaseAuthApi);
}
