// Real browser, real Next proxy, real FastAPI, real verifier. Only Meta's API is faked.
import { expect, test } from "@playwright/test";
import { bubble, duck, expectDuck, first, line, open, quacksPlayed, reading, script, settle, spyOnQuacks, stats, strokeCount, write } from "./helpers";

const WRONG = reading(first("2(x + 3)"), line("= 2x + 3"));
const RIGHT = reading(first("2(x + 3)"), line("= 2x + 6"));
const NEW_MISTAKE = reading(first("2(x + 3)"), line("= 2x + 5"));

test.beforeEach(async ({ page }) => {
  await open(page);
});

test.describe("notebook", () => {
  test("loads and accepts writing", async ({ page }) => {
    await expect(page.getByRole("button", { name: "Pen", exact: true })).toBeVisible();
    await write(page, "2x");
    expect(await strokeCount(page)).toBe(3);
    const painted = await page.locator("canvas").first().evaluate((canvas: HTMLCanvasElement) => {
      const data = canvas.getContext("2d")!.getImageData(0, 0, canvas.width, canvas.height).data;
      let dark = 0;
      for (let i = 0; i < data.length; i += 4) if (data[i] < 100) dark += 1;
      return dark;
    });
    expect(painted).toBeGreaterThan(50);
  });

  test("erases, undoes and redoes", async ({ page }) => {
    await write(page, "1 1", 80, 80);
    expect(await strokeCount(page)).toBe(2);
    await page.getByTestId("tool-eraser").click();
    const box = (await page.getByTestId("ink").boundingBox())!;
    await page.mouse.move(box.x + 80 + 17 * 0.8 * 0.5 * 2 * 0.5 + 3, box.y + 97);
    await page.mouse.down();
    await page.mouse.move(box.x + 110, box.y + 97, { steps: 5 });
    await page.mouse.up();
    expect(await strokeCount(page)).toBe(1);
    await page.getByTestId("undo").click();
    expect(await strokeCount(page)).toBe(2);
    await page.getByTestId("redo").click();
    expect(await strokeCount(page)).toBe(1);
  });

  test("clears only after confirmation, and a clear can be undone", async ({ page }) => {
    await write(page, "1");
    await page.getByTestId("clear").click();
    expect(await strokeCount(page)).toBe(1);
    await page.getByRole("button", { name: "Keep it" }).click();
    expect(await strokeCount(page)).toBe(1);
    await page.getByTestId("clear").click();
    await page.getByTestId("clear-confirm").click();
    expect(await strokeCount(page)).toBe(0);
    await page.getByTestId("undo").click();
    expect(await strokeCount(page)).toBe(1);
  });

  test("keeps notes across a reload", async ({ page }) => {
    await write(page, "2x");
    await settle(page, 700);
    await page.reload();
    await expect.poll(() => strokeCount(page)).toBe(3);
  });

  test("keeps ink in place when the window is resized", async ({ page }) => {
    await write(page, "1", 100, 100);
    const inkPosition = () =>
      page.locator("canvas").first().evaluate((canvas: HTMLCanvasElement) => {
        const { width, height } = canvas;
        const data = canvas.getContext("2d")!.getImageData(0, 0, width, height).data;
        let minX = width;
        for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) if (data[(y * width + x) * 4] < 100 && x < minX) minX = x;
        return minX / width;
      });
    await settle(page, 100);
    const before = await inkPosition();
    await page.setViewportSize({ width: 800, height: 700 });
    await settle(page, 200);
    const after = await inkPosition();
    expect(Math.abs(after - before)).toBeLessThan(0.01);
  });
});

