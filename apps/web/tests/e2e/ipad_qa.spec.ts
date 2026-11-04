// The physical iPad session of 2026-11-02, as sequences, with only Meta's API faked. These show the
// browser, proxy, API, verifier and state handling what the model is scripted to say. They say nothing
// about whether the real model can read real Apple Pencil ink. That's still to be tested on an iPad.
import { expect, test, type Page } from "@playwright/test";
import { bubble, expectDuck, line, open, quacksPlayed, reading, script, settle, spyOnQuacks, stats, strokeCount, write } from "./helpers";

type Marker = { id: string; kind: string; minX: number; minY: number; maxX: number; maxY: number };

const calculus = (math: string, overrides = {}) => line(math, { continues_previous: "no", ...overrides });
const sum = (math: string) => line(math, { continues_previous: "no", operation: "arithmetic" });
const markers = async (page: Page): Promise<Marker[]> => JSON.parse((await page.getByTestId("notebook").getAttribute("data-markers")) ?? "[]");
const count = (page: Page) => page.getByTestId("mistake-count");
const choices = (page: Page) => page.getByTestId("mistake-choice");
const panel = (page: Page) => page.getByTestId("ask-panel");
const reply = (page: Page) => page.getByTestId("ask-reply");

// Handwritten in the fake's stroke font. What the fake says it reads is what's scripted.
const DERIVATIVE = "d/dx(x^3)=3x^2";
const TOP = 60;

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

test.describe("automatic checking needs nothing more to be written", () => {
  for (const math of ["d/dx(x^3) = 3x^2 + C", "∫ x dx = 2", "d/dx(sin(x)) = sin(x)", "d/dx(sin x) = sin x"]) {
    test(`${math}, alone on the page, with the second reading running out of tokens`, async ({ page }) => {
      await script({ reading: reading(calculus(math)), second_reading: "truncated" });
      await write(page, "x=2", 80, TOP);
      await expect(count(page)).toHaveText("1");
      expect(await strokeCount(page)).toBeGreaterThan(0);
      await expect(page.getByTestId("issue")).toHaveCount(0);
      expect(await markers(page)).toHaveLength(1);
      const asked = await stats();
      expect(asked.efforts.filter((effort) => effort === "low")).toHaveLength(1);
      expect(asked.efforts.filter((effort) => effort === "minimal")).toHaveLength(2);
    });
  }

  test("with one reading vaguely unsure", async ({ page }) => {
    await script({ queue: [reading(calculus("∫ x dx = 2")), reading(calculus("∫ x dx = 2", { legibility: "unsure" }))] });
    await write(page, "x=2", 80, TOP);
    await expect(count(page)).toHaveText("1");
  });

  test("several lines, each flagged on its own, with nothing unrelated added", async ({ page }) => {
    await script({ reading: reading(calculus("∫ x dx = 2")) });
    await write(page, "x=2", 80, TOP);
    await expect(count(page)).toHaveText("1");
    await script({ reading: reading(calculus("∫ x dx = 2"), calculus("d/dx(sin(x)) = sin(x)")) });
    await write(page, "s=s", 80, 200);
    await expect(count(page)).toHaveText("2");
    expect(await markers(page)).toHaveLength(2);
    expect(await quacksPlayed(page)).toBeGreaterThan(0);
  });

  test("a line both readings can't settle is looked at again on its own, up to its limit", async ({ page }) => {
    await script({
      queue: [
        reading(calculus("∫ x dx = 2")),
        reading(calculus("∫ x dx = 2x")),
        reading(calculus("∫ x dx = 2")),
        reading(calculus("∫ x dx = 2")),
      ],
    });
    await write(page, "x=2", 80, TOP);
    await expect(count(page)).toHaveText("1", { timeout: 8000 });
  });

  test("when nothing can read it, it says so, tries a bounded number of times, and accuses nobody", async ({ page }) => {
    await script({ reading: reading(calculus("∫ x dx = 2")), second_reading: "truncated", queue: [reading(calculus("∫ x dx = 2")), reading(calculus("∫ x dx = 2x"))] });
    await write(page, "x=2", 80, TOP);
    await settle(page, 1500);
    await expect(count(page)).toHaveCount(0);
  });
});

