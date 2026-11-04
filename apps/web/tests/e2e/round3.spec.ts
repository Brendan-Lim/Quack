// Physical iPad test, round 3, in a real browser with the fake model: the page where most mistakes
// went unreported. This is the path from a reading to the screen. It isn't handwriting recognition.
import { expect, test, type Page } from "@playwright/test";
import { expectDuck, line, open, quacksPlayed, reading, script, settle, spyOnQuacks, stats, write } from "./helpers";

type Marker = { id: string; kind: string; minX: number; minY: number; maxX: number; maxY: number };

const sum = (math: string, overrides = {}) => line(math, { continues_previous: "unknown", operation: "arithmetic", ...overrides });
const markers = async (page: Page): Promise<Marker[]> => JSON.parse((await page.getByTestId("notebook").getAttribute("data-markers")) ?? "[]");
const count = (page: Page) => page.getByTestId("mistake-count");
const choices = (page: Page) => page.getByTestId("mistake-choice");

const ANSWERS = ["3 * 4 = 10", "1 ÷ 2 = 1/2", "3 * 4 = 9", "1 ÷ 3 = 0", "1 ÷ 2 = 3", "1 ÷ 4 = 0", "1 ÷ 5 = 0", "1 * 4 = 4"];
const INK = ["3*4=10", "1/2=1/2", "3*4=9", "1/3=0", "1/2=3", "1/4=0", "1/5=0", "1*4=4"];
const WRONG = [0, 2, 3, 4, 5, 6];
const yOf = (position: number) => 50 + position * 80;

test.beforeEach(async ({ page }) => {
  await spyOnQuacks(page);
  await open(page);
});

test.describe("the reported page", () => {
  for (const position of WRONG) {
    test(`${ANSWERS[position]} alone on a clean page`, async ({ page }) => {
      await script({ reading: reading(sum(ANSWERS[position])) });
      await write(page, INK[position], 80, yOf(0));
      await expectDuck(page, "alert");
      expect(await markers(page)).toHaveLength(1);
      await settle(page, 400);
      expect(await quacksPlayed(page)).toBe(1);
    });
  }

  test("written all at once, each mistake gets a record, a highlight and a quack", async ({ page }) => {
    await script({ reading: reading(...ANSWERS.map((text) => sum(text))) });
    for (const [position, ink] of INK.entries()) await write(page, ink, 80, yOf(position));
    await expect(count(page)).toHaveText(String(WRONG.length));
    await expect(choices(page)).toHaveCount(WRONG.length);
    for (const text of ["3 × 4 = 10", "3 × 4 = 9", "1/3 = 0", "1/2 = 3", "1/4 = 0", "1/5 = 0"]) {
      await expect(page.getByTestId("mistake-choices").getByText(text, { exact: true })).toBeVisible();
    }
    const marks = await markers(page);
    expect(marks).toHaveLength(WRONG.length);
    // Each highlight is its own line's, and they don't touch.
    const sorted = [...marks].sort((a, b) => a.minY - b.minY);
    for (let index = 1; index < sorted.length; index++) expect(sorted[index - 1].maxY).toBeLessThan(sorted[index].minY);
    // They're played a moment apart, so as to be heard as separate quacks.
    await settle(page, 2200);
    expect(await quacksPlayed(page)).toBe(WRONG.length);
  });

  test("written a line at a time, each new mistake quacks once, and right lines stay silent", async ({ page }) => {
    for (const position of ANSWERS.keys()) {
      await script({ reading: reading(...ANSWERS.slice(0, position + 1).map((text) => sum(text))) });
      await write(page, INK[position], 80, yOf(position));
      await expect.poll(() => quacksPlayed(page)).toBe(WRONG.filter((wrong) => wrong <= position).length);
      await settle(page, 900);
      expect(await quacksPlayed(page)).toBe(WRONG.filter((wrong) => wrong <= position).length);
    }
    expect(await markers(page)).toHaveLength(WRONG.length);
    await expect(count(page)).toHaveText(String(WRONG.length));
  });

  test("hints stay with their own mistake", async ({ page }) => {
    await script({ reading: reading(sum("3 * 4 = 10"), sum("1 ÷ 3 = 0")) });
    await write(page, "3*4=10", 80, yOf(0));
    await page.waitForTimeout(50);
    await write(page, "1/3=0", 80, yOf(1));
    await expect(count(page)).toHaveText("2");
    await page.getByTestId("mistake-choices").getByText("1/3 = 0", { exact: true }).click();
    await page.getByTestId("hint-button").click();
    await expect(page.getByTestId("help-level-1")).toHaveText("Check that division again.");
    await page.getByTestId("mistake-choices").getByText("3 × 4 = 10", { exact: true }).click();
    await page.getByTestId("hint-button").click();
    await expect(page.getByTestId("help-level-1")).toHaveText("Check that multiplication again.");
  });

  test("right lines alone never quack", async ({ page }) => {
    await script({ reading: reading(...["1 ÷ 3 = 1/3", "1 ÷ 4 = 0.25", "1 ÷ 5 = 0.2", "3 * 4 = 12", "0 ÷ 5 = 0", "1 ÷ 10 = 0.1", "1/3 ≈ 0.33"].map((text) => sum(text))) });
    for (const [position, ink] of ["1/3=1/3", "1/4=0.25", "1/5=0.2", "3*4=12", "0/5=0", "1/10=0.1", "1/3=0.33"].entries()) await write(page, ink, 80, yOf(position));
    await settle(page, 1500);
    expect(await quacksPlayed(page)).toBe(0);
    expect(await markers(page)).toHaveLength(0);
  });
});