test.describe("the core loop", () => {
  test("a verified mistake quacks once, offers help, and resolves on correction", async ({ page }) => {
    await script({ reading: WRONG, help: ["Both terms get multiplied by 2. So 2(x + 3) becomes 2x + 6, not 2x + 3."] });
    await write(page, "2(x+3)", 80, 60);
    await write(page, "=2x+3", 80, 130);

    await expect(bubble(page)).toHaveText(/Quack!/);
    await expectDuck(page, "alert");
    await expect(bubble(page)).not.toContainText("2x");

    await page.getByTestId("hint-button").click();
    await expect(page.getByTestId("help-level-1")).toHaveText("Check whether you multiplied both terms inside the bracket.");
    await expectDuck(page, "hint");

    await page.getByTestId("another-hint").click();
    await expect(page.getByTestId("help-level-2")).toHaveText("Look at what happened to each term in the bracket. Did every one get multiplied?");
    // Both were written for this kind of mistake, and neither needed the model.
    expect((await stats()).helpRequests).toBe(0);

    await page.getByTestId("explain").click();
    await expect(page.getByTestId("help-level-3")).toContainText("2x + 6");
    await expectDuck(page, "explaining");
    await expect(page.getByTestId("help-level-3")).toHaveAttribute("data-source", "model");

    await script({ reading: RIGHT });
    await write(page, "6", 300, 130);
    await expectDuck(page, "resolved");
    await expect(bubble(page)).toHaveText("That's it.");
    await expect(page.getByTestId("hint-panel")).toBeHidden();
    await expectDuck(page, "idle");
  });

  test("an unchanged mistake never quacks twice, and a new one does", async ({ page }) => {
    await spyOnQuacks(page);
    await page.reload();
    await script({ reading: WRONG });
    await page.mouse.click(5, 300);
    await write(page, "2(x+3)", 80, 60);
    await expectDuck(page, "alert");
    const afterFirst = await quacksPlayed(page);
    expect(afterFirst).toBe(1);

    // New strokes elsewhere, same mistake read again.
    await write(page, "1", 80, 200);
    await settle(page, 1200);
    await write(page, "1", 120, 200);
    await settle(page, 1200);
    expect(await quacksPlayed(page)).toBe(1);

    await script({ reading: RIGHT });
    await write(page, "1", 160, 200);
    await expectDuck(page, "resolved");

    await script({ reading: NEW_MISTAKE });
    await write(page, "1", 200, 200);
    await expectDuck(page, "alert");
    expect(await quacksPlayed(page)).toBe(2);
  });

  test("correct working stays silent", async ({ page }) => {
    await script({ reading: reading(first("3(x + 2)"), line("= 3x + 6")) });
    await write(page, "3(x+2)", 80, 60);
    await write(page, "=3x+6", 80, 130);
    await expect.poll(async () => (await stats()).readingRequests).toBeGreaterThan(0);
    await settle(page, 800);
    await expect(bubble(page)).toBeHidden();
    await expectDuck(page, "idle");
  });

  test("ambiguous handwriting never accuses, and a manual check asks for clarity", async ({ page }) => {
    await script({ reading: reading(first("2(x + 3)"), line("= 2x + 3", { legibility: "unsure", alternatives: ["= 2x + 8"] })) });
    await write(page, "2(x+3)", 80, 60);
    await expect.poll(async () => (await stats()).readingRequests).toBeGreaterThan(0);
    await settle(page, 800);
    await expect(bubble(page)).toBeHidden();

    await page.getByTestId("check").click();
    await expect(bubble(page)).toContainText("can't quite read");
    await expectDuck(page, "idle");
  });

  test("unsupported notation stays silent", async ({ page }) => {
    await script({ reading: reading(first("∫x dx")) });
    await write(page, "2x+1");
    await expect.poll(async () => (await stats()).readingRequests).toBeGreaterThan(0);
    await settle(page, 800);
    await expect(bubble(page)).toBeHidden();
  });

  test("a second reading that disagrees blocks the alert", async ({ page }) => {
    await script({ queue: [WRONG, RIGHT], reading: RIGHT });
    await write(page, "2(x+3)");
    await expect.poll(async () => (await stats()).readingRequests).toBeGreaterThanOrEqual(2);
    await settle(page, 800);
    await expect(bubble(page)).toBeHidden();
    await expectDuck(page, "idle");
  });

  test("erasing the whole page clears the alert", async ({ page }) => {
    await script({ reading: WRONG });
    await write(page, "2(x+3)");
    await expectDuck(page, "alert");
    await page.getByTestId("clear").click();
    await page.getByTestId("clear-confirm").click();
    // It's gone, but nothing has shown it was put right, so no "That's it".
    await expectDuck(page, "idle");
    await expect(duck(page)).not.toHaveAttribute("aria-label", /re-check/);
  });

  test("the alert can be dismissed without coming back", async ({ page }) => {
    await script({ reading: WRONG });
    await write(page, "2(x+3)");
    await expectDuck(page, "alert");
    await page.getByTestId("dismiss-button").click();
    await expect(bubble(page)).toBeHidden();
    await write(page, "1", 80, 200);
    await settle(page, 1200);
    await expect(bubble(page)).toBeHidden();
    await duck(page).click();
    await expectDuck(page, "alert");
  });

  test("typed questions reach the explanation", async ({ page }) => {
    await script({ reading: WRONG, help: ["Look at the bracket.", "Both terms get multiplied. So it becomes 2x + 6, not 2x + 3."] });
    await write(page, "2(x+3)");
    await expectDuck(page, "alert");
    await duck(page).click();
    await expect(page.getByTestId("help-level-1")).toBeVisible();
    await page.getByTestId("question").fill("Why is this wrong?");
    await page.getByTestId("question").press("Enter");
    await expect(page.getByTestId("help-level-3")).toContainText("2x + 6");
  });

  test("a hint that gives away the answer is replaced and labelled", async ({ page }) => {
    await script({ reading: WRONG, help: ["The answer is 2x + 6.", "It should be 2x + 6."] });
    await write(page, "2(x+3)");
    await expectDuck(page, "alert");
    await duck(page).click();
    const hint = page.getByTestId("help-level-1");
    await expect(hint).toBeVisible();
    await expect(hint).toHaveAttribute("data-source", "template");
    await expect(hint).not.toContainText("6");
  });
});

