// Does the celebration for a right answer get in the way of writing? Frame gaps while writing, at rest and while
// the duck, sparks, strip and progress animate, with the CPU slowed 4 times. Only runs with QUACK_PERF=1, and
// prints its numbers. A desktop Chromium slowed down is not an iPad, so this is a comparison, not a promise.
//
//   QUACK_PERF=1 QUACK_E2E_PORT_SHIFT=2000 npx playwright test perf.lessons --project=accounts
import { randomUUID } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import { first, reading, script, write } from "./helpers";

const SAMPLES = ["2 * 3 = 6", "1 ÷ 4 = 1/4", "x^2 + 3x", "d/dx(x^3) = 3x^2", "7x + 1 = 15", "(x + 2)^2"];

test.skip(!process.env.QUACK_PERF, "performance only on request");

async function frames(page: Page, during: () => Promise<void>) {
  await page.evaluate(() => {
    const gaps: number[] = [];
    let last = performance.now();
    (window as unknown as { __gaps: number[]; __stop: boolean }).__gaps = gaps;
    (window as unknown as { __stop: boolean }).__stop = false;
    const tick = (now: number) => {
      gaps.push(now - last);
      last = now;
      if (!(window as unknown as { __stop: boolean }).__stop) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  await during();
  const gaps = await page.evaluate(() => {
    (window as unknown as { __stop: boolean }).__stop = true;
    return (window as unknown as { __gaps: number[] }).__gaps.slice(1);
  });
  gaps.sort((a, b) => a - b);
  const at = (share: number) => gaps[Math.min(gaps.length - 1, Math.floor(gaps.length * share))];
  return { frames: gaps.length, p50: +at(0.5).toFixed(1), p95: +at(0.95).toFixed(1), max: +gaps[gaps.length - 1].toFixed(1), over33: gaps.filter((gap) => gap > 33.4).length };
}

test("writing while a right answer is celebrated", async ({ page }) => {
  test.setTimeout(300_000);
  await script({ reset: true });
  await page.setViewportSize({ width: 810, height: 976 });
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
  await expect(page.getByTestId("lesson-prompt")).toBeVisible();

  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });

  // At rest: nothing animating, the student writes a line.
  await script({ reading: reading(first("x")) });
  const rest = await frames(page, () => write(page, "x+x+x", 80, 260));
  await page.getByTestId("lesson-clear").click();
  await page.waitForTimeout(1500);

  // The answer, then writing straight away while the strip, sparks, duck and progress animate.
  // The prompt is in y; the test font has no y, so the ink is an x, and the fake reads it as asked.
  await script({ reading: reading(first("5y")) });
  await write(page, "5x", 80, 80);
  await expect(page.getByTestId("lesson-correct")).toBeVisible({ timeout: 20_000 });
  const celebrating = await frames(page, () => write(page, "x+x+x", 80, 260));

  console.log(JSON.stringify({ cpuThrottle: 4, rest, celebrating }));
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 1 });
});
