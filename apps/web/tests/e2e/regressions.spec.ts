// Bugs found by independent review, each reproduced with real pointer events in a browser.
// Pen and touch come through Chrome's input protocol, so these exercise the same PointerEvent
// paths as a stylus. They are not a substitute for testing on a physical iPad.
import { resolve } from "node:path";
import { expect, test, type CDPSession, type Page } from "@playwright/test";
import { bubble, duck, expectDuck, first, line, open, quacksPlayed, reading, script, settle, spyOnQuacks, stats, strokeCount, write } from "./helpers";

const WRONG = reading(first("2(x + 3)"), line("= 2x + 3"));
const RIGHT = reading(first("2(x + 3)"), line("= 2x + 6"));
const SECOND = reading(...WRONG.lines, first("3(x + 1)"), line("= 3x + 1"));

type TouchPoint = { x: number; y: number; id: number };

const touch = (cdp: CDPSession, type: "touchStart" | "touchMove" | "touchEnd" | "touchCancel", touchPoints: TouchPoint[]) =>
  cdp.send("Input.dispatchTouchEvent", { type, touchPoints });

const pen = (cdp: CDPSession, type: "mousePressed" | "mouseMoved" | "mouseReleased", x: number, y: number) =>
  cdp.send("Input.dispatchMouseEvent", { type, x, y, button: "left", buttons: type === "mouseReleased" ? 0 : 1, clickCount: 1, pointerType: "pen", force: 0.5 });

async function penStroke(cdp: CDPSession, x: number, y: number, length = 80) {
  await pen(cdp, "mousePressed", x, y);
  for (let i = 1; i <= 8; i++) await pen(cdp, "mouseMoved", x + (length * i) / 8, y + i);
  await pen(cdp, "mouseReleased", x + length, y + 8);
}

const savedStrokes = (page: Page) =>
  page.evaluate(
    () =>
      new Promise<number>((resolve) => {
        const request = indexedDB.open("quack");
        request.onsuccess = () => {
          const get = request.result.transaction("notes").objectStore("notes").get("notebook");
          get.onsuccess = () => resolve(get.result?.strokes?.length ?? 0);
        };
      }),
  );