test.describe("editing a flagged line", () => {
  async function flagged(page: Page, math = "2 * 3 = 5") {
    await script({ reading: reading(sum(math)) });
    await write(page, "2*3=5", 80, TOP);
    await expectDuck(page, "alert");
    await expect(count(page)).toHaveText("1");
  }

  test("A: wrong to right resolves it once, and takes the highlight with it", async ({ page }) => {
    await flagged(page);
    await script({ reading: reading(sum("2 * 3 = 6")) });
    await page.getByTestId("undo").click();
    await write(page, "6", 80 + 34 * 4, TOP);
    await expectDuck(page, "resolved");
    await expect(bubble(page)).toHaveText("That's it.");
    expect(await markers(page)).toHaveLength(0);
    await expect(count(page)).toHaveCount(0);
  });

  test("B: wrong to a different wrong: new feedback, nothing kept from before, and one open mistake", async ({ page }) => {
    await flagged(page);
    await page.getByTestId("hint-button").click();
    await expect(page.getByTestId("help-level-1")).toBeVisible();
    await script({ reading: reading(sum("2 * 3 = 7")) });
    await page.getByTestId("undo").click();
    await write(page, "7", 80 + 34 * 4, TOP);
    await expect(bubble(page)).toContainText("Not quite yet.");
    await expect(page.getByTestId("help-level-1")).toHaveCount(0);
    await expect(count(page)).toHaveText("1");
    const quacks = await quacksPlayed(page);
    await page.getByTestId("hint-button").click();
    await expect(page.getByTestId("step-label")).toContainText("7");
    await expect(page.getByTestId("step-label")).not.toContainText("Before");
    expect(await quacksPlayed(page)).toBe(quacks);
  });

  test("while it's being checked, old help is marked as being about what was there, with nothing to act on", async ({ page }) => {
    await flagged(page);
    await page.getByTestId("hint-button").click();
    await expect(page.getByTestId("help-level-1")).toBeVisible();
    await script({ reading: reading(sum("2 * 3 = 6")), delay_ms: 1500 });
    await page.getByTestId("undo").click();
    await expect(page.getByTestId("pending")).toHaveText("Checking your change…");
    await expect(page.getByTestId("step-label")).toContainText("Before your change");
    await expect(page.getByTestId("another-hint")).toHaveCount(0);
    await expect(page.getByTestId("explain")).toHaveCount(0);
    await expect(page.getByTestId("question")).toHaveCount(0);
  });

  test("D: rubbing out the answer is no fix", async ({ page }) => {
    await flagged(page);
    await script({ reading: reading(line("2 * 3 =", { kind: "incomplete", continues_previous: "no", operation: "arithmetic" })) });
    await page.getByTestId("undo").click();
    await settle(page, 1600);
    await expect(page.getByTestId("bubble")).toHaveCount(0);
    await expect(count(page)).toHaveText("1");
    expect((await markers(page))[0].kind).toBe("pending");
  });

  test("F: a new problem nearby isn't taken for the old one", async ({ page }) => {
    await flagged(page);
    await script({ reading: reading(sum("2 * 3 = 5"), sum("1 + 1 = 3")) });
    await write(page, "1+1=3", 80, 180);
    await expect(count(page)).toHaveText("2");
  });

  test("G: an answer about the old ink can't bring its mistake back after the edit", async ({ page }) => {
    await script({ reading: reading(sum("2 * 3 = 5")), delay_ms: 900 });
    await write(page, "2*3=5", 80, TOP);
    await settle(page, 500);
    await script({ reading: reading(sum("2 * 3 = 6")), delay_ms: 0 });
    await write(page, "6", 80 + 34 * 5, TOP);
    await settle(page, 1800);
    await expect(count(page)).toHaveCount(0);
    expect(await markers(page)).toHaveLength(0);
  });

  test("H: undoing a fix brings the mistake back with no second quack, and redo clears it again", async ({ page }) => {
    await flagged(page);
    const quacks = await quacksPlayed(page);
    await script({ reading: reading(sum("2 * 3 = 5"), sum("2 * 3 = 6")) });
    await script({ reading: reading(sum("2 * 3 = 6")) });
    await page.getByTestId("undo").click();
    await write(page, "6", 80 + 34 * 4, TOP);
    await expectDuck(page, "resolved");
    await settle(page, 300);
    await script({ reading: reading(sum("2 * 3 = 5")) });
    await page.getByTestId("undo").click();
    await page.getByTestId("undo").click();
    await settle(page, 200);
    await expect(count(page)).toHaveCount(0);
    expect(await quacksPlayed(page)).toBe(quacks);
  });

  test("a chip for an edited line doesn't show the old maths as what's there", async ({ page }) => {
    await script({ reading: reading(sum("2 * 3 = 5"), sum("1 + 1 = 3")) });
    await write(page, "2*3=5", 80, TOP);
    await write(page, "1+1=3", 80, 180);
    await expect(count(page)).toHaveText("2");
    await script({ reading: reading(sum("2 * 3 = 5"), sum("1 + 1 = 3")), delay_ms: 1200 });
    await write(page, "4", 80 + 34 * 6, TOP);
    await page.getByTestId("duck").click();
    await expect(choices(page).filter({ hasText: "Your change" })).toHaveCount(1);
  });
});

