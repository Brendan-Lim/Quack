// The iPad session of 2026-11-04, afternoon: switching between My Notebook and Learn, the first visit's choice,
// the pen and eraser's selected look, and one status at a time in a lesson. A real browser drives the real app
// with local accounts; only Meta's API is faked, so nothing here says how well a real model reads handwriting,
// and none of it has been done with an Apple Pencil.
import { randomUUID } from "node:crypto";
import { expect, test, type Browser, type Page } from "@playwright/test";
import { first, line, reading, script, stats, write } from "./helpers";

const PASSWORD = "correct horse battery";
const SAMPLES = ["2 * 3 = 6", "1 ÷ 4 = 1/4", "x^2 + 3x", "d/dx(x^3) = 3x^2", "7x + 1 = 15", "(x + 2)^2"];
const COURSE = "algebra_foundations";
const DUCK = "rgb(255, 217, 90)";

async function createAndCalibrate(page: Page): Promise<string> {
  const address = `nav-${randomUUID()}@example.com`;
  await page.goto("/");
  await page.getByTestId("choose-create").click();
  await page.getByTestId("email").fill(address);
  await page.getByTestId("password").fill(PASSWORD);
  await page.getByTestId("account-submit").click();
  await page.getByTestId("calibration-start").click();
  for (let index = 0; index < SAMPLES.length; index++) {
    await expect(page.getByTestId("calibration-progress")).toHaveAttribute("aria-valuenow", String(index));
    await write(page, SAMPLES[index]);
    await page.getByTestId("calibration-next").click();
  }
  await page.getByTestId("calibration-finish").click();
  await page.evaluate(() => localStorage.setItem("quack.privacy-seen", "1"));
  return address;
}

async function signIn(page: Page, address: string) {
  await page.goto("/");
  await page.getByTestId("choose-sign-in").click();
  await page.getByTestId("email").fill(address);
  await page.getByTestId("password").fill(PASSWORD);
  await page.getByTestId("account-submit").click();
}

const lesson = (page: Page) => page.getByTestId("lesson");

// "Simplify y + y + y + y + y": what's to simplify, and its answer, worked out as the student would.
async function question(page: Page): Promise<{ given: string; answer: string; letter: string }> {
  const prompt = (await page.getByTestId("lesson-prompt").innerText()).replace(/^Simplify\s+/, "").replace(/−/g, "-");
  const letter = prompt.match(/[a-z]/)![0];
  const count = prompt.split("+").length;
  return { given: prompt, answer: `${count}${letter}`, letter };
}
const lessonStrokes = async (page: Page) => Number(await lesson(page).getByTestId("notebook").getAttribute("data-strokes"));
const notebookStrokes = async (page: Page) => Number(await page.getByTestId("notebook").first().getAttribute("data-strokes"));
const background = (page: Page, selector: string) => page.locator(selector).evaluate((element) => getComputedStyle(element).backgroundColor);
// The selected look fades in over 140ms, so it's read once it has settled.
async function settlesTo(page: Page, selector: string, colour: string, yes = true) {
  await expect.poll(() => background(page, selector)).toBe(yes ? colour : "rgba(0, 0, 0, 0)");
}

async function startLevelOne(page: Page) {
  await page.getByTestId(`path-${COURSE}`).click();
  await page.getByTestId("level-start").click();
  await page.getByTestId("lesson-try").click();
  await expect(lesson(page).getByTestId("ink")).toBeVisible();
}

test.beforeEach(async () => {
  await script({ reset: true });
});

test.describe("a first visit", () => {
  test("asks where to start, once, and Learn starts in Learn", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 1366 });
    await createAndCalibrate(page);
    await expect(page.getByTestId("choose-mode")).toBeVisible();
    await expect(page.getByTestId("choose-notebook")).toContainText("My Notebook");
    await expect(page.getByTestId("choose-learn")).toContainText("Learn with Quack");
    await page.screenshot({ path: "test-results/nav-1-first-visit.png" });

    await page.getByTestId("choose-learn").click();
    await expect(page.getByTestId("learn")).toBeVisible();
    await expect(page.getByTestId("learn").getByTestId("mode-switch")).toHaveAttribute("data-mode", "learn");
    await expect(page.getByTestId(`path-${COURSE}`)).toBeVisible();

    // Coming back isn't a first visit.
    await page.reload();
    await expect(page.getByTestId("ink")).toBeVisible();
    await expect(page.getByTestId("choose-mode")).toHaveCount(0);
  });

  test("the notebook starts in the notebook, with the switch at the top", async ({ page }) => {
    await createAndCalibrate(page);
    await page.getByTestId("choose-notebook").click();
    await expect(page.getByTestId("ink")).toBeVisible();
    await expect(page.getByTestId("mode-switch")).toHaveAttribute("data-mode", "notebook");
    await expect(page.getByTestId("mode-notebook")).toHaveAttribute("aria-current", "page");
  });

  test("someone with a lesson under way isn't asked again on another device", async ({ page, browser }) => {
    const address = await createAndCalibrate(page);
    await page.getByTestId("choose-learn").click();
    await page.getByTestId(`path-${COURSE}`).click();
    await page.getByTestId("level-start").click();
    await expect(page.getByTestId("lesson-intro")).toBeVisible();

    const other = await freshPage(browser);
    await signIn(other, address);
    await expect(other.getByTestId("ink")).toBeVisible();
    await expect(other.getByTestId("choose-mode")).toHaveCount(0);
    await other.context().close();
  });
});