test.describe("stylus and palm", () => {
  test.use({ hasTouch: true, viewport: { width: 1024, height: 768 } });

  async function setup(page: Page) {
    await open(page);
    const cdp = await page.context().newCDPSession(page);
    const box = (await page.getByTestId("ink").boundingBox())!;
    return { cdp, box };
  }

  test("a palm before the first pen stroke leaves no mark and doesn't swallow the stroke", async ({ page }) => {
    const { cdp, box } = await setup(page);
    const palm = { x: box.x + 600, y: box.y + 500, id: 7 };
    await touch(cdp, "touchStart", [palm]);
    await touch(cdp, "touchMove", [{ ...palm, x: palm.x + 6, y: palm.y + 4 }]);
    await penStroke(cdp, box.x + 100, box.y + 100);
    await touch(cdp, "touchEnd", []);
    await settle(page, 100);
    expect(await strokeCount(page)).toBe(1);
  });

  test("a palm landing mid-stroke neither deletes the stroke nor scrolls the page", async ({ page }) => {
    const { cdp, box } = await setup(page);
    await penStroke(cdp, box.x + 100, box.y + 100);
    await penStroke(cdp, box.x + 100, box.y + 600);
    await settle(page, 100);
    await page.getByTestId("notebook").evaluate((area) => {
      area.scrollTop = 200;
    });
    await settle(page, 100);

    const x = box.x + 100;
    const y = box.y + 300;
    await pen(cdp, "mousePressed", x, y);
    await pen(cdp, "mouseMoved", x + 20, y);
    const palm = { x: box.x + 600, y: box.y + 500, id: 7 };
    await touch(cdp, "touchStart", [palm]);
    await touch(cdp, "touchMove", [{ ...palm, y: palm.y - 60 }]);
    await pen(cdp, "mouseMoved", x + 60, y);
    await pen(cdp, "mouseReleased", x + 80, y);
    await touch(cdp, "touchEnd", []);
    await settle(page, 100);

    expect(await strokeCount(page)).toBe(3);
    expect(await page.getByTestId("notebook").evaluate((area) => area.scrollTop)).toBe(200);
  });

  test("once a pen has been used, a finger scrolls, even after a reload", async ({ page }) => {
    const { cdp, box } = await setup(page);
    await penStroke(cdp, box.x + 100, box.y + 100);
    await penStroke(cdp, box.x + 100, box.y + 600);
    await settle(page, 700);
    await page.reload();
    await expect.poll(() => strokeCount(page)).toBe(2);
    const fresh = await page.context().newCDPSession(page);
    const start = { x: box.x + 300, y: box.y + 500, id: 1 };
    await touch(fresh, "touchStart", [start]);
    await touch(fresh, "touchMove", [{ ...start, y: start.y - 100 }]);
    await touch(fresh, "touchEnd", []);
    await settle(page, 100);
    expect(await strokeCount(page)).toBe(2);
    expect(await page.getByTestId("notebook").evaluate((area) => area.scrollTop)).toBeGreaterThan(50);
  });

  test("a cancelled finger stroke is dropped", async ({ page }) => {
    const { cdp, box } = await setup(page);
    const finger = { x: box.x + 300, y: box.y + 300, id: 1 };
    await touch(cdp, "touchStart", [finger]);
    await touch(cdp, "touchMove", [{ ...finger, x: finger.x + 40 }]);
    await touch(cdp, "touchCancel", []);
    await settle(page, 100);
    expect(await strokeCount(page)).toBe(0);
  });

  test("two-finger scrolling with the eraser selected doesn't erase", async ({ page }) => {
    const { cdp, box } = await setup(page);
    const finger = { x: box.x + 300, y: box.y + 300, id: 1 };
    await touch(cdp, "touchStart", [finger]);
    await touch(cdp, "touchMove", [{ ...finger, x: finger.x + 80, y: finger.y + 4 }]);
    await touch(cdp, "touchEnd", []);
    await settle(page, 100);
    expect(await strokeCount(page)).toBe(1);
    await page.getByTestId("tool-eraser").click();
    const a = { x: box.x + 340, y: box.y + 302, id: 1 };
    await touch(cdp, "touchStart", [a]);
    await touch(cdp, "touchStart", [a, { x: box.x + 500, y: box.y + 300, id: 2 }]);
    await touch(cdp, "touchMove", [{ ...a, y: a.y - 30 }, { x: box.x + 500, y: box.y + 270, id: 2 }]);
    await touch(cdp, "touchEnd", []);
    await settle(page, 100);
    expect(await strokeCount(page)).toBe(1);
  });
});

