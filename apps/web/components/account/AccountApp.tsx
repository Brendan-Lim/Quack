"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { cachedProfile, cacheProfile, fetchProfile } from "@/lib/account";
import { ApiError, setAccount } from "@/lib/api";
import { createAuthClient, loadAuthConfig, type AuthClient, type AuthUser } from "@/lib/auth";
import { rememberChoice, shouldAskWhereToStart, type StartingMode } from "@/lib/firstVisit";
import { hasNotesFromBeforeAccounts } from "@/lib/persist";
import { QuackApp } from "../QuackApp";
import { Duck } from "../duck/Duck";
import { AccountHome } from "./AccountHome";
import { BringInNotes } from "./BringInNotes";
import { CalibrationFlow } from "./CalibrationFlow";
import { ChooseMode } from "./ChooseMode";
import { SetNewPassword, Welcome } from "./Welcome";

type Stage =
  | { name: "loading" }
  // No accounts on this server, so the notebook is all there is.
  | { name: "off" }
  // Accounts are on, and this browser has never been able to find out how to reach them.
  | { name: "unreachable" }
  | { name: "signed_out"; notice?: string }
  | { name: "recovery" }
  | { name: "calibrating"; user: AuthUser; redo?: boolean }
  | { name: "bring_in_notes"; user: AuthUser }
  // A first visit: My Notebook or Learn, side by side, once.
  | { name: "choosing"; user: AuthUser }
  | { name: "ready"; user: AuthUser; start?: StartingMode };

const declinedKey = (userId: string) => `quack.notes-declined.${userId}`;

export function AccountApp() {
  const [stage, setStage] = useState<Stage>({ name: "loading" });
  const [client, setClient] = useState<AuthClient | null>(null);

  // Just calibrated for the first time, in this browser: a new student, who's asked where to start. Signing in,
  // on this device or another, or calibrating again from Settings, never asks.
  const firstCalibration = useRef(false);
  const arrive = useCallback(async (user: AuthUser) => {
    const ask = firstCalibration.current && (await shouldAskWhereToStart(user.id));
    firstCalibration.current = false;
    setStage(ask ? { name: "choosing", user } : { name: "ready", user });
  }, []);

  const enterNotebook = useCallback(async (user: AuthUser) => {
    const declined = localStorage.getItem(declinedKey(user.id)) === "1";
    const offer = !declined && (await hasNotesFromBeforeAccounts());
    if (offer) setStage({ name: "bring_in_notes", user });
    else await arrive(user);
  }, [arrive]);

  const enter = useCallback(
    async (user: AuthUser) => {
      let status = cachedProfile(user.id)?.onboardingStatus;
      try {
        const profile = await fetchProfile();
        cacheProfile(profile);
        status = profile.onboardingStatus;
      } catch (error) {
        // The token's no good, and signing in again is already on its way.
        if (error instanceof ApiError && error.status === 401) return;
        // With no answer and nothing remembered, the notebook still opens. Asking someone to
        // calibrate again because the server's down is the one thing this must never do.
        status ??= "calibration_completed";
      }
      if (status === "calibration_completed") await enterNotebook(user);
      else setStage({ name: "calibrating", user });
    },
    [enterNotebook],
  );

  useEffect(() => {
    let cancelled = false;
    let stopListening = () => {};
    void (async () => {
      const config = await loadAuthConfig();
      if (cancelled) return;
      if (!config) {
        setStage({ name: "unreachable" });
        return;
      }
      const created = await createAuthClient(config);
      if (cancelled) return;
      if (!created) {
        setStage({ name: "off" });
        return;
      }
      setClient(created);
      setAccount(
        () => created.accessToken(),
        () => {
          void created.signOut().catch(() => {});
          setStage({ name: "signed_out", notice: "Please sign in again." });
        },
      );
      let recovering = false;
      stopListening = created.onRecovery(() => {
        recovering = true;
        setStage({ name: "recovery" });
      });
      const user = await created.restore();
      // Coming back from a reset link signs them in, and choosing a password comes first.
      if (cancelled || recovering) return;
      if (user) await enter(user);
      else setStage({ name: "signed_out" });
    })();
    return () => {
      cancelled = true;
      stopListening();
    };
  }, [enter]);

  if (stage.name === "off") return <QuackApp />;
  if (stage.name === "unreachable") {
    return (
      <main className="flex h-dvh flex-col items-center justify-center gap-3 bg-paper p-6 text-center">
        <Duck state="idle" size={72} />
        <p data-testid="unreachable" className="max-w-sm text-[15px] text-muted">Quack can&apos;t reach its server yet. Check the connection and reload this page.</p>
      </main>
    );
  }
  if (stage.name === "loading" || !client) {
    return <main aria-busy="true" className="flex h-dvh items-center justify-center bg-paper"><Duck state="idle" size={72} /></main>;
  }
  if (stage.name === "recovery") {
    return <SetNewPassword auth={client} onDone={() => setStage({ name: "signed_out", notice: "Password changed. Sign in with it now." })} />;
  }
  if (stage.name === "signed_out") return <Welcome auth={client} notice={stage.notice} onSignedIn={enter} />;
  if (stage.name === "calibrating") {
    const { user, redo } = stage;
    return (
      <CalibrationFlow
        onDone={() => {
          firstCalibration.current = !redo;
          void enter(user);
        }}
      />
    );
  }
  if (stage.name === "bring_in_notes") {
    const { user } = stage;
    return (
      <BringInNotes
        userId={user.id}
        onDone={(declined) => {
          if (declined) localStorage.setItem(declinedKey(user.id), "1");
          void arrive(user);
        }}
      />
    );
  }
  if (stage.name === "choosing") {
    const { user } = stage;
    return (
      <ChooseMode
        onChoose={(mode) => {
          rememberChoice(user.id, mode);
          setStage({ name: "ready", user, start: mode });
        }}
      />
    );
  }
  return (
    <AccountHome
      user={stage.user}
      auth={client}
      start={stage.start}
      onSignedOut={() => setStage({ name: "signed_out" })}
      onRedoCalibration={() => setStage({ name: "calibrating", user: stage.user, redo: true })}
    />
  );
}