async function freshPage(browser: Browser): Promise<Page> {
  const context = await browser.newContext({ baseURL: test.info().project.use.baseURL });
  return context.newPage();
}

test.describe("switching between My Notebook and Learn", () => {
  test("keeps the notebook's ink and the lesson's working, both ways, and checks nothing extra", async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1024, height: 1366 });
    await createAndCalibrate(page);
    await page.getByTestId("choose-notebook").click();

    // Something in the notebook, never checked: watching is turned off for this.
    await page.getByTestId("monitoring").click();
    await write(page, "7", 80, 300);
    await expect.poll(() => notebookStrokes(page)).toBeGreaterThan(0);
    const notebookInk = await notebookStrokes(page);

    // The switch is on the catalog, the roadmap, the lesson's intro and its question.
    const learnSwitch = page.getByTestId("learn").getByTestId("mode-switch");
    await page.getByTestId("open-learn").click();
    await expect(learnSwitch).toHaveAttribute("data-mode", "learn");
    await page.getByTestId(`path-${COURSE}`).click();
    await expect(learnSwitch).toHaveAttribute("data-mode", "learn");
    await page.getByTestId("level-start").click();
    await expect(page.getByTestId("lesson-intro")).toBeVisible();
    await expect(learnSwitch).toHaveAttribute("data-mode", "learn");
    await page.getByTestId("lesson-try").click();
    await expect(lesson(page).getByTestId("mode-switch")).toBeVisible();
    await expect(lesson(page).getByTestId("ink")).toBeVisible();

    // Half an answer, still being read when the student leaves.
    await script({ reading: reading(first((await question(page)).given)), delay_ms: 1500 });
    await write(page, "x+x", 80, 80);
    const questionId = await lesson(page).getAttribute("data-question");
    await expect.poll(() => lessonStrokes(page)).toBeGreaterThan(0);
    const working = await lessonStrokes(page);
    await page.screenshot({ path: "test-results/nav-2-lesson-switch.png" });

    await lesson(page).getByTestId("to-notebook").click();
    await expect(page.getByTestId("learn")).toBeHidden();
    await expect(page.getByTestId("mode-switch").first()).toHaveAttribute("data-mode", "notebook");
    await expect(page.getByTestId("mode-switch").first()).toBeVisible();
    expect(await notebookStrokes(page)).toBe(notebookInk);
    const readingsAway = (await stats()).readingRequests;
    await page.waitForTimeout(2500);
    await page.screenshot({ path: "test-results/nav-3-notebook-switch.png" });

    // Back to Learn: the same question, with its working, not marked right, and nothing new was read.
    await page.getByTestId("open-learn").click();
    await expect(lesson(page)).toBeVisible();
    await expect(lesson(page)).toHaveAttribute("data-question", questionId!);
    expect(await lessonStrokes(page)).toBe(working);
    await expect(page.getByTestId("lesson-correct")).toHaveCount(0);
    expect((await stats()).readingRequests).toBeLessThanOrEqual(readingsAway + 2);

    // And the notebook's ink is still there after all of that.
    await lesson(page).getByTestId("to-notebook").click();
    expect(await notebookStrokes(page)).toBe(notebookInk);
  });

  test("the notebook's continue chip goes back to the lesson already open, with its working", async ({ page }) => {
    await createAndCalibrate(page);
    await page.getByTestId("choose-learn").click();
    await startLevelOne(page);
    await script({ reading: reading(first((await question(page)).given)) });
    await write(page, "x+x", 80, 80);
    const working = await lessonStrokes(page);
    await lesson(page).getByTestId("to-notebook").click();
    await page.getByTestId("continue-chip-open").click();
    await expect(lesson(page)).toBeVisible();
    expect(await lessonStrokes(page)).toBe(working);
  });
});

