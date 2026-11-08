// Real browser, real Next proxy, real FastAPI, real verifier, local development accounts.
// Only Meta's API is faked, so none of this says how well a real model reads real handwriting,
// and local accounts aren't Supabase: see docs/AUTH_AND_PROFILES.md for what's unverified there.
import { randomUUID } from "node:crypto";
import { expect, test, type Browser, type Page } from "@playwright/test";
import { bubble, duck, expectDuck, first, line, reading, script, settle, stats, strokeCount, write } from "./helpers";

const PASSWORD = "correct horse battery";
const SAMPLES = ["2 * 3 = 6", "1 ÷ 4 = 1/4", "x^2 + 3x", "d/dx(x^3) = 3x^2", "7x + 1 = 15", "(x + 2)^2"];

const email = () => `student-${randomUUID()}@example.com`;

async function createAccount(page: Page, address = email()): Promise<string> {
  await page.goto("/");
  await page.getByTestId("choose-create").click();
  await expect(page.getByTestId("local-account-notice")).toBeVisible();
  await page.getByTestId("email").fill(address);
  await page.getByTestId("password").fill(PASSWORD);
  await page.getByTestId("account-submit").click();
  await expect(page.getByTestId("calibration-intro")).toBeVisible();
  return address;
}

async function writeSample(page: Page, index: number) {
  await expect(page.getByTestId("calibration-progress")).toHaveAttribute("aria-valuenow", String(index));
  await write(page, SAMPLES[index]);
  await page.getByTestId("calibration-next").click();
}

async function calibrate(page: Page) {
  await page.getByTestId("calibration-start").click();
  for (let index = 0; index < SAMPLES.length; index++) await writeSample(page, index);
  await expect(page.getByTestId("calibration-done")).toBeVisible();
  await page.getByTestId("calibration-finish").click();
  // A first visit asks where to start. These tests start in the notebook.
  await page.getByTestId("choose-notebook").click();
  await expect(page.getByTestId("ink")).toBeVisible();
}

async function signUpAndCalibrate(page: Page): Promise<string> {
  const address = await createAccount(page);
  await calibrate(page);
  await page.evaluate(() => localStorage.setItem("quack.privacy-seen", "1"));
  return address;
}

async function signIn(page: Page, address: string) {
  await page.goto("/");
  await page.getByTestId("choose-sign-in").click();
  await page.getByTestId("email").fill(address);
  await page.getByTestId("password").fill(PASSWORD);
  await page.getByTestId("account-submit").click();
}

async function signOut(page: Page) {
  await page.getByTestId("account-menu").click();
  await page.getByTestId("menu-sign-out").click();
  await expect(page.getByTestId("choose-sign-in")).toBeVisible();
}

const glance = (page: Page, name: string) => page.getByTestId(`glance-${name}`);

test.beforeEach(async () => {
  await script({ reset: true });
});