test.describe("mistakes on the page", () => {
  test.beforeEach(async ({ page }) => {
    await open(page);
  });

  test("a mistake left on the page doesn't silence a later one, and they don't take turns", async ({ page }) => {
    await spyOnQuacks(page);
    await page.reload();
    await page.mouse.click(5, 300);
    await script({ reading: WRONG });
    await write(page, "2(x+3)", 80, 60);
    await expectDuck(page, "alert");
    await page.getByTestId("dismiss-button").click();
    expect(await quacksPlayed(page)).toBe(1);

    await script({ reading: SECOND });
    await write(page, "3(x+1)", 80, 400);
    await expectDuck(page, "alert");
    expect(await quacksPlayed(page)).toBe(2);

    await write(page, "1", 80, 500);
    await settle(page, 1200);
    await write(page, "1", 120, 500);
    await settle(page, 1200);
    expect(await quacksPlayed(page)).toBe(2);
  });

  test("a reload doesn't quack again about a mistake already reported", async ({ page }) => {
    await script({ reading: WRONG });
    await write(page, "2(x+3)", 80, 60);
    await expectDuck(page, "alert");
    await page.getByTestId("dismiss-button").click();
    await settle(page, 700);

    await spyOnQuacks(page);
    await page.reload();
    await expect.poll(() => strokeCount(page)).toBeGreaterThan(0);
    await page.mouse.click(5, 300);
    await write(page, "1", 80, 130);
    await settle(page, 1200);
    expect((await stats()).readingRequests).toBeGreaterThan(1);
    expect(await quacksPlayed(page)).toBe(0);
    await expect(bubble(page)).toBeHidden();
  });

  test("an alert still on screen comes back after a reload, without a second quack", async ({ page }) => {
    await script({ reading: WRONG });
    await write(page, "2(x+3)", 80, 60);
    await expectDuck(page, "alert");
    await settle(page, 700);

    await spyOnQuacks(page);
    await page.reload();
    await expectDuck(page, "alert");
    expect(await quacksPlayed(page)).toBe(0);
  });

  test("writing checked only after a reload still gets its quack", async ({ page }) => {
    await script({ reading: WRONG });
    await write(page, "2(x+3)", 80, 60);
    await settle(page, 500);
    await page.reload();
    await expectDuck(page, "alert");
  });

  test("more changed lines than one crop holds are checked in turn, always with the known mistake", async ({ page }) => {
    await script({ reading: WRONG });
    await write(page, "2(x+3)", 80, 40);
    await expectDuck(page, "alert");
    await page.getByTestId("dismiss-button").click();
    await settle(page, 300);

    await page.getByTestId("monitoring").click();
    for (let row = 0; row < 16; row++) await write(page, "1", 80 + (row % 6) * 40, 130 + row * 36, 22);

    const checks: { acknowledgedMistakeIds: string[]; outOfViewMistakeIds: string[] }[] = [];
    page.on("request", (request) => {
      if (request.url().endsWith("/api/analyze")) checks.push(request.postDataJSON());
    });
    await page.getByTestId("monitoring").click();
    await expect.poll(() => checks.length, { timeout: 15000 }).toBeGreaterThan(1);
    for (const check of checks) {
      expect(check.acknowledgedMistakeIds).toHaveLength(1);
      expect(check.outOfViewMistakeIds).toEqual([]);
    }
    await expect(bubble(page)).toBeHidden();
  });

  test("after the network comes back, the writing that failed is checked and the alert arrives", async ({ page }) => {
    await script({ reading: WRONG });
    await page.route("**/api/analyze", (route) => route.abort());
    await write(page, "2(x+3)", 80, 60);
    await expect(page.getByTestId("issue")).toBeVisible();
    await expectDuck(page, "idle");

    await page.unroute("**/api/analyze");
    await write(page, "1", 80, 130);
    await expectDuck(page, "alert");
    await expect(page.getByTestId("issue")).toBeHidden();
  });

  test("a hint that fails because the mistake was just fixed isn't reported as a failure", async ({ page }) => {
    await script({ reading: WRONG });
    await write(page, "2(x+3)", 80, 60);
    await expectDuck(page, "alert");

    let release = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route("**/api/hint", async (route) => {
      await held;
      await route.fulfill({ status: 404, json: { detail: "gone" } });
    });
    await duck(page).click();
    await script({ reading: RIGHT });
    await write(page, "1", 80, 130);
    await expectDuck(page, "resolved");
    release();
    await settle(page, 400);
    await expect(page.getByTestId("issue")).toBeHidden();
  });

  test("muting while a check is in flight silences the quack it brings back", async ({ page }) => {
    await spyOnQuacks(page);
    await page.reload();
    await page.mouse.click(5, 300);
    await script({ reading: WRONG });
    let release = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route("**/api/analyze", async (route) => {
      await held;
      await route.continue();
    });
    await write(page, "2(x+3)", 80, 60);
    await settle(page, 1200);
    await page.getByTestId("sound").click();
    release();
    await expectDuck(page, "alert");
    expect(await quacksPlayed(page)).toBe(0);
  });

  test("clearing and then undoing checks the restored writing again", async ({ page }) => {
    await script({ reading: WRONG });
    await write(page, "2(x+3)", 80, 60);
    await expectDuck(page, "alert");
    await page.getByTestId("clear").click();
    await page.getByTestId("clear-confirm").click();
    await expectDuck(page, "idle");
    await expect(page.getByTestId("mistake-count")).toHaveCount(0);
    const before = (await stats()).readingRequests;
    await page.getByTestId("undo").click();
    await expectDuck(page, "alert");
    expect((await stats()).readingRequests).toBeGreaterThan(before);
  });

  test("erasing the mistaken lines clears the alert, with other writing still on the page", async ({ page }) => {
    await script({ reading: reading(first("9")) });
    await write(page, "9", 80, 500);
    await expect.poll(async () => (await stats()).readingRequests).toBeGreaterThan(0);
    await settle(page, 300);
    await script({ reading: WRONG });
    await write(page, "2(x+3)", 80, 60);
    await expectDuck(page, "alert");
    await page.getByTestId("tool-eraser").click();
    const box = (await page.getByTestId("ink").boundingBox())!;
    await page.mouse.move(box.x + 60, box.y + 40);
    await page.mouse.down();
    for (let y = 40; y <= 110; y += 12) await page.mouse.move(box.x + 60, box.y + y);
    for (let x = 60; x <= 320; x += 10) {
      await page.mouse.move(box.x + x, box.y + 75);
      await page.mouse.move(box.x + x, box.y + 92);
    }
    await page.mouse.up();
    await expect(page.getByTestId("mistake-count")).toHaveCount(0);
    await expectDuck(page, "idle");
    expect(await strokeCount(page)).toBe(1);
  });

  test("a correction is recognised with unsupported notation nearby", async ({ page }) => {
    await script({ reading: WRONG });
    await write(page, "2(x+3)", 80, 60);
    await expectDuck(page, "alert");
    await script({ reading: reading(...RIGHT.lines, first("∫x dx")) });
    await write(page, "6", 300, 130);
    await expectDuck(page, "resolved");
  });

  test("a pen tap during the wait doesn't lose the check", async ({ page }) => {
    await script({ reading: WRONG });
    await write(page, "2(x+3)", 80, 60);
    await page.getByTestId("tool-eraser").click();
    const box = (await page.getByTestId("ink").boundingBox())!;
    await page.mouse.click(box.x + 700, box.y + 600);
    await expectDuck(page, "alert");
  });

  test("turning monitoring back on checks what was written meanwhile", async ({ page }) => {
    await script({ reading: WRONG });
    await page.getByTestId("monitoring").click();
    await write(page, "2(x+3)", 80, 60);
    await settle(page, 800);
    expect((await stats()).readingRequests).toBe(0);
    await page.getByTestId("monitoring").click();
    await expectDuck(page, "alert");
  });

  test("a failed manual check keeps the alert", async ({ page }) => {
    await script({ reading: WRONG });
    await write(page, "2(x+3)", 80, 60);
    await expectDuck(page, "alert");
    await page.route("**/api/analyze", (route) => route.abort());
    await page.getByTestId("check").click();
    await expect(page.getByTestId("issue")).toContainText("can't reach");
    await expect(bubble(page)).toHaveText(/Quack!/);
    await expect(page.getByTestId("hint-button")).toBeVisible();
  });

  test("the hint panel's close button stays reachable with long help", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 500 });
    await script({ reading: WRONG, help: ["Look at the bracket. ".repeat(4), "Both terms. It becomes 2x + 6, not 2x + 3. ", "Both terms. It becomes 2x + 6, not 2x + 3.", "Both terms. It becomes 2x + 6, not 2x + 3."] });
    await write(page, "2(x+3)", 80, 60);
    await page.getByTestId("hint-button").click();
    await expect(page.getByTestId("help-level-1")).toBeVisible();
    for (let i = 0; i < 3; i++) {
      await page.getByTestId("question").fill("Why?");
      await page.getByTestId("question").press("Enter");
      await expect(page.getByTestId("help-level-3").nth(i)).toBeVisible();
    }
    await expect(page.getByTestId("panel-close")).toBeInViewport({ ratio: 1 });
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("hint-panel")).toBeHidden();
  });
});

