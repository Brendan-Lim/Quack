"use client";

import { useEffect, useState } from "react";
import { deleteAccount, deleteCalibration, deleteLearning, exportCalibration, fetchProfile, forgetCachedLearning, restartCalibration } from "@/lib/account";
import type { AuthClient, AuthUser } from "@/lib/auth";
import { bringInNotes, hasNotesFromBeforeAccounts } from "@/lib/persist";
import type { Profile } from "@/lib/types";
import { LocalAccountNotice } from "./Welcome";

type Props = {
  user: AuthUser;
  auth: AuthClient;
  onBack: () => void;
  onSignOut: () => void;
  onSignedOut: () => void;
  onRedoCalibration: () => void;
};

const CARD = "flex flex-col gap-2 rounded-2xl bg-white p-4 ring-1 ring-ink/5";
const BUTTON = "min-h-11 self-start rounded-xl px-4 text-[14px] font-medium ring-1 ring-ink/15 hover:bg-ink/5";
const DANGER = "min-h-11 self-start rounded-xl bg-alert px-4 text-[14px] font-semibold text-white hover:opacity-90";

function Confirmed({ label, confirmLabel, testId, onConfirm }: { label: string; confirmLabel: string; testId: string; onConfirm: () => void }) {
  const [asking, setAsking] = useState(false);
  if (!asking) return <button type="button" data-testid={testId} className={BUTTON} onClick={() => setAsking(true)}>{label}</button>;
  return (
    <div className="flex flex-wrap gap-2">
      <button type="button" data-testid={`${testId}-confirm`} className={DANGER} onClick={onConfirm}>{confirmLabel}</button>
      <button type="button" className={BUTTON} onClick={() => setAsking(false)}>Keep it</button>
    </div>
  );
}

export function SettingsView({ user, auth, onBack, onSignOut, onSignedOut, onRedoCalibration }: Props) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const [hasEarlierNotes, setHasEarlierNotes] = useState(false);

  useEffect(() => {
    fetchProfile().then(setProfile, () => {});
    void hasNotesFromBeforeAccounts().then(setHasEarlierNotes);
  }, []);

  const attempt = async (work: () => Promise<void>) => {
    setSaid(null);
    try {
      await work();
    } catch {
      setSaid("That didn't work. Check the connection and try again.");
    }
  };

  const download = () =>
    attempt(async () => {
      const bundle = await exportCalibration();
      const url = URL.createObjectURL(new Blob([JSON.stringify(bundle)], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = "quack-handwriting-samples.json";
      link.click();
      URL.revokeObjectURL(url);
    });

  return (
    <div data-testid="settings" className="fixed inset-0 z-40 overflow-y-auto bg-paper">
      <main className="mx-auto flex w-full max-w-2xl flex-col gap-4 p-4 pb-10 sm:p-8">
        <header className="flex items-center justify-between gap-3">
          <h1 className="font-display text-2xl font-bold tracking-tight">Settings</h1>
          <button type="button" data-testid="settings-back" onClick={onBack} className={BUTTON}>Back to my notebook</button>
        </header>
        {said && <p role="status" data-testid="settings-said" className="text-[14px] text-muted">{said}</p>}

        <section className={CARD}>
          <h2 className="font-semibold">Account</h2>
          {auth.mode === "local" && <LocalAccountNotice />}
          {user.email && <p className="text-[15px] text-muted">{user.email}</p>}
          <button type="button" className={BUTTON} onClick={onSignOut}>Sign out</button>
        </section>

        <section className={CARD}>
          <h2 className="font-semibold">Your handwriting samples</h2>
          <p data-testid="samples-status" className="text-[14px] leading-snug text-muted">
            {profile?.hasHandwritingSamples
              ? "Saved privately to your account. When Quack can't settle a line, or when you press Check my work, up to two of them go to Meta's AI API along with that line, to show how you write. They're never used for anyone else."
              : "None saved. Quack reads your writing without them."}
          </p>
          <div className="flex flex-wrap gap-2">
            {profile?.hasHandwritingSamples && <button type="button" data-testid="download-samples" className={BUTTON} onClick={() => void download()}>Download them</button>}
            {profile?.hasHandwritingSamples && (
              <Confirmed
                label="Delete them"
                confirmLabel="Delete my samples"
                testId="delete-samples"
                onConfirm={() =>
                  void attempt(async () => {
                    setProfile(await deleteCalibration());
                    setSaid("Your samples have been deleted.");
                  })
                }
              />
            )}
            <button
              type="button"
              data-testid="redo-calibration"
              className={BUTTON}
              onClick={() =>
                void attempt(async () => {
                  await restartCalibration();
                  onRedoCalibration();
                })
              }
            >
              {profile?.hasHandwritingSamples ? "Write them again" : "Write new samples"}
            </button>
          </div>
        </section>

        {hasEarlierNotes && (
          <section className={CARD}>
            <h2 className="font-semibold">Notes from before you had an account</h2>
            <p className="text-[14px] leading-snug text-muted">They&apos;re still on this device, separate from this account.</p>
            <button
              type="button"
              data-testid="settings-bring-in-notes"
              className={BUTTON}
              onClick={() =>
                void bringInNotes(user.id).then((moved) => {
                  if (moved) window.location.reload();
                  else setSaid("This account already has notes on this device, so nothing was moved.");
                })
              }
            >
              Bring them in
            </button>
          </section>
        )}

        <section className={CARD}>
          <h2 className="font-semibold">What Quack keeps</h2>
          <p className="text-[14px] leading-snug text-muted">
            Your notes stay on this device. When you pause, Quack sends a picture of your recent lines to Meta&apos;s AI API to read them, and doesn&apos;t keep the picture. Your account keeps your samples, and the mistakes, hints and practice that make up your recaps.
          </p>
          <Confirmed
            label="Delete my learning history"
            confirmLabel="Delete all my recaps and history"
            testId="delete-learning"
            onConfirm={() =>
              void attempt(async () => {
                await deleteLearning();
                forgetCachedLearning(user.id);
                setSaid("Your learning history has been deleted.");
              })
            }
          />
          <Confirmed
            label="Delete my account"
            confirmLabel="Delete my account and everything in it"
            testId="delete-account"
            onConfirm={() =>
              void attempt(async () => {
                const { signInDeleted } = await deleteAccount();
                forgetCachedLearning(user.id);
                await auth.signOut().catch(() => {});
                if (!signInDeleted) window.alert("Your data has been deleted. Your sign-in itself couldn't be removed from here, so please ask for that to be done.");
                onSignedOut();
              })
            }
          />
        </section>
      </main>
    </div>
  );
}