test.describe("a line that can't be settled", () => {
  test("is looked at again without being touched, and its mistake then comes up", async ({ page }) => {
    await script({
      reading: reading(sum("3 * 4 = 10")),
      // Two readings that disagree, and then two that don't.
      queue: [reading(sum("3 * 4 = 16", { legibility: "unsure", alternatives: ["3 * 4 = 12"] })), reading(sum("3 * 4 = 10"))],
    });
    await write(page, "3*4=10", 80, yOf(0));
    await expect(page.getByTestId("duck")).toHaveAttribute("data-duck-state", "alert", { timeout: 8000 });
    expect(await markers(page)).toHaveLength(1);
    expect((await stats()).readingRequests).toBe(4);
  });

  test("is only looked at a few times", async ({ page }) => {
    await script({ reading: reading(sum("3 * 4 = 10", { legibility: "unsure" })) });
    await write(page, "3*4=10", 80, yOf(0));
    await settle(page, 7500);
    // Two readings at the first look, and two for each of two more.
    expect((await stats()).readingRequests).toBe(6);
    expect(await quacksPlayed(page)).toBe(0);
  });

  test("is not looked at again once settled", async ({ page }) => {
    await script({ reading: reading(sum("3 * 4 = 12")) });
    await write(page, "3*4=12", 80, yOf(0));
    await settle(page, 3500);
    expect((await stats()).readingRequests).toBe(2);
  });
});

test.describe("the debug panel", () => {
  test("says what became of each line", async ({ page }) => {
    await page.goto("/?debug");
    await script({ reading: reading(sum("3 * 4 = 10"), sum("1 ÷ 2 = 1/2"), sum("1 ÷ 3 = 0.33")) });
    await write(page, "3*4=10", 80, yOf(0));
    await page.waitForTimeout(30);
    await write(page, "1/2=1/2", 80, yOf(1));
    await page.waitForTimeout(30);
    await write(page, "1/3=0.33", 80, yOf(2));
    await expect(page.getByTestId("debug-line")).toHaveCount(3);
    await expect(page.getByTestId("debug-disposition")).toHaveText([/confirmed_new_mistake|known_mistake_active/, "verification_valid", "verification_uncertain"]);
    await expect(page.getByTestId("debug-line").first()).toContainText("1st: 3 * 4 = 10");
  });
});
