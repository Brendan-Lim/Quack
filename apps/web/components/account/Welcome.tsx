"use client";

import { useState, type FormEvent } from "react";
import { AuthProblem, type AuthClient, type AuthUser } from "@/lib/auth";
import { Duck } from "../duck/Duck";

type Mode = "start" | "create" | "sign_in" | "reset";

const PRIMARY = "btn btn-primary btn-lg w-full";
const SECONDARY = "btn btn-secondary btn-lg w-full";
const FIELD = "min-h-12 rounded-[var(--radius-control)] bg-surface px-3.5 py-2 text-base font-normal ring-1 ring-ink/15 transition-shadow focus:ring-[3px] focus:ring-duck-deep focus:outline-none";

const message = (error: unknown) => (error instanceof AuthProblem ? error.message : "That didn't work. Try again in a moment.");

export function LocalAccountNotice() {
  return (
    <p data-testid="local-account-notice" className="rounded-xl bg-duck/25 px-3 py-2 text-[13px] leading-snug text-ink/80">
      Local test account. It&apos;s kept on this computer for development, and isn&apos;t a secure registered account.
    </p>
  );
}

export function Welcome({ auth, notice, onSignedIn }: { auth: AuthClient; notice?: string; onSignedIn: (user: AuthUser) => void }) {
  const [mode, setMode] = useState<Mode>("start");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(notice ?? null);
  const [busy, setBusy] = useState(false);

  const choose = (next: Mode) => {
    setMode(next);
    setProblem(null);
    setInfo(null);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setProblem(null);
    setInfo(null);
    try {
      if (mode === "create") {
        const { user, confirmEmail } = await auth.signUp(email.trim(), password);
        if (user) onSignedIn(user);
        else if (confirmEmail) {
          setMode("sign_in");
          setInfo("Check your email to confirm your account, then sign in here.");
        }
      } else if (mode === "sign_in") {
        onSignedIn(await auth.signIn(email.trim(), password));
      } else {
        await auth.requestPasswordReset(email.trim());
        setMode("sign_in");
        setInfo("If there's an account with that email, a reset link is on its way.");
      }
    } catch (error) {
      setProblem(message(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="flex min-h-dvh flex-col items-center justify-center bg-paper p-6">
      <div className="flex w-full max-w-sm flex-col gap-5">
        <div className="question-in flex flex-col items-center gap-3 text-center">
          <span className="flex size-28 items-center justify-center rounded-full bg-duck-soft"><Duck state="idle" size={84} /></span>
          <h1 className="font-display text-3xl font-bold tracking-tight">Welcome to Quack</h1>
          <p className="text-[15px] leading-snug text-muted">A little study companion that learns how you write and helps you catch mistakes.</p>
        </div>

        {auth.mode === "local" && <LocalAccountNotice />}
        {info && <p role="status" data-testid="welcome-info" className="rounded-xl bg-success/10 px-3 py-2 text-[14px] text-success">{info}</p>}

        {mode === "start" ? (
          <div className="flex flex-col gap-2 text-[15px]">
            <button type="button" data-testid="choose-create" className={PRIMARY} onClick={() => choose("create")}>Create account</button>
            <button type="button" data-testid="choose-sign-in" className={SECONDARY} onClick={() => choose("sign_in")}>Sign in</button>
          </div>
        ) : (
          <form onSubmit={(event) => void submit(event)} className="flex flex-col gap-3 text-[15px]">
            <label className="flex flex-col gap-1 text-sm font-medium">
              Email
              <input data-testid="email" type="email" required autoComplete="email" autoCapitalize="none" value={email} onChange={(event) => setEmail(event.target.value)} className={FIELD} />
            </label>
            {mode !== "reset" && (
              <label className="flex flex-col gap-1 text-sm font-medium">
                Password
                <input data-testid="password" type="password" required minLength={mode === "create" ? 8 : undefined} autoComplete={mode === "create" ? "new-password" : "current-password"} value={password} onChange={(event) => setPassword(event.target.value)} className={FIELD} />
              </label>
            )}
            {problem && <p role="alert" data-testid="account-problem" className="text-sm text-alert">{problem}</p>}
            <button type="submit" data-testid="account-submit" disabled={busy} className={PRIMARY}>
              {mode === "create" ? "Create account" : mode === "sign_in" ? "Sign in" : "Send reset link"}
            </button>
            <div className="flex flex-wrap justify-between gap-2 text-sm text-muted">
              <button type="button" className="min-h-11 underline-offset-2 hover:underline" onClick={() => choose(mode === "create" ? "sign_in" : "create")}>
                {mode === "create" ? "I already have an account" : "Create an account"}
              </button>
              {mode === "sign_in" && (
                <button type="button" data-testid="choose-reset" className="min-h-11 underline-offset-2 hover:underline" onClick={() => choose("reset")}>Forgot password?</button>
              )}
            </div>
          </form>
        )}
      </div>
    </main>
  );
}

export function SetNewPassword({ auth, onDone }: { auth: AuthClient; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      await auth.updatePassword(password);
      await auth.signOut();
      onDone();
    } catch (error) {
      setProblem(message(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="flex min-h-dvh flex-col items-center justify-center bg-paper p-6">
      <form onSubmit={(event) => void submit(event)} className="flex w-full max-w-sm flex-col gap-3 text-[15px]">
        <h1 className="font-display text-2xl font-bold">Choose a new password</h1>
        <label className="flex flex-col gap-1 text-sm font-medium">
          New password
          <input type="password" required minLength={8} autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} className={FIELD} />
        </label>
        {problem && <p role="alert" className="text-sm text-alert">{problem}</p>}
        <button type="submit" disabled={busy} className={PRIMARY}>Change password</button>
      </form>
    </main>
  );
}