test.describe("the whole journey in the brief", () => {
  test("register, calibrate, make mistakes, recap, practise, come back the next day", async ({ page, browser }) => {
    test.setTimeout(240_000);
    const address = await signUpAndCalibrate(page);

    // First study session: a wrong sum, a hint, and a correction.
    await script({ reading: reading(first("3 * 4 = 10")) });
    await write(page, "3 * 4 = 10", 80, 80);
    await expectDuck(page, "alert");
    await expect(bubble(page)).toContainText("Quack!");
    await duck(page).click();
    await expect(page.getByTestId("hint-panel")).toBeVisible();

    await script({ reading: reading(first("3 * 4 = 10"), first("3 * 4 = 12")) });
    await write(page, "3 * 4 = 12", 80, 160);
    await expect(bubble(page)).toHaveText("That's it.");

    // A wrong derivative, corrected with no help at all.
    await script({ reading: reading(first("3 * 4 = 10"), first("3 * 4 = 12"), first("d/dx(x^2 + 2x) = 3x + 2")) });
    await write(page, "d/dx(x^2 + 2x) = 3x + 2", 80, 240);
    await expect(bubble(page)).toContainText("Quack!");

    await script({ reading: reading(first("3 * 4 = 10"), first("3 * 4 = 12"), first("d/dx(x^2 + 2x) = 3x + 2"), first("d/dx(x^2 + 2x) = 2x + 2")) });
    await write(page, "d/dx(x^2 + 2x) = 2x + 2", 80, 320);
    await expect(bubble(page)).toHaveText("That's it.");
    const strokesWritten = await strokeCount(page);

    // Finish: the recap counts it all, with the model unreachable to prove it isn't asked.
    await script({ status: 500 });
    const requestsBefore = (await stats()).readingRequests;
    await page.getByTestId("finish-session").click();
    await expect(page.getByTestId("recap")).toBeVisible();
    await expect(glance(page, "found")).toHaveText("2");
    await expect(glance(page, "corrected")).toHaveText("2");
    await expect(glance(page, "unresolved")).toHaveText("0");
    await expect(glance(page, "independent")).toHaveText("1");
    await expect(glance(page, "hints")).toHaveText("1");
    await expect(glance(page, "explanations")).toHaveText("0");
    await expect(page.getByTestId("recap-topics")).toContainText("Multiplication");
    await expect(page.getByTestId("recap-topics")).toContainText("Power rule");
    await expect(page.getByTestId("recommendation")).toContainText("Multiplication");
    expect((await stats()).readingRequests).toBe(requestsBefore);
    await script({ status: 200 });

    // Targeted practice, on the same canvas and duck.
    await page.getByTestId("practice-this").click();
    const prompt = page.getByTestId("practice-prompt");
    await expect(prompt).toHaveText(/^\d+ × \d+ = \?$/);
    const [a, b] = (await prompt.innerText()).match(/\d+/g)!.map(Number);
    const right = a * b;

    await script({ reading: reading(first(String(right + 1))) });
    const practiceInk = page.getByTestId("practice").getByTestId("ink");
    await expect(practiceInk).toBeVisible();
    await write(page, String(right + 1), 80, 80);
    await expect(page.getByTestId("practice").getByTestId("bubble")).toContainText("Quack!");
    await expect(page.getByTestId("practice-correct")).toHaveCount(0);

    await script({ reading: reading(first(String(right))) });
    await page.getByTestId("practice-clear").click();
    await write(page, String(right), 80, 80);
    await expect(page.getByTestId("practice-correct")).toBeVisible();

    await page.getByTestId("practice-end").click();
    await expect(page.getByTestId("summary-attempted")).toHaveText("1");
    await expect(page.getByTestId("summary-correct")).toHaveText("1");
    await expect(page.getByTestId("summary-message")).toContainText("One question doesn't say much");
    await expect(page.getByTestId("summary-message")).not.toContainText(/master/i);
    await page.getByTestId("practice-close").click();

    // The notes are as they were: practice wrote nothing on them.
    expect(await strokeCount(page)).toBe(strokesWritten);

    // The next day, on the same device.
    await page.waitForTimeout(600);
    await signOut(page);
    await signIn(page, address);
    await expect(page.getByTestId("ink")).toBeVisible();
    await expect(page.getByTestId("calibration-intro")).toHaveCount(0);
    await expect.poll(() => strokeCount(page)).toBe(strokesWritten);
    await page.getByTestId("account-menu").click();
    await page.getByTestId("menu-history").click();
    await page.getByTestId("history-item").click();
    await expect(glance(page, "found")).toHaveText("2");
    await expect(page.getByTestId("recap")).toContainText("Practice since then");
    await expect(page.getByTestId("recap")).toContainText("Multiplication: 1 of 1 right");

    // And on a different device: history and calibration follow the account, and notes don't.
    const other = await browser.newContext();
    const tablet = await other.newPage();
    await signIn(tablet, address);
    await expect(tablet.getByTestId("ink")).toBeVisible();
    await expect(tablet.getByTestId("calibration-intro")).toHaveCount(0);
    expect(await strokeCount(tablet)).toBe(0);
    await tablet.getByTestId("account-menu").click();
    await tablet.getByTestId("menu-history").click();
    await expect(tablet.getByTestId("history-item")).toHaveCount(1);
    await other.close();
  });
});