test.describe("saved notes", () => {
  test.beforeEach(async ({ page }) => {
    await open(page);
  });

  test("a stroke drawn just before closing the page is saved", async ({ page }) => {
    await write(page, "1", 80, 80);
    await page.evaluate(() => window.dispatchEvent(new Event("pagehide")));
    await settle(page, 50);
    expect(await savedStrokes(page)).toBe(1);
  });

  test("the app works over plain HTTP without crypto.randomUUID", async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(Crypto.prototype, "randomUUID", { value: undefined, configurable: true });
    });
    await page.reload();
    await script({ reading: WRONG });
    await write(page, "2(x+3)", 80, 60);
    await expectDuck(page, "alert");
    await duck(page).click();
    await expect(page.getByTestId("hint-panel")).toBeVisible();
  });

  test("a reload never lands in a state where every check silently goes stale", async ({ page }) => {
    await script({ reading: RIGHT });
    for (let i = 0; i < 3; i++) await write(page, "1", 80 + i * 30, 60);
    await settle(page, 800);
    await page.evaluate(() => new Promise<void>((resolve) => {
      const request = indexedDB.deleteDatabase("quack");
      request.onsuccess = request.onblocked = () => resolve();
    }));
    await page.reload();
    await write(page, "7", 80, 60);
    await page.getByTestId("check").click();
    await expect(bubble(page)).toContainText("Looks right");
  });
});

