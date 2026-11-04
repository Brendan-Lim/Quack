// LIVE: real browser ink, real proxy, real API, and the real Meta Model API. Spends credit.
// The ink is drawn from a synthetic stroke alphabet by a script, so this proves the pipeline
// works, not how well Quack reads a person's handwriting.
import { expect, test } from "@playwright/test";
import { bubble, expectDuck, openFresh, write } from "../e2e/helpers";

const MODEL_WAIT = 60_000;

test.beforeEach(async ({ page }) => {
  await openFresh(page);
});

test("demo 1: algebra mistake, hint, then correction", async ({ page }) => {
  await write(page, "2(x+3)", 60, 40, 44);
  await write(page, "=2x+3", 60, 130, 44);
  await expect(bubble(page)).toHaveText(/Quack!/, { timeout: MODEL_WAIT });
  await expectDuck(page, "alert");
  await expect(bubble(page)).not.toContainText("2x");
  await page.screenshot({ path: "test-results/live/demo1-alert.png" });

  await page.getByTestId("hint-button").click();
  const first = page.getByTestId("help-level-1");
  await expect(first).toBeVisible({ timeout: MODEL_WAIT });
  const firstHint = await first.innerText();
  console.log(`[live] level 1 (${await first.getAttribute("data-source")}): ${firstHint}`);
  expect(firstHint).not.toContain("2x + 6");
  expect(firstHint).not.toMatch(/\b6\b/);

  await page.getByTestId("another-hint").click();
  const second = page.getByTestId("help-level-2");
  await expect(second).toBeVisible({ timeout: MODEL_WAIT });
  const secondHint = await second.innerText();
  console.log(`[live] level 2 (${await second.getAttribute("data-source")}): ${secondHint}`);
  expect(secondHint).not.toMatch(/\b6\b/);
  await page.screenshot({ path: "test-results/live/demo1-hint.png" });

  // Correct the working: undo the final 3 stroke by stroke, then write a 6.
  await page.getByTestId("undo").click();
  await write(page, "6", 60 + 44 * 4, 130, 44);
  await expectDuck(page, "resolved");
  await expect(page.getByTestId("hint-panel")).toBeHidden();
  await page.screenshot({ path: "test-results/live/demo1-resolved.png" });
});

test("demo 2: differentiation mistake and explanation", async ({ page }) => {
  await write(page, "d/dx(x^3+2x)", 60, 40, 44);
  await write(page, "=3x+2", 60, 130, 44);
  await expect(bubble(page)).toHaveText(/Quack!/, { timeout: MODEL_WAIT });
  await page.getByTestId("hint-button").click();
  await expect(page.getByTestId("help-level-1")).toBeVisible({ timeout: MODEL_WAIT });
  await page.getByTestId("question").fill("Why is this wrong?");
  await page.getByTestId("question").press("Enter");
  const explanation = page.getByTestId("help-level-3");
  await expect(explanation).toBeVisible({ timeout: MODEL_WAIT });
  const text = await explanation.innerText();
  console.log(`[live] explanation (${await explanation.getAttribute("data-source")}): ${text}`);
  expect(text.replace(/\s/g, "")).toContain("3x^2+2");
  await expect(explanation).toHaveAttribute("data-source", "model");
  await page.screenshot({ path: "test-results/live/demo2-explanation.png" });
});

test("demo 3: correct working stays silent", async ({ page }) => {
  const analysed = page.waitForResponse((response) => response.url().includes("/api/analyze") && response.status() === 200, { timeout: MODEL_WAIT });
  await write(page, "3(x+2)", 60, 40, 44);
  await write(page, "=3x+6", 60, 130, 44);
  let reply = await (await analysed).json();
  while (reply.snapshotVersion !== Number(await page.getByTestId("notebook").getAttribute("data-version"))) {
    reply = await (await page.waitForResponse((response) => response.url().includes("/api/analyze"), { timeout: MODEL_WAIT })).json();
  }
  console.log(`[live] demo 3: status=${reply.status} lines=${JSON.stringify(reply.lines.map((line: { text: string }) => line.text))} ${reply.timings.totalMs}ms $${reply.timings.costUsd}`);
  expect(reply.status).toBe("no_error");
  await page.waitForTimeout(1000);
  await expect(bubble(page)).toBeHidden();
  await expectDuck(page, "idle");
  await page.screenshot({ path: "test-results/live/demo3-silent.png" });
});