test.describe("registration", () => {
  test("says when an account already exists, and when a password doesn't match", async ({ page }) => {
    const address = await createAccount(page);
    await page.evaluate(() => localStorage.clear());
    await page.goto("/");
    await page.getByTestId("choose-create").click();
    await page.getByTestId("email").fill(address);
    await page.getByTestId("password").fill(PASSWORD);
    await page.getByTestId("account-submit").click();
    await expect(page.getByTestId("account-problem")).toContainText("already an account");

    await page.goto("/");
    await page.getByTestId("choose-sign-in").click();
    await page.getByTestId("email").fill(address);
    await page.getByTestId("password").fill("not the password");
    await page.getByTestId("account-submit").click();
    await expect(page.getByTestId("account-problem")).toContainText("don't match");
  });

  test("a local account can't pretend to reset a password", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("choose-sign-in").click();
    await page.getByTestId("choose-reset").click();
    await page.getByTestId("email").fill("someone@example.com");
    await page.getByTestId("account-submit").click();
    await expect(page.getByTestId("account-problem")).toContainText("no email behind them");
  });

  test("a token that stops working goes back to signing in", async ({ page }) => {
    await signUpAndCalibrate(page);
    await page.evaluate(() => {
      const stored = JSON.parse(localStorage.getItem("quack.local-account")!);
      localStorage.setItem("quack.local-account", JSON.stringify({ ...stored, accessToken: "expired" }));
    });
    await page.reload();
    await expect(page.getByTestId("choose-sign-in")).toBeVisible();
    await expect(page.getByTestId("welcome-info")).toContainText("sign in again");
    await expect(page.getByTestId("ink")).toHaveCount(0);
  });
});

test.describe("calibration", () => {
  test("won't continue with nothing written, and refuses a stray mark", async ({ page }) => {
    await createAccount(page);
    await page.getByTestId("calibration-start").click();
    await expect(page.getByTestId("calibration-next")).toBeDisabled();
    await write(page, "1");
    await expect(page.getByTestId("calibration-next")).toBeEnabled();
    await page.getByTestId("calibration-next").click();
    await expect(page.getByTestId("calibration-problem")).toContainText("unfinished");
    await expect(page.getByTestId("calibration-progress")).toHaveAttribute("aria-valuenow", "0");
    await page.getByTestId("calibration-clear").click();
    await expect(page.getByTestId("calibration-next")).toBeDisabled();
  });

  test("shows the expression, and never judges it", async ({ page }) => {
    await createAccount(page);
    await page.getByTestId("calibration-start").click();
    await expect(page.getByTestId("calibration-prompt")).toHaveText("2 × 3 = 6");
    expect((await stats()).readingRequests).toBe(0);
    // Writing something else isn't Quack's business here, and nothing is sent to be read.
    await write(page, "2 * 3 = 5");
    await page.getByTestId("calibration-next").click();
    await expect(page.getByTestId("calibration-prompt")).toHaveText("1 ÷ 4 = 1/4");
    expect((await stats()).readingRequests).toBe(0);
  });

  test("closing part way through carries on from the last sample saved", async ({ page }) => {
    const address = await createAccount(page);
    await page.getByTestId("calibration-start").click();
    await writeSample(page, 0);
    await writeSample(page, 1);
    await expect(page.getByTestId("calibration-prompt")).toHaveText("x² + 3x");

    await page.reload();
    await expect(page.getByTestId("calibration-prompt")).toHaveText("x² + 3x");
    await expect(page.getByTestId("calibration-progress")).toHaveAttribute("aria-valuenow", "2");

    // Signing out and in again on the way, too.
    await page.evaluate(() => localStorage.removeItem("quack.local-account"));
    await signIn(page, address);
    await expect(page.getByTestId("calibration-prompt")).toHaveText("x² + 3x");
    for (let index = 2; index < SAMPLES.length; index++) await writeSample(page, index);
    await expect(page.getByTestId("calibration-done")).toBeVisible();
  });

  test("once done it never comes back", async ({ page, browser }) => {
    const address = await signUpAndCalibrate(page);
    for (const step of ["refresh", "again"]) {
      await page.reload();
      await expect(page.getByTestId("ink"), step).toBeVisible();
      await expect(page.getByTestId("calibration-intro")).toHaveCount(0);
    }
    // Temporary browser data cleared.
    await page.evaluate(() => {
      const account = localStorage.getItem("quack.local-account")!;
      localStorage.clear();
      localStorage.setItem("quack.local-account", account);
    });
    await page.reload();
    await expect(page.getByTestId("ink")).toBeVisible();
    await signOut(page);
    await signIn(page, address);
    await expect(page.getByTestId("ink")).toBeVisible();
    await expect(page.getByTestId("calibration-intro")).toHaveCount(0);
    const context = await browser.newContext();
    const device = await context.newPage();
    await signIn(device, address);
    await expect(device.getByTestId("ink")).toBeVisible();
    await expect(device.getByTestId("calibration-intro")).toHaveCount(0);
    await context.close();
  });

  test("a server that can't be reached doesn't send a calibrated student back to it", async ({ page }) => {
    await signUpAndCalibrate(page);
    await page.route("**/api/profile", (route) => route.abort());
    await page.reload();
    await expect(page.getByTestId("ink")).toBeVisible();
    await expect(page.getByTestId("calibration-intro")).toHaveCount(0);
  });
});

