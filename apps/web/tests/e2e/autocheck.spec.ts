// The reported failures, end to end with a scripted model: a student writes one line, and nothing
// should need a button. None of these tests press Check my work unless it says so. How fast the
// real model is, and how well it reads, is measured separately in tests/live and evals/.
import { expect, test, type Page } from "@playwright/test";
import { bubble, expectDuck, line, open, quacksPlayed, reading, script, settle, spyOnQuacks, stats, strokeCount, write } from "./helpers";

const sum = (math: string, overrides: Record<string, unknown> = {}) =>
  line(math, { continues_previous: "no", operation: "arithmetic", y_top: 0.1, y_bottom: 0.9, ...overrides });

const hasCheckedOnce = () => expect.poll(async () => (await stats()).readingRequests).toBeGreaterThan(0);

async function listen(page: Page) {
  await spyOnQuacks(page);
  await page.reload();
  await expect(page.getByTestId("ink")).toBeVisible();
  await page.mouse.click(5, 300);
}

test.beforeEach(async ({ page }) => {
  await open(page);
});

test.describe("one line on its own, with no button pressed", () => {
  for (const [math, written] of [
    ["2 * 3 = 5", "2*3=5"],
    ["3 * 4 = 10", "3*4=10"],
    ["6 / 2 = 2", "6/2=2"],
    ["7 + 5 = 11", "7+5=11"],
  ]) {
    test(`${written} quacks once`, async ({ page }) => {
      await listen(page);
      await script({ reading: reading(sum(math)) });
      await write(page, written, 80, 60);
      await expect(bubble(page)).toHaveText(/Quack!/);
      await expectDuck(page, "alert");
      await settle(page, 800);
      expect(await quacksPlayed(page)).toBe(1);
    });
  }

  for (const [math, written] of [
    ["2 * 3 = 6", "2*3=6"],
    ["3 * 4 = 12", "3*4=12"],
    ["6 / 2 = 3", "6/2=3"],
    ["7 + 5 = 12", "7+5=12"],
  ]) {
    test(`${written} stays silent`, async ({ page }) => {
      await listen(page);
      await script({ reading: reading(sum(math)) });
      await write(page, written, 80, 60);
      await hasCheckedOnce();
      await settle(page, 800);
      await expectDuck(page, "idle");
      await expect(bubble(page)).toBeHidden();
      expect(await quacksPlayed(page)).toBe(0);
    });
  }

  test("a wrong derivative on one line quacks", async ({ page }) => {
    await script({ reading: reading(sum("d/dx(x^3 + 2x) = 3x + 2", { operation: "differentiate" })) });
    await write(page, "d/dx(x^3+2x)=3x+2", 40, 60, 28);
    await expect(bubble(page)).toHaveText(/Quack!/);
  });

  test("a right derivative on one line stays silent", async ({ page }) => {
    await script({ reading: reading(sum("d/dx(x^3 + 2x) = 3x^2 + 2", { operation: "differentiate" })) });
    await write(page, "d/dx(x^3+2x)=3x^2+2", 40, 60, 28);
    await hasCheckedOnce();
    await settle(page, 800);
    await expectDuck(page, "idle");
  });

  test("a line the model isn't sure of stays silent", async ({ page }) => {
    await script({ reading: reading(sum("2 * 3 = 5", { legibility: "unsure", alternatives: ["2 * 3 = 6"] })) });
    await write(page, "2*3=5", 80, 60);
    await hasCheckedOnce();
    await settle(page, 800);
    await expectDuck(page, "idle");
  });

  test("the two readings disagreeing stays silent", async ({ page }) => {
    await script({ queue: [reading(sum("2 * 3 = 5")), reading(sum("2 * 3 = 6"))], reading: reading(sum("2 * 3 = 6")) });
    await write(page, "2*3=5", 80, 60);
    await hasCheckedOnce();
    await settle(page, 800);
    await expectDuck(page, "idle");
  });
});

