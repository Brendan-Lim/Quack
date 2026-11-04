// The second physical iPad session of 2026-11-02, with only Meta's API faked. The readings scripted here
// are the ones the real model gave in that session. What the real model makes of real Apple Pencil ink,
// and how often, is still to be tested on an iPad.
import { expect, test, type Page } from "@playwright/test";
import { line, open, quacksPlayed, reading, script, spyOnQuacks, write } from "./helpers";

const calculus = (math: string, overrides = {}) => line(math, { continues_previous: "no", ...overrides });
const count = (page: Page) => page.getByTestId("mistake-count");
const markers = async (page: Page): Promise<unknown[]> => JSON.parse((await page.getByTestId("notebook").getAttribute("data-markers")) ?? "[]");

async function circle(page: Page, left: number, top: number, right: number, bottom: number) {
  const box = (await page.getByTestId("ink").boundingBox())!;
  await page.getByTestId("tool-lasso").click();
  await page.mouse.move(box.x + left, box.y + top);
  await page.mouse.down();
  await page.mouse.move(box.x + right, box.y + top, { steps: 6 });
  await page.mouse.move(box.x + right, box.y + bottom, { steps: 3 });
  await page.mouse.move(box.x + left, box.y + bottom, { steps: 6 });
  await page.mouse.move(box.x + left, box.y + top, { steps: 3 });
  await page.mouse.up();
}

test.beforeEach(async ({ page }) => {
  await spyOnQuacks(page);
  await open(page);
});

test("the same wrong integral written again lower down is its own alert", async ({ page }) => {
  // Screenshot 4: the first copy was flagged at 15:11, and the second, at 15:20, never was.
  await script({ reading: reading(calculus("∫x dx=2")) });
  await write(page, "x=2", 80, 60);
  await expect(count(page)).toHaveText("1");
  await script({ reading: reading(calculus("∫x dx=2"), calculus("d/dx(cos(x)) = -sin(x)")) });
  await write(page, "c=s", 80, 200);
  await expect.poll(async () => (await page.evaluate(() => (window as unknown as { __quackLatency: { stage: string }[] }).__quackLatency.filter((mark) => mark.stage === "result_applied").length))).toBeGreaterThan(1);
  await script({ reading: reading(calculus("∫x dx=2"), calculus("d/dx(cos(x)) = -sin(x)"), calculus("∫ x dx = 2")) });
  await write(page, "x=2", 80, 340);
  await expect(count(page)).toHaveText("2");
  await expect.poll(() => markers(page).then((found) => found.length)).toBe(2);
  await expect.poll(() => quacksPlayed(page)).toBe(2);
});

test.describe("Explain this step, at iPad size", () => {
  test.use({ viewport: { width: 1024, height: 768 } });

  test("is a few short numbered steps, inside the screen", async ({ page }) => {
    await script({ reading: reading(calculus("∫ x dx = x^2/2 + C")) });
    await write(page, "x=x", 120, 120);
    await expect.poll(async () => (await page.evaluate(() => (window as unknown as { __quackLatency: { stage: string }[] }).__quackLatency.some((mark) => mark.stage === "result_applied")))).toBe(true);
    await circle(page, 90, 90, 330, 190);
    await page.getByTestId("ask-explain").click();
    const steps = page.getByTestId("ask-step");
    await expect(steps).toHaveCount(3);
    await expect(steps.first()).toContainText("Raise the power by one");
    // Written as a student would: x², not x^2.
    await expect(steps.nth(1)).toContainText("x²/2");
    await expect(page.getByTestId("ask-takeaway")).toContainText("Quick check");
    await expect(page.getByRole("list").filter({ has: steps.first() })).toBeVisible();
    const panel = (await page.getByTestId("ask-panel").boundingBox())!;
    expect(panel.y).toBeGreaterThanOrEqual(0);
    expect(panel.y + panel.height).toBeLessThanOrEqual(768);
    expect(panel.x + panel.width).toBeLessThanOrEqual(1024);
    // Its buttons are still there to use.
    await expect(page.getByTestId("ask-open-question")).toBeVisible();
  });

  test("never teaches like terms for what it can't make out", async ({ page }) => {
    await script({ reading: reading(calculus("x^2/2 + C")) });
    await write(page, "x+2", 120, 120);
    await expect.poll(async () => (await page.evaluate(() => (window as unknown as { __quackLatency: { stage: string }[] }).__quackLatency.some((mark) => mark.stage === "result_applied")))).toBe(true);
    await circle(page, 90, 90, 330, 190);
    await page.getByTestId("ask-explain").click();
    await expect(page.getByTestId("ask-reply")).toContainText("won't guess");
    await expect(page.getByTestId("ask-reply")).not.toContainText("Like terms");
  });
});