test.describe("concurrency", () => {
  test("a slow reply cannot complain about writing that has since changed", async ({ page }) => {
    await script({ reading: WRONG, delay_ms: 1500 });
    await write(page, "2(x+3)");
    await expect.poll(async () => (await stats()).readingRequests).toBeGreaterThan(0);

    // The student fixes the line while the slow request is still out.
    await script({ reading: RIGHT, delay_ms: 0 });
    await write(page, "6", 300, 130);
    await settle(page, 2500);
    await expect(bubble(page)).toBeHidden();
    await expectDuck(page, "idle");
  });

  test("rapid edits produce no duplicate alerts", async ({ page }) => {
    await spyOnQuacks(page);
    await page.reload();
    await page.mouse.click(5, 300);
    await script({ reading: WRONG, delay_ms: 150 });
    for (let i = 0; i < 6; i++) {
      await write(page, "1", 80 + i * 30, 60);
      await page.waitForTimeout(450);
    }
    await expectDuck(page, "alert");
    await settle(page, 1000);
    expect(await quacksPlayed(page)).toBe(1);
  });

  test("identical ink is not analysed again", async ({ page }) => {
    await script({ reading: RIGHT });
    await write(page, "2x+1");
    await expect.poll(async () => (await stats()).readingRequests).toBeGreaterThan(0);
    await settle(page, 500);
    const readings = (await stats()).readingRequests;
    await write(page, "9", 300, 300);
    await page.getByTestId("undo").click();
    await settle(page, 1200);
    expect((await stats()).readingRequests).toBe(readings);
  });
});

