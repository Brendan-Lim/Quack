// Physical iPad test, round 2, in a real browser with the fake model: each mistake on its own line.
import { expect, test, type Page } from "@playwright/test";
import { duck, expectDuck, line, open, quacksPlayed, reading, script, settle, spyOnQuacks, stats, write } from "./helpers";

type Marker = { id: string; kind: string; minX: number; minY: number; maxX: number; maxY: number };

const sum = (math: string, overrides = {}) => line(math, { continues_previous: "unknown", operation: "arithmetic", ...overrides });
const markers = async (page: Page): Promise<Marker[]> => JSON.parse((await page.getByTestId("notebook").getAttribute("data-markers")) ?? "[]");
const choices = (page: Page) => page.getByTestId("mistake-choice");
const count = (page: Page) => page.getByTestId("mistake-count");
// Page units to CSS pixels, as `write` positions ink in CSS pixels.
const scaleOf = async (page: Page) => Number(await page.getByTestId("notebook").getAttribute("data-scale"));

const TOP = 60;
const SECOND = 160;

test.beforeEach(async ({ page }) => {
  await spyOnQuacks(page);
  await open(page);
});

async function twoMistakes(page: Page) {
  await script({ reading: reading(sum("3 * 4 = 10"), sum("1 ÷ 2 = 2")) });
  await write(page, "3*4=10", 80, TOP);
  await page.waitForTimeout(50);
  await write(page, "1/2=2", 80, SECOND);
  await expect(count(page)).toHaveText("2");
}

test.describe("two independent mistakes", () => {
  test("are tracked apart, with one quack each", async ({ page }) => {
    await twoMistakes(page);
    await expect(choices(page)).toHaveCount(2);
    await expect(choices(page).nth(0)).toContainText("3 × 4 = 10");
    await expect(choices(page).nth(1)).toContainText("1/2 = 2");
    await settle(page, 1000);
    expect(await quacksPlayed(page)).toBe(2);
    const marks = await markers(page);
    expect(marks).toHaveLength(2);
    const [above, below] = marks.sort((a, b) => a.minY - b.minY);
    expect(above.maxY).toBeLessThan(below.minY);
  });

  test("each gets its own hint, the division a division hint", async ({ page }) => {
    await twoMistakes(page);
    await choices(page).nth(1).click();
    await page.getByTestId("hint-button").click();
    await expect(page.getByTestId("help-level-1")).toHaveText("Check that division again.");
    await expect(page.getByTestId("step-label")).toContainText("1/2 = 2");
    await page.getByTestId("mistake-choices").getByText("3 × 4 = 10").click();
    await page.getByTestId("hint-button").click();
    await expect(page.getByTestId("help-level-1")).toHaveText("Check that multiplication again.");
    // Going back shows what was already said, without asking anything.
    const before = (await stats()).helpRequests;
    await page.getByTestId("mistake-choices").getByText("1/2 = 2").click();
    await expect(page.getByTestId("help-level-1")).toHaveText("Check that division again.");
    expect((await stats()).helpRequests).toBe(before);
  });

  test("correcting one leaves the other, with no second quack", async ({ page }) => {
    await twoMistakes(page);
    await settle(page, 1000);
    const quacks = await quacksPlayed(page);
    await script({ reading: reading(sum("3 * 4 = 12"), sum("1 ÷ 2 = 2")) });
    await write(page, "2", 80 + 34 * 6, TOP);
    await expect(count(page)).toHaveText("1");
    await expect(page.getByTestId("bubble")).toHaveText("That's it.");
    await settle(page, 1000);
    expect(await quacksPlayed(page)).toBe(quacks);
    const marks = await markers(page);
    expect(marks).toHaveLength(1);
    expect(marks[0].minY * (await scaleOf(page))).toBeGreaterThan(SECOND - 30);
  });

  test("no mistake can be selected once fixed", async ({ page }) => {
    await twoMistakes(page);
    await script({ reading: reading(sum("3 * 4 = 12"), sum("1 ÷ 2 = 1/2")) });
    await write(page, "2", 80 + 34 * 6, TOP);
    await write(page, "1", 80 + 34 * 6, SECOND);
    await expect(count(page)).toHaveCount(0);
    await expect(choices(page)).toHaveCount(0);
  });
});

test("right algebra beside a wrong derivative isn't highlighted", async ({ page }) => {
  await script({
    reading: reading(
      line("2(x + 3) = 2x + 6", { continues_previous: "no", operation: "expand" }),
      line("d/dx(x^2 + 2x) = 3x + 2", { continues_previous: "unknown", operation: "differentiate" }),
    ),
  });
  await write(page, "2(x+3)=2x+6", 40, TOP);
  // Tight, as handwriting is: the next line starts just below this one.
  await write(page, "x^2+2x=3x+2", 40, TOP + 42);
  await expectDuck(page, "alert");
  const marks = await markers(page);
  expect(marks).toHaveLength(1);
  // Clear of the middle of the first line, and only one line tall.
  const scale = await scaleOf(page);
  expect(marks[0].minY * scale).toBeGreaterThan(TOP + 17);
  expect((marks[0].maxY - marks[0].minY) * scale).toBeLessThan(68);
  await page.getByTestId("hint-button").click();
  await page.getByTestId("another-hint").click();
  await page.getByTestId("explain").click();
  await expect(page.getByTestId("help-level-3")).toHaveText("The derivative of x² is 2x, not 3x. The derivative of 2x is 2, so the correct result is 2x + 2.");
});