test.describe("the pen and the eraser", () => {
  async function inLesson(page: Page) {
    await createAndCalibrate(page);
    await page.getByTestId("choose-learn").click();
    await startLevelOne(page);
  }

  const pen = '[data-testid="lesson"] [aria-label="Pen"]';
  const eraser = '[data-testid="lesson"] [aria-label="Eraser"]';

  test("in a lesson, the chosen one is yellow even with the pointer still on it", async ({ page }) => {
    await inLesson(page);
    await expect(page.locator(pen)).toHaveAttribute("aria-pressed", "true");
    await settlesTo(page, pen, DUCK);

    // Clicked, and the pointer stays over it, as a finger's tap leaves :hover on an iPad.
    await page.locator(eraser).click();
    await expect(page.locator(eraser)).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator(pen)).toHaveAttribute("aria-pressed", "false");
    await settlesTo(page, eraser, DUCK);
    await settlesTo(page, pen, DUCK, false);
    await page.screenshot({ path: "test-results/nav-4-eraser-selected.png", clip: (await page.locator('[data-testid="lesson"] [role="toolbar"]').boundingBox())! });

    await page.locator(pen).click();
    await settlesTo(page, pen, DUCK);
    await settlesTo(page, eraser, DUCK, false);

    // While a check is under way, and after it.
    await script({ reading: reading(first((await question(page)).given)), delay_ms: 2000 });
    await write(page, "x+x", 80, 80);
    await page.locator(eraser).click();
    await settlesTo(page, eraser, DUCK);
    await page.locator(pen).click();
    await settlesTo(page, pen, DUCK);
    // A focus ring stays for the keyboard.
    await page.keyboard.press("Tab");
    await page.keyboard.press("Shift+Tab");
    await settlesTo(page, pen, DUCK);
  });

  test("in the notebook, the same yellow", async ({ page }) => {
    await createAndCalibrate(page);
    await page.getByTestId("choose-notebook").click();
    await settlesTo(page, '[data-testid="tool-pen"]', DUCK);
    await page.getByTestId("tool-eraser").click();
    await expect(page.getByTestId("tool-eraser")).toHaveAttribute("aria-pressed", "true");
    await settlesTo(page, '[data-testid="tool-eraser"]', DUCK);
    await settlesTo(page, '[data-testid="tool-pen"]', DUCK, false);
  });
});

test.describe("on an iPad-sized touch screen", () => {
  test.use({ viewport: { width: 1024, height: 1366 }, hasTouch: true, isMobile: true });

  test("a tapped tool is yellow, and the one before isn't", async ({ page }) => {
    await createAndCalibrate(page);
    await page.getByTestId("choose-learn").click();
    await page.getByTestId(`path-${COURSE}`).tap();
    await page.getByTestId("level-start").tap();
    await page.getByTestId("lesson-try").tap();
    const pen = '[data-testid="lesson"] [aria-label="Pen"]';
    const eraser = '[data-testid="lesson"] [aria-label="Eraser"]';
    await page.locator(eraser).tap();
    await settlesTo(page, eraser, DUCK);
    await settlesTo(page, pen, DUCK, false);
    await page.locator(pen).tap();
    await settlesTo(page, pen, DUCK);
    await settlesTo(page, eraser, DUCK, false);
    await page.screenshot({ path: "test-results/nav-5-ipad-portrait-lesson.png" });
  });
});

test.describe("one status at a time in a lesson", () => {
  test("a line Quack can't read is marked, and the note goes once the ink changes", async ({ page }) => {
    test.setTimeout(90_000);
    await createAndCalibrate(page);
    await page.getByTestId("choose-learn").click();
    await startLevelOne(page);

    // Two lines: the first read clearly, the second not, as with x = 6 beside a crossed-out 5.
    const { given, answer } = await question(page);
    expect(given).toMatch(/^[a-z]( \+ [a-z])+$/);
    await script({ reading: reading(first(given), line(`= ${answer}`, { legibility: "unsure", alternatives: [`= 3${answer}`], continues_previous: "yes" })) });
    await write(page, "x+x", 80, 80);
    await write(page, "=2x", 80, 170);
    await page.getByTestId("lesson-check").click();
    const unread = page.getByTestId("lesson-unread");
    await expect(unread).toBeVisible({ timeout: 20_000 });
    await expect(unread).toHaveAttribute("data-lines", "1");
    await expect(unread).toContainText("the marked line");
    const markers = JSON.parse((await lesson(page).getByTestId("notebook").getAttribute("data-markers"))!) as { kind: string }[];
    expect(markers.filter((marker) => marker.kind === "unread")).toHaveLength(1);
    await page.screenshot({ path: "test-results/nav-6-unread-line.png" });

    // A change: the note was about the ink before it, so it goes, and only the check of the new ink is said.
    await script({ reading: reading(first(given), line(`= ${answer}`, { continues_previous: "yes" })), delay_ms: 2500 });
    await write(page, "x", 200, 170);
    await expect(unread).toHaveCount(0);
    await expect(page.getByTestId("lesson-checking")).toBeVisible();
    await expect(unread).toHaveCount(0);
    await expect(page.getByTestId("lesson-correct")).toBeVisible({ timeout: 20_000 });
  });
});