test.describe("failure", () => {
  test("a model failure shows a message and leaves the notebook working", async ({ page }) => {
    await script({ reading: WRONG, status: 500 });
    await write(page, "2x");
    await expect(page.getByTestId("issue")).toContainText("Meta's API", { timeout: 15000 });
    await expect(bubble(page)).toBeHidden();
    await write(page, "1", 80, 200);
    expect(await strokeCount(page)).toBe(4);
  });

  test("recovers after the failure ends", async ({ page }) => {
    await script({ reading: WRONG, status: 401 });
    await write(page, "2x");
    await expect(page.getByTestId("issue")).toContainText("rejected");
    await script({ reading: RIGHT, status: 200 });
    await write(page, "1", 80, 200);
    await expect(page.getByTestId("issue")).toBeHidden();
  });

  test("a dead backend does not lose the page", async ({ page }) => {
    await page.route("**/api/analyze", (route) => route.abort());
    await write(page, "2x");
    await expect(page.getByTestId("issue")).toContainText("can't reach");
    expect(await strokeCount(page)).toBe(3);
    await settle(page, 700);
    await page.reload();
    await expect.poll(() => strokeCount(page)).toBe(3);
  });

  test("the browser never receives the API key", async ({ page }) => {
    const seen: string[] = [];
    page.on("response", async (response) => {
      if (response.url().includes("/_next/") || response.url().includes("/api/")) {
        seen.push(await response.text().catch(() => ""));
      }
    });
    await script({ reading: WRONG });
    await write(page, "2(x+3)");
    await expectDuck(page, "alert");
    expect((await stats()).authHeaders).toEqual(["Bearer fake-e2e-key"]);
    expect(seen.join("\n")).not.toContain("fake-e2e-key");
  });
});

test.describe("controls", () => {
  test("monitoring off stops automatic checks but not manual ones", async ({ page }) => {
    await script({ reading: WRONG });
    await page.getByTestId("monitoring").click();
    await write(page, "2(x+3)");
    await settle(page, 1200);
    expect((await stats()).readingRequests).toBe(0);
    await page.getByTestId("check").click();
    await expectDuck(page, "alert");
  });

  test("monitoring off persists across a reload", async ({ page }) => {
    await page.getByTestId("monitoring").click();
    await page.reload();
    await write(page, "2x");
    await settle(page, 1200);
    expect((await stats()).readingRequests).toBe(0);
  });

  test("mute silences the sound but not the alert", async ({ page }) => {
    await spyOnQuacks(page);
    await page.reload();
    await script({ reading: WRONG });
    await page.getByTestId("sound").click();
    await expectDuck(page, "muted");
    await write(page, "2(x+3)");
    await expectDuck(page, "alert");
    await expect(bubble(page)).toHaveText(/Quack!/);
    expect(await quacksPlayed(page)).toBe(0);
  });
});

test("the privacy note appears once", async ({ page }) => {
  await page.evaluate(() => localStorage.removeItem("quack.privacy-seen"));
  await page.reload();
  await expect(page.getByTestId("privacy")).toContainText("Meta");
  await page.getByRole("button", { name: "Got it" }).click();
  await page.reload();
  await expect(page.getByRole("button", { name: "Pen", exact: true })).toBeVisible();
  await expect(page.getByTestId("privacy")).toBeHidden();
});

test.describe("sample collection", () => {
  test("needs consent, keeps samples out of the student's notebook, and exports labels", async ({ page }) => {
    await write(page, "1", 80, 80);
    await settle(page, 700);

    await page.goto("/collect");
    await expect(page.getByTestId("start")).toBeDisabled();
    await page.getByTestId("contributor").fill("tester");
    await expect(page.getByTestId("start")).toBeDisabled();
    await page.getByTestId("consent").check();
    await page.getByTestId("start").click();

    await write(page, "2x", 60, 60);
    const firstPrompt = await page.getByTestId("prompt").innerText();
    await page.getByTestId("next").click();
    await expect(page.getByTestId("prompt")).not.toHaveText(firstPrompt);

    const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("download").click()]);
    const path = await download.path();
    const bundle = JSON.parse((await import("node:fs")).readFileSync(path, "utf8"));
    expect(bundle.consent).toBe(true);
    expect(bundle.contributor).toBe("tester");
    expect(bundle.samples).toHaveLength(1);
    expect(bundle.samples[0].lines.join("\n")).toBe(firstPrompt);
    expect(bundle.samples[0].image).toMatch(/^data:image\/png;base64,/);

    await page.goto("/");
    await expect.poll(() => strokeCount(page)).toBe(1);
  });
});