test.describe("reading with the student's samples", () => {
  test("the first look has none, and looking again at a line that didn't settle does", async ({ page }) => {
    await signUpAndCalibrate(page);
    await script({ reading: reading(line("3 * 4 = 12", { legibility: "unsure", continues_previous: "no" })) });
    await write(page, "3 * 4 = 12", 80, 80);
    await expect.poll(async () => (await stats()).referenceCounts.length, { timeout: 15_000 }).toBeGreaterThanOrEqual(4);
    const { referenceCounts, imagesSent } = await stats();
    expect(referenceCounts.slice(0, 2)).toEqual([0, 0]);
    expect(imagesSent.slice(0, 2)).toEqual([1, 1]);
    expect(referenceCounts.slice(2, 4)).toEqual([2, 2]);
    expect(imagesSent.slice(2, 4)).toEqual([3, 3]);
  });

  test("pressing Check my work brings them in, and a wrong answer is still wrong", async ({ page }) => {
    await signUpAndCalibrate(page);
    await script({ reading: reading(first("2 * 3 = 5")) });
    await write(page, "2 * 3 = 5", 80, 80);
    await expectDuck(page, "alert");
    const before = (await stats()).referenceCounts.length;
    await page.getByTestId("check").click();
    await expect.poll(async () => (await stats()).referenceCounts.length).toBeGreaterThan(before);
    expect((await stats()).referenceCounts.slice(before)).toContain(2);
    await settle(page);
    await expectDuck(page, "alert");
  });
});