test.describe("hints", () => {
  test("come at once, with no further reading of the page and no model", async ({ page }) => {
    await script({ reading: reading(sum("2 * 3 = 5")) });
    await write(page, "2*3=5", 80, 60);
    await expect(bubble(page)).toHaveText(/Quack!/);
    await settle(page, 300);
    const before = await stats();
    // A model this slow would be obvious if anything were still waiting for one.
    await script({ delay_ms: 8000 });

    const started = Date.now();
    await page.getByTestId("hint-button").click();
    await expect(page.getByTestId("help-level-1")).toHaveText("Check that multiplication again.");
    await page.getByTestId("another-hint").click();
    await expect(page.getByTestId("help-level-2")).toHaveText("Try adding 3 twice. Does that give you 5?");
    expect(Date.now() - started).toBeLessThan(2000);

    const after = await stats();
    expect(after.readingRequests).toBe(before.readingRequests);
    expect(after.helpRequests).toBe(0);
  });

  test("don't give the answer", async ({ page }) => {
    await script({ reading: reading(sum("3 * 4 = 10")) });
    await write(page, "3*4=10", 80, 60);
    await expect(bubble(page)).toHaveText(/Quack!/);
    await page.getByTestId("hint-button").click();
    await expect(page.getByTestId("help-level-1")).not.toContainText("12");
    await page.getByTestId("another-hint").click();
    await expect(page.getByTestId("help-level-2")).toBeVisible();
    await expect(page.getByTestId("help-level-2")).not.toContainText("12");
  });
});

test.describe("correcting a mistake, with no button pressed", () => {
  async function wrongSum(page: Page) {
    await script({ reading: reading(sum("2 * 3 = 5")) });
    await write(page, "2*3=5", 80, 60);
    await expect(bubble(page)).toHaveText(/Quack!/);
    await settle(page, 200);
  }

  test("the alert goes away as soon as the ink changes, before any reply", async ({ page }) => {
    await wrongSum(page);
    await script({ reading: reading(sum("2 * 3 =", { kind: "incomplete" })), delay_ms: 4000 });
    await page.getByTestId("undo").click();
    await expect(bubble(page)).toBeHidden({ timeout: 500 });
    await expect(page.getByTestId("duck")).not.toHaveAttribute("data-duck-state", "alert");
  });

  test("replacing the wrong number is recognised", async ({ page }) => {
    await listen(page);
    await wrongSum(page);
    await script({ reading: reading(sum("2 * 3 = 6")) });
    await page.getByTestId("undo").click();
    await write(page, "6", 80 + 34 * 4, 60);
    await expectDuck(page, "resolved");
    await expect(bubble(page)).toHaveText("That's it.");
    expect(await quacksPlayed(page)).toBe(1);
  });

  test("only part way through the fix, it doesn't claim to be fixed", async ({ page }) => {
    await wrongSum(page);
    await script({ reading: reading(sum("2 * 3 =", { kind: "incomplete" })) });
    await page.getByTestId("undo").click();
    await settle(page, 1500);
    await expect(page.getByTestId("duck")).not.toHaveAttribute("data-duck-state", "resolved");
    await expect(bubble(page)).toBeHidden();
  });

  test("changing the number to another wrong one quacks about that one", async ({ page }) => {
    await listen(page);
    await wrongSum(page);
    await script({ reading: reading(sum("2 * 3 = 7")) });
    await page.getByTestId("undo").click();
    await write(page, "7", 80 + 34 * 4, 60);
    await expect.poll(() => quacksPlayed(page)).toBe(2);
    await expectDuck(page, "alert");
  });

  test("touching the line without fixing it brings the alert back with no second quack", async ({ page }) => {
    await listen(page);
    await wrongSum(page);
    await write(page, ".", 80 + 34 * 6, 60);
    await expect(bubble(page)).toHaveText(/Quack!/);
    await settle(page, 500);
    expect(await quacksPlayed(page)).toBe(1);
  });

  test("touching the line and undoing it brings the alert back", async ({ page }) => {
    await listen(page);
    await wrongSum(page);
    await write(page, "1", 80 + 34 * 6, 60);
    await page.getByTestId("undo").click();
    await expect(bubble(page)).toHaveText(/Quack!/);
    await settle(page, 500);
    expect(await quacksPlayed(page)).toBe(1);
  });

  test("the right answer written below is recognised", async ({ page }) => {
    await wrongSum(page);
    await script({ reading: reading(sum("2 * 3 = 5", { y_top: 0.1, y_bottom: 0.4 }), sum("= 6", { continues_previous: "yes", y_top: 0.6, y_bottom: 0.9 })) });
    await write(page, "=6", 80, 140);
    await expectDuck(page, "resolved");
  });

  test("another problem below is not taken for the correction", async ({ page }) => {
    await listen(page);
    await wrongSum(page);
    await script({ reading: reading(sum("2 * 3 = 5", { y_top: 0.1, y_bottom: 0.4 }), sum("7 + 5 = 12", { y_top: 0.6, y_bottom: 0.9 })) });
    const before = (await stats()).readingRequests;
    await write(page, "7+5=12", 80, 300);
    await expect.poll(async () => (await stats()).readingRequests).toBeGreaterThan(before);
    await settle(page, 800);
    await expectDuck(page, "alert");
    expect(await quacksPlayed(page)).toBe(1);
  });

  test("rewriting the whole line is recognised", async ({ page }) => {
    await wrongSum(page);
    await script({ reading: reading(sum("2 * 3 = 6")) });
    for (let stroke = 0; stroke < 7; stroke++) await page.getByTestId("undo").click();
    await write(page, "2*3=6", 80, 60);
    await expectDuck(page, "resolved");
  });

  test("a fix made while the check that found the mistake is on its way isn't accused", async ({ page }) => {
    await listen(page);
    await script({ reading: reading(sum("2 * 3 = 5")), delay_ms: 1500 });
    await write(page, "2*3=5", 80, 60);
    await hasCheckedOnce();
    await script({ reading: reading(sum("2 * 3 = 6")), delay_ms: 0 });
    await page.getByTestId("undo").click();
    await write(page, "6", 80 + 34 * 4, 60);
    await settle(page, 2500);
    expect(await quacksPlayed(page)).toBe(0);
    await expectDuck(page, "idle");
  });
});