test.describe("circling", () => {
  test("Explain on the correct integral teaches it, and Check says whether it holds", async ({ page }) => {
    await page.getByTestId("monitoring").click();
    await write(page, DERIVATIVE, 80, TOP);
    await settle(page);
    await script({ reading: reading(calculus("∫ x dx = x^2/2 + C")) });
    await circle(page, 60, 30, 720, 120);
    await expect(page.getByTestId("ask-hint")).toHaveCount(0);
    await page.getByTestId("ask-explain").click();
    // Numbered steps since the second iPad session.
    await expect(reply(page)).toContainText("Raise the power by one");
    await expect(reply(page)).toContainText("+ C");
    await expect(reply(page)).not.toContainText("holds up");
    await expect(page.getByTestId("ask-explain")).toHaveCount(0);
    await expect(count(page)).toHaveCount(0);
  });

  test("a check saying something's wrong offers a first hint, then another, then the explanation", async ({ page }) => {
    await page.getByTestId("monitoring").click();
    await write(page, DERIVATIVE, 80, TOP);
    await settle(page);
    await script({ reading: reading(calculus("d/dx(sin(x)) = sin(x)")) });
    await circle(page, 60, 30, 720, 120);
    await page.getByTestId("ask-check").click();
    await expect(reply(page)).toHaveAttribute("data-verdict", "incorrect");
    await expect(page.getByTestId("ask-hint")).toHaveText("Give me a hint");
    await page.getByTestId("ask-hint").click();
    await expect(reply(page)).toHaveText("Which derivative rule applies to sin(x)?");
    await expect(page.getByTestId("ask-hint")).toHaveText("Another hint");
    await page.getByTestId("ask-hint").click();
    await expect(reply(page)).toHaveText("Think about how the slope of the sine curve relates to cosine.");
    await expect(page.getByTestId("ask-hint")).toHaveCount(0);
    await page.getByTestId("ask-explain").click();
    await expect(reply(page)).toContainText("cos(x), not sin(x)");
    await expect(page.getByTestId("ask-explain")).toHaveCount(0);
  });

  test("a mistake already open offers a hint first, labelled by what's been seen, and its alert stays one alert", async ({ page }) => {
    await script({ reading: reading(sum("2 * 3 = 5")) });
    await write(page, "2*3=5", 80, TOP);
    await expect(count(page)).toHaveText("1");
    await circle(page, 60, 30, 400, 120);
    await expect(page.getByTestId("ask-hint")).toHaveText("Give me a hint");
    await expect(page.getByTestId("ask-check")).toHaveCount(0);
    await page.getByTestId("ask-hint").click();
    await expect(page.getByTestId("help-level-1")).toBeVisible();
    await expect(panel(page)).toHaveCount(0);
    await circle(page, 60, 30, 400, 120);
    await expect(page.getByTestId("ask-hint")).toHaveText("Another hint");
    await expect(count(page)).toHaveText("1");
  });

  test("working already checked as right isn't offered a check or a hint", async ({ page }) => {
    await script({ reading: reading(sum("2 + 3 = 5")) });
    await write(page, "2+3=5", 80, TOP);
    await settle(page, 1200);
    await circle(page, 60, 30, 400, 120);
    await expect(page.getByTestId("ask-explain")).toBeVisible();
    await expect(page.getByTestId("ask-check")).toHaveCount(0);
    await expect(page.getByTestId("ask-hint")).toHaveCount(0);
  });
});