test.describe("recaps", () => {
  test("a session with no mistakes doesn't invent anything", async ({ page }) => {
    await signUpAndCalibrate(page);
    await script({ reading: reading(first("2 + 3 = 5")) });
    await write(page, "2 + 3 = 5", 80, 80);
    await settle(page, 1500);
    await page.getByTestId("finish-session").click();
    await expect(glance(page, "found")).toHaveText("0");
    await expect(page.getByTestId("recap-opening")).toContainText("didn't spot anything to fix");
    await expect(page.getByTestId("recap-note")).toContainText("No particular trouble spot stood out");
    await expect(page.getByTestId("recommendation")).toHaveCount(0);
    await expect(page.getByTestId("recap-observations")).toHaveCount(0);
    await page.getByTestId("general-practice").click();
    await expect(page.getByTestId("practice-topic-power_rule")).toBeVisible();
  });

  test("dismissing isn't correcting, and a closed page doesn't lose it", async ({ page }) => {
    await signUpAndCalibrate(page);
    await script({ reading: reading(first("2 * 3 = 5")) });
    await write(page, "2 * 3 = 5", 80, 80);
    await expectDuck(page, "alert");
    await page.keyboard.press("Escape");
    // The browser goes away with no warning, and comes back.
    await page.waitForTimeout(500);
    await page.reload();
    await expect(page.getByTestId("ink")).toBeVisible();
    await page.getByTestId("finish-session").click();
    await expect(glance(page, "found")).toHaveText("1");
    await expect(glance(page, "corrected")).toHaveText("0");
    await expect(glance(page, "unresolved")).toHaveText("1");
  });

  test("events that couldn't be sent are sent when the connection's back, and only once", async ({ page }) => {
    await signUpAndCalibrate(page);
    await page.route("**/api/learning/events", (route) => route.abort());
    await script({ reading: reading(first("2 * 3 = 5")) });
    await write(page, "2 * 3 = 5", 80, 80);
    await expectDuck(page, "alert");
    await page.waitForTimeout(800);
    await page.unroute("**/api/learning/events");
    await page.reload();
    await expect(page.getByTestId("ink")).toBeVisible();
    await page.waitForTimeout(500);
    await page.reload();
    await expect(page.getByTestId("ink")).toBeVisible();
    await page.getByTestId("finish-session").click();
    await expect(glance(page, "found")).toHaveText("1");
  });

  test("a saved recap can be read with no connection", async ({ page }) => {
    await signUpAndCalibrate(page);
    await script({ reading: reading(first("2 * 3 = 5")) });
    await write(page, "2 * 3 = 5", 80, 80);
    await expectDuck(page, "alert");
    await page.waitForTimeout(500);
    await page.getByTestId("finish-session").click();
    await expect(glance(page, "found")).toHaveText("1");
    await page.getByTestId("recap-back").click();

    await page.route("**/api/**", (route) => route.abort());
    await page.getByTestId("account-menu").click();
    await page.getByTestId("menu-history").click();
    await expect(page.getByTestId("history-offline")).toBeVisible();
    await page.getByTestId("history-item").click();
    await expect(glance(page, "found")).toHaveText("1");
  });
});

test.describe("practice", () => {
  test("the answer isn't in anything the browser gets until it's asked for", async ({ page }) => {
    await signUpAndCalibrate(page);
    const bodies: string[] = [];
    page.on("response", async (response) => {
      if (response.url().includes("/api/practice") && !response.url().includes("/reveal")) bodies.push(await response.text().catch(() => ""));
    });
    await page.getByTestId("account-menu").click();
    await page.getByTestId("menu-practice").click();
    const prompt = page.getByTestId("practice-prompt");
    await expect(prompt).toBeVisible();
    await expect.poll(() => bodies.length).toBeGreaterThan(0);
    for (const body of bodies) expect(JSON.parse(body).expectedAnswer).toBeUndefined();
    await page.getByTestId("practice-reveal").click();
    const shown = (await page.getByTestId("practice-answer").innerText()).match(/One answer is (.+)\./)![1];
    for (const body of bodies) expect(body).not.toContain(`"${shown}"`);
  });

  test("a second question gets a new page", async ({ page }) => {
    await signUpAndCalibrate(page);
    await page.getByTestId("account-menu").click();
    await page.getByTestId("menu-practice").click();
    const prompt = page.getByTestId("practice-prompt");
    await expect(prompt).toBeVisible();
    // With no mistakes yet, it starts with adding and taking away.
    const [, a, sign, b] = (await prompt.innerText()).match(/(\d+)\s*([+−-])\s*(\d+)/)!;
    const answer = String(sign === "+" ? Number(a) + Number(b) : Number(a) - Number(b));
    await script({ reading: reading(first(answer)) });
    await write(page, answer, 80, 80);
    await expect(page.getByTestId("practice-correct")).toBeVisible();
    const before = await prompt.innerText();
    await page.getByTestId("practice-next").click();
    await expect(prompt).not.toHaveText(before);
    await expect(prompt).toHaveAttribute("data-difficulty", "intermediate");
    await expect(page.getByTestId("practice-correct")).toHaveCount(0);
    expect(await page.getByTestId("practice").getByTestId("notebook").getAttribute("data-strokes")).toBe("0");
  });
});