test.describe("while a check is running", () => {
  test("the student can keep writing, and the alert still arrives", async ({ page }) => {
    await listen(page);
    await script({ reading: reading(sum("3 * 4 = 10", { y_top: 0.2, y_bottom: 0.8 })), delay_ms: 1500 });
    await write(page, "3*4=10", 80, 60);
    await hasCheckedOnce();
    const strokes = await strokeCount(page);
    // Far enough down not to be taken for a correction.
    await write(page, "1", 80, 400);
    expect(await strokeCount(page)).toBeGreaterThan(strokes);
    await expect(bubble(page)).toHaveText(/Quack!/, { timeout: 8000 });
    await settle(page, 800);
    expect(await quacksPlayed(page)).toBe(1);
  });

  test("writing goes on working during a very slow check", async ({ page }) => {
    await script({ reading: reading(sum("2 * 3 = 6")), delay_ms: 5000 });
    await write(page, "2*3=6", 80, 60);
    await hasCheckedOnce();
    await expect(page.getByTestId("duck")).toHaveAttribute("data-duck-state", "checking");
    const strokes = await strokeCount(page);
    await write(page, "1", 80, 300);
    expect(await strokeCount(page)).toBeGreaterThan(strokes);
  });

  test("pressing Check my work over and over reads the page once", async ({ page }) => {
    await script({ reading: reading(sum("2 * 3 = 5")), delay_ms: 1500 });
    await page.getByTestId("monitoring").click();
    await write(page, "2*3=5", 80, 60);
    for (let press = 0; press < 5; press++) await page.getByTestId("check").click();
    await expect(bubble(page)).toHaveText(/Quack!/, { timeout: 8000 });
    // The first reading and its confirmation.
    expect((await stats()).readingRequests).toBe(2);
  });

  test("a passing failure is tried again with no further writing", async ({ page }) => {
    await script({ reading: reading(sum("2 * 3 = 5")), status: 500 });
    await write(page, "2*3=5", 80, 60);
    await expect(page.getByTestId("issue")).toBeVisible({ timeout: 15000 });
    await script({ reading: reading(sum("2 * 3 = 5")), status: 200 });
    await expect(bubble(page)).toHaveText(/Quack!/, { timeout: 15000 });
  });

  test("a failure says so and the next edit checks again", async ({ page }) => {
    await script({ reading: reading(sum("2 * 3 = 5")), status: 401 });
    await write(page, "2*3=5", 80, 60);
    await expect(page.getByTestId("issue")).toContainText("Meta's API", { timeout: 15000 });
    await script({ reading: reading(sum("2 * 3 = 5")), status: 200 });
    await write(page, "1", 80, 400);
    await expect(bubble(page)).toHaveText(/Quack!/, { timeout: 15000 });
  });
});

test.describe("monitoring", () => {
  test("off means no check until asked, and on again checks what was written", async ({ page }) => {
    await script({ reading: reading(sum("2 * 3 = 5")) });
    await page.getByTestId("monitoring").click();
    await write(page, "2*3=5", 80, 60);
    await settle(page, 1000);
    expect((await stats()).readingRequests).toBe(0);
    await page.getByTestId("monitoring").click();
    await expect(bubble(page)).toHaveText(/Quack!/);
  });
});