test.describe("the highlight", () => {
  async function pixelAt(page: Page, x: number, y: number): Promise<number[]> {
    return page.evaluate(
      ([cssX, cssY]) => {
        const canvas = document.querySelector("canvas[aria-hidden]") as HTMLCanvasElement;
        const dpr = window.devicePixelRatio || 1;
        return Array.from(canvas.getContext("2d")!.getImageData(Math.round(cssX * dpr), Math.round(cssY * dpr), 1, 1).data);
      },
      [x, y],
    );
  }

  async function expectTintedAtMarker(page: Page) {
    await page.waitForTimeout(150);
    const [marker] = await markers(page);
    const { scale, scrollTop } = await page.evaluate(() => {
      const area = document.querySelector('[data-testid="notebook"]') as HTMLElement;
      return { scale: Number(area.dataset.scale), scrollTop: area.scrollTop };
    });
    // In the padding just inside the top left corner, where there's no ink.
    const inside = await pixelAt(page, (marker.minX + 3) * scale + 3, (marker.minY + 3) * scale - scrollTop + 3);
    const outside = await pixelAt(page, (marker.maxX + 40) * scale, (marker.minY + 3) * scale - scrollTop);
    expect(inside.slice(0, 3)).not.toEqual(outside.slice(0, 3));
    expect(inside[2]).toBeLessThan(outside[2]);
  }

  test("stays on its line through scrolling and resizing, top and bottom of the page", async ({ page }) => {
    await script({ reading: reading(sum("3 * 4 = 10")) });
    await write(page, "3*4=10", 80, 600);
    await expectDuck(page, "alert");
    await expectTintedAtMarker(page);

    // Each size is scrolled so the line is on screen, at a different offset each time.
    const showIt = async (offset: number) => {
      const [marker] = await markers(page);
      const scale = await scaleOf(page);
      await page.getByTestId("notebook").evaluate((area, top) => {
        area.scrollTop = top;
      }, Math.max(0, marker.minY * scale - offset));
    };

    await page.setViewportSize({ width: 1000, height: 500 });
    await showIt(100);
    await expectTintedAtMarker(page);

    await showIt(20);
    await expectTintedAtMarker(page);

    await page.setViewportSize({ width: 800, height: 900 });
    await showIt(300);
    await expectTintedAtMarker(page);
  });

  test("near the top of the page too", async ({ page }) => {
    await script({ reading: reading(sum("3 * 4 = 10")) });
    await write(page, "3*4=10", 80, 20);
    await expectDuck(page, "alert");
    await expectTintedAtMarker(page);
  });
});

test.describe("correcting a line", () => {
  test("turns its highlight pending at once, before any check answers", async ({ page }) => {
    await script({ reading: reading(sum("2 * 3 = 5")) });
    await write(page, "2*3=5", 80, TOP);
    await expectDuck(page, "alert");
    expect((await markers(page))[0].kind).toBe("selected");

    await script({ reading: reading(sum("2 * 3 = 6")), delay_ms: 1500 });
    await page.getByTestId("undo").click();
    await expect.poll(async () => (await markers(page))[0]?.kind, { timeout: 200 }).toBe("pending");
    await expect(page.getByTestId("bubble")).toHaveCount(0);
    await expectDuck(page, "idle");

    await write(page, "6", 80 + 34 * 4, TOP);
    await expectDuck(page, "resolved");
    await expect(page.getByTestId("bubble")).toHaveText("That's it.");
    expect(await markers(page)).toHaveLength(0);
  });

  test("an unclear reading doesn't claim it's fixed", async ({ page }) => {
    await script({ reading: reading(sum("2 * 3 = 5")) });
    await write(page, "2*3=5", 80, TOP);
    await expectDuck(page, "alert");
    await script({ reading: reading(sum("2 * 3 = 6", { legibility: "unsure", alternatives: ["2 * 3 = 8"] })) });
    await page.getByTestId("undo").click();
    await write(page, "6", 80 + 34 * 4, TOP);
    await settle(page, 2000);
    await expect(page.getByTestId("bubble")).toHaveCount(0);
    expect((await markers(page))[0].kind).toBe("pending");
    await expect(count(page)).toHaveText("1");
  });

  test("another wrong answer is neither a fix nor a new quack", async ({ page }) => {
    await script({ reading: reading(sum("2 * 3 = 5")) });
    await write(page, "2*3=5", 80, TOP);
    await expectDuck(page, "alert");
    await settle(page, 300);
    const quacks = await quacksPlayed(page);
    await script({ reading: reading(sum("2 * 3 = 7")) });
    await page.getByTestId("undo").click();
    await write(page, "7", 80 + 34 * 4, TOP);
    await expect(page.getByTestId("bubble")).toContainText("Not quite yet.");
    await expect(page.getByTestId("hint-button")).toBeVisible();
    await settle(page, 500);
    expect(await quacksPlayed(page)).toBe(quacks);
    await expect(count(page)).toHaveText("1");
    expect((await markers(page))[0].kind).toBe("selected");
  });

  test("edits to two lines during one check don't swap their mistakes", async ({ page }) => {
    await twoMistakes(page);
    await settle(page, 300);
    const quacks = await quacksPlayed(page);
    await script({ reading: reading(sum("3 * 4 = 12"), sum("1 ÷ 2 = 2")), delay_ms: 1200 });
    await write(page, "2", 80 + 34 * 6, TOP);
    await settle(page, 700);
    // The check is on its way. Now the other line changes too.
    await script({ reading: reading(sum("3 * 4 = 12"), sum("1 ÷ 2 = 1/2")), delay_ms: 0 });
    await write(page, "1", 80 + 34 * 6, SECOND);
    await expect(count(page)).toHaveCount(0);
    expect(await quacksPlayed(page)).toBe(quacks);
    await expect(duck(page)).not.toHaveAttribute("data-duck-state", "alert");
  });
});

