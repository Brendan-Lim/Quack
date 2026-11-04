// A finished answer in a lesson isn't held back by a slow check of the half-written line before it. On the iPad on
// 2026-11-04 it was, by 5 to 16 seconds. Real browser, proxy, API and verifier; only Meta's API is faked, and
// made slow for the first check and quick for the second, as that session's second readings were (18 s, then 9 s).
import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";
import { first, quacksPlayed, reading, script, spyOnQuacks, stats, write } from "./helpers";

const SAMPLES = ["2 * 3 = 6", "1 ÷ 4 = 1/4", "x^2 + 3x", "d/dx(x^3) = 3x^2", "7x + 1 = 15", "(x + 2)^2"];

test.beforeEach(async () => {
  await script({ reset: true });
});

test("a finished answer is confirmed while a slower check of the half-written line is still under way", async ({ page }) => {
  test.setTimeout(180_000);
  await spyOnQuacks(page);
  await page.goto("/");
  await page.getByTestId("choose-create").click();
  await page.getByTestId("email").fill(`student-${randomUUID()}@example.com`);
  await page.getByTestId("password").fill("correct horse battery");
  await page.getByTestId("account-submit").click();
  await page.getByTestId("calibration-start").click();
  for (const sample of SAMPLES) {
    await write(page, sample);
    await page.getByTestId("calibration-next").click();
  }
  await page.getByTestId("calibration-finish").click();
  // A first visit asks where to start. These tests start in the notebook.
  await page.getByTestId("choose-notebook").click();
  await page.evaluate(() => localStorage.setItem("quack.privacy-seen", "1"));
  await page.getByTestId("open-learn").click();
  await page.getByTestId("path-algebra_foundations").click();
  await page.getByTestId("level-start").click();
  await page.getByTestId("lesson-try").click();
  await expect(page.getByTestId("lesson-prompt")).toHaveText("Simplify y + y + y + y + y");

  // The pen lifts after "5". Its check reads slowly, and what it reads would be a slip if it ever counted.
  const before = (await stats()).readingRequests;
  await script({ reading: reading(first("5")), delay_ms: 9000 });
  await write(page, "5", 80, 80);
  await expect.poll(async () => (await stats()).readingRequests, { timeout: 5000 }).toBeGreaterThan(before);
  // The finished answer, read quickly.
  await script({ reading: reading(first("5y")), delay_ms: 300 });
  await write(page, "x", 80 + 34, 80);
  const lastStroke = Date.now();
  await expect(page.getByTestId("lesson-correct")).toBeVisible({ timeout: 6000 });
  // Well before the slow check could have answered, which is when it was confirmed before.
  expect(Date.now() - lastStroke).toBeLessThan(6000);
  await expect(page.getByTestId("lesson-progress")).toHaveText("1 of 3 points");
  // The success chime is sound too, so what's counted is anything after it.
  await page.waitForTimeout(500);
  const sounds = await quacksPlayed(page);

  // The slow check's reply arrives later, about older ink. It changes nothing: no alert, no quack, no lost point.
  await page.waitForTimeout(11_000);
  await expect(page.getByTestId("lesson-correct")).toBeVisible();
  await expect(page.getByTestId("lesson").getByTestId("bubble")).toHaveCount(0);
  await expect(page.getByTestId("lesson-noticed")).toHaveCount(0);
  expect(await quacksPlayed(page)).toBe(sounds);
  await expect(page.getByTestId("lesson-progress")).toHaveText("1 of 3 points");
});