test.describe("accounts stay apart", () => {
  test("notes and history on a shared device belong to whoever's signed in", async ({ page }) => {
    await signUpAndCalibrate(page);
    await script({ reading: reading(first("2 * 3 = 5")) });
    await write(page, "2 * 3 = 5", 80, 80);
    await expectDuck(page, "alert");
    await page.waitForTimeout(600);
    const written = await strokeCount(page);
    await page.getByTestId("finish-session").click();
    await expect(glance(page, "found")).toHaveText("1");
    await page.getByTestId("recap-back").click();
    await signOut(page);

    await page.getByTestId("choose-create").click();
    await page.getByTestId("email").fill(email());
    await page.getByTestId("password").fill(PASSWORD);
    await page.getByTestId("account-submit").click();
    await calibrate(page);
    expect(await strokeCount(page)).toBe(0);
    await page.getByTestId("account-menu").click();
    await page.getByTestId("menu-history").click();
    await expect(page.getByTestId("history-empty")).toBeVisible();
    expect(written).toBeGreaterThan(0);
  });
});

test.describe("notes from before accounts", () => {
  async function seedOldNotes(page: Page) {
    await page.goto("/");
    await page.evaluate(
      () =>
        new Promise<void>((done, failed) => {
          const open = indexedDB.open("quack");
          open.onupgradeneeded = () => open.result.createObjectStore("notes");
          open.onerror = () => failed(open.error);
          open.onsuccess = () => {
            const stroke = { id: "old", points: [{ x: 10, y: 10, timestamp: 1 }, { x: 80, y: 10, timestamp: 2 }], color: "#262626", width: 3, createdAt: 1 };
            const write = open.result.transaction("notes", "readwrite").objectStore("notes").put({ strokes: [stroke], version: 1 }, "notebook");
            write.onsuccess = () => {
              open.result.close();
              done();
            };
          };
        }),
    );
  }

  const hasOldNotes = (page: Page) =>
    page.evaluate(
      () =>
        new Promise<boolean>((done) => {
          const open = indexedDB.open("quack");
          open.onsuccess = () => {
            const get = open.result.transaction("notes").objectStore("notes").get("notebook");
            get.onsuccess = () => {
              open.result.close();
              done(Boolean(get.result));
            };
          };
        }),
    );

  async function signUpWithOldNotes(page: Page) {
    await seedOldNotes(page);
    await page.reload();
    await page.getByTestId("choose-create").click();
    await page.getByTestId("email").fill(email());
    await page.getByTestId("password").fill(PASSWORD);
    await page.getByTestId("account-submit").click();
    await page.getByTestId("calibration-start").click();
    for (let index = 0; index < SAMPLES.length; index++) await writeSample(page, index);
    await page.getByTestId("calibration-finish").click();
  }

  test("are brought in only when asked", async ({ page }) => {
    await signUpWithOldNotes(page);
    await page.getByTestId("bring-in-notes").click();
    await expect(page.getByTestId("ink")).toBeVisible();
    await expect.poll(() => strokeCount(page)).toBe(1);
    expect(await hasOldNotes(page)).toBe(false);
  });

  test("stay where they are if not", async ({ page }) => {
    await signUpWithOldNotes(page);
    await page.getByTestId("leave-notes").click();
    // Their old notes stayed behind, so this account is new, and is asked where to start.
    await page.getByTestId("choose-notebook").click();
    await expect(page.getByTestId("ink")).toBeVisible();
    expect(await strokeCount(page)).toBe(0);
    expect(await hasOldNotes(page)).toBe(true);
    // It isn't asked every time, and can still be done from Settings.
    await page.reload();
    await expect(page.getByTestId("ink")).toBeVisible();
    await page.getByTestId("account-menu").click();
    await page.getByTestId("menu-settings").click();
    await expect(page.getByTestId("settings-bring-in-notes")).toBeVisible();
  });
});