test("right sums on one page stay silent", async ({ page }) => {
  await script({ reading: reading(sum("2 * 3 = 6"), sum("1 ÷ 6 = 1/6"), sum("1 ÷ 2 = 1/2")) });
  await write(page, "2*3=6", 80, TOP);
  await write(page, "1/6=1/6", 80, SECOND);
  await write(page, "1/2=1/2", 80, 260);
  await settle(page, 1800);
  expect(await quacksPlayed(page)).toBe(0);
  await expectDuck(page, "idle");
  await expect(count(page)).toHaveCount(0);
});

test.describe("reading only what changed", () => {
  test.beforeEach(async ({ page }) => {
    await page.evaluate(() => localStorage.setItem("quack.incremental", "1"));
    await page.reload();
    await expect(page.getByTestId("ink")).toBeVisible();
  });

  test("a growing page keeps sending the same small image", async ({ page }) => {
    await script({ reading: reading(sum("7 + 5 = 12")) });
    await write(page, "7+5=12", 80, TOP);
    await expect.poll(async () => (await stats()).lineCounts.length).toBe(2);
    await settle(page, 300);

    await script({ reading: reading(sum("7 + 5 = 12"), sum("2 * 3 = 6")) });
    await write(page, "2*3=6", 80, SECOND);
    await expect.poll(async () => (await stats()).lineCounts.length).toBe(4);
    await settle(page, 300);

    await script({ reading: reading(sum("2 * 3 = 6"), sum("3 * 4 = 10")) });
    await write(page, "3*4=10", 80, 260);
    await expect.poll(async () => (await stats()).lineCounts.length).toBe(6);
    expect((await stats()).lineCounts).toEqual([1, 1, 2, 2, 2, 2]);

    // Earlier lines came from memory, and the new one is the mistake.
    await expectDuck(page, "alert");
    const marks = await markers(page);
    expect(marks).toHaveLength(1);
    expect(marks[0].minY * (await scaleOf(page))).toBeGreaterThan(230);
  });

  test("correcting a line reads just that line and the one above", async ({ page }) => {
    await script({ reading: reading(sum("7 + 5 = 12")) });
    await write(page, "7+5=12", 80, TOP);
    await expect.poll(async () => (await stats()).lineCounts.length).toBe(2);
    await settle(page, 300);
    await script({ reading: reading(sum("7 + 5 = 12"), sum("2 * 3 = 5")) });
    await write(page, "2*3=5", 80, SECOND);
    await expectDuck(page, "alert");
    await settle(page, 300);

    await script({ reading: reading(sum("7 + 5 = 12"), sum("2 * 3 = 6")) });
    await page.getByTestId("undo").click();
    await write(page, "6", 80 + 34 * 4, SECOND);
    await expectDuck(page, "resolved");
    expect((await stats()).lineCounts.slice(-2)).toEqual([2, 2]);
  });

  test("undoing back to lines already read costs no reading at all", async ({ page }) => {
    await script({ reading: reading(sum("2 * 3 = 5")) });
    await write(page, "2*3=5", 80, TOP);
    await expectDuck(page, "alert");
    await settle(page, 300);
    await page.getByTestId("undo").click();
    await settle(page, 200);
    await write(page, "5", 80 + 34 * 4, TOP);
    await settle(page, 1500);
    const before = (await stats()).readingRequests;
    await page.getByTestId("undo").click();
    await page.getByTestId("redo").click();
    await settle(page, 1500);
    expect((await stats()).readingRequests).toBe(before);
    expect(await markers(page)).not.toHaveLength(0);
  });
});