test("a second tab holding old notes doesn't overwrite newer work, and says so", async ({ page, context }) => {
  await open(page);
  await write(page, "1", 80, 60);
  await settle(page, 700);
  const other = await context.newPage();
  await open(other);
  await expect.poll(() => strokeCount(other)).toBeGreaterThan(0);

  await write(page, "2", 80, 160);
  await settle(page, 700);
  await write(other, "3", 80, 260);
  await expect(other.getByText("changed in another tab")).toBeVisible();

  await page.reload();
  await expect.poll(() => strokeCount(page)).toBeGreaterThan(0);
  const saved = await strokeCount(page);
  await other.close();
  expect(saved).toBeGreaterThan(1);
});

test.describe("importing an image", () => {
  test.beforeEach(async ({ page }) => {
    await open(page);
  });

  // Any real image will do: the fake model decides what it "reads".
  const file = resolve(__dirname, "../../../../evals/dataset/synthetic/images/demo_correct.png");

  test("a correct imported page leaves the notebook's mistake alone", async ({ page }) => {
    await script({ reading: WRONG, help: ["Look at the bracket."] });
    await write(page, "2(x+3)", 80, 60);
    await expectDuck(page, "alert");
    await script({ reading: RIGHT, help: ["Look at the bracket."] });
    await page.getByTestId("import-input").setInputFiles(file);
    await expect(page.getByTestId("import-preview")).toBeVisible();
    await settle(page, 500);
    await duck(page).click();
    await expect(page.getByTestId("help-level-1")).toBeVisible();
    await page.getByTestId("import-close").click();
    await expect(page.getByTestId("import-preview")).toBeHidden();
  });

  test("a mistake on an imported page can get a hint, and importing works after a reload", async ({ page }) => {
    await page.reload();
    await script({ reading: WRONG, help: ["Look at the bracket."] });
    await page.getByTestId("import-input").setInputFiles(file);
    await expectDuck(page, "alert");
    await duck(page).click();
    await expect(page.getByTestId("help-level-1")).toBeVisible();
  });

  test("a corrupt file gives a message rather than an error", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.getByTestId("import-input").setInputFiles({ name: "bad.png", mimeType: "image/png", buffer: Buffer.from("nope") });
    await expect(bubble(page)).toContainText("couldn't open");
    expect(errors).toEqual([]);
  });
});

test("collection: undo can't carry the last sample onto the next prompt", async ({ page }) => {
  await open(page);
  await page.goto("/collect");
  await page.getByTestId("contributor").fill("tester");
  await page.getByTestId("consent").check();
  await page.getByTestId("start").click();
  await write(page, "2x", 60, 60);
  await page.getByTestId("next").click();
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(page.getByTestId("next")).toBeDisabled();
});

test("a microphone released during the permission prompt doesn't stay on", async ({ page, context }) => {
  await context.grantPermissions(["microphone"]);
  await page.addInitScript(() => {
    const state = { stopped: 0, started: 0 };
    (window as unknown as { mic: typeof state }).mic = state;
    Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
      value: async () => {
        await new Promise((resolve) => setTimeout(resolve, 400));
        const track = { stop: () => (state.stopped += 1) };
        state.started += 1;
        return { getTracks: () => [track] };
      },
    });
  });
  await open(page);
  const talk = page.getByTestId("talk-button");
  const box = (await talk.boundingBox())!;
  await page.mouse.move(box.x + 22, box.y + 22);
  await page.mouse.down();
  await page.mouse.up();
  await page.waitForTimeout(700);
  const mic = await page.evaluate(() => (window as unknown as { mic: { stopped: number; started: number } }).mic);
  expect(mic.started).toBe(1);
  expect(mic.stopped).toBe(1);
  await expect(talk).toHaveAttribute("aria-pressed", "false");
});