test.describe("deleting", () => {
  test("samples go, and aren't asked for again or sent", async ({ page }) => {
    await signUpAndCalibrate(page);
    await page.getByTestId("account-menu").click();
    await page.getByTestId("menu-settings").click();
    await page.getByTestId("delete-samples").click();
    await page.getByTestId("delete-samples-confirm").click();
    await expect(page.getByTestId("samples-status")).toContainText("None saved");
    await page.reload();
    await expect(page.getByTestId("ink")).toBeVisible();
    await expect(page.getByTestId("calibration-intro")).toHaveCount(0);

    await script({ reading: reading(line("1 + 1 = 2", { legibility: "unsure", continues_previous: "no" })) });
    await write(page, "1 + 1 = 2", 80, 80);
    await expect.poll(async () => (await stats()).referenceCounts.length, { timeout: 15_000 }).toBeGreaterThanOrEqual(4);
    expect((await stats()).referenceCounts.every((count) => count === 0)).toBe(true);
  });

  test("learning history can be deleted and then there's nothing", async ({ page }) => {
    await signUpAndCalibrate(page);
    await page.getByTestId("finish-session").click();
    await page.getByTestId("recap-back").click();
    await page.getByTestId("account-menu").click();
    await page.getByTestId("menu-settings").click();
    await page.getByTestId("delete-learning").click();
    await page.getByTestId("delete-learning-confirm").click();
    await expect(page.getByTestId("settings-said")).toContainText("deleted");
    await page.getByTestId("settings-back").click();
    await page.getByTestId("account-menu").click();
    await page.getByTestId("menu-history").click();
    await expect(page.getByTestId("history-empty")).toBeVisible();
  });

  test("an account can be deleted, and can't be signed in to", async ({ page }) => {
    const address = await signUpAndCalibrate(page);
    await page.getByTestId("account-menu").click();
    await page.getByTestId("menu-settings").click();
    await page.getByTestId("delete-account").click();
    await page.getByTestId("delete-account-confirm").click();
    await expect(page.getByTestId("choose-sign-in")).toBeVisible();
    await signIn(page, address);
    await expect(page.getByTestId("account-problem")).toBeVisible();
  });
});

test.describe("tablet layouts", () => {
  const SIZES = [
    { width: 1180, height: 820, name: "iPad landscape" },
    { width: 820, height: 1180, name: "iPad portrait" },
  ];

  async function fitsAcross(page: Page) {
    const over = await page.evaluate(() => [...document.querySelectorAll("[data-testid='recap'], [data-testid='settings'], [data-testid='history'], main")].some((el) => el.scrollWidth > el.clientWidth + 1));
    expect(over).toBe(false);
  }

  for (const size of SIZES) {
    test(`everything fits on ${size.name}`, async ({ browser }: { browser: Browser }) => {
      test.setTimeout(120_000);
      const context = await browser.newContext({ viewport: { width: size.width, height: size.height }, hasTouch: true });
      const page = await context.newPage();
      await createAccount(page);
      await page.screenshot({ path: `test-results/accounts-${size.width}-intro.png` });
      await page.getByTestId("calibration-start").click();
      await expect(page.getByTestId("calibration-prompt")).toBeVisible();
      await write(page, SAMPLES[0]);
      await fitsAcross(page);
      await page.screenshot({ path: `test-results/accounts-${size.width}-calibration.png` });
      await page.getByTestId("calibration-next").click();
      for (let index = 1; index < SAMPLES.length; index++) await writeSample(page, index);
      await page.getByTestId("calibration-finish").click();
      // A first visit asks where to start. These tests start in the notebook.
      await page.getByTestId("choose-notebook").click();
      await page.evaluate(() => localStorage.setItem("quack.privacy-seen", "1"));
      await expect(page.getByTestId("finish-session")).toBeVisible();
      await expect(page.getByTestId("check")).toBeVisible();
      await page.screenshot({ path: `test-results/accounts-${size.width}-notebook.png` });

      await script({ reading: reading(first("3 * 4 = 10")) });
      await write(page, "3 * 4 = 10", 80, 80);
      await expectDuck(page, "alert");
      await page.waitForTimeout(500);
      await page.getByTestId("finish-session").click();
      await expect(page.getByTestId("recap")).toBeVisible();
      await fitsAcross(page);
      await page.screenshot({ path: `test-results/accounts-${size.width}-recap.png` });
      await page.getByTestId("practice-this").click();
      await expect(page.getByTestId("practice-prompt")).toBeVisible();
      await fitsAcross(page);
      await page.screenshot({ path: `test-results/accounts-${size.width}-practice.png` });
      await context.close();
    });
  }
});
