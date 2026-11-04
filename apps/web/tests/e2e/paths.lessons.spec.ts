// The five newer learning paths, one smoke test each: open the path, start its first level, see the lesson and its
// example, answer wrongly and see the mistake, answer rightly, move on, and find the progress again after a reload.
// Real browser, Next proxy, FastAPI and verifier; only Meta's API is faked, so this says nothing about how a real
// model reads handwritten fractions, degree signs or calculus.
import { randomUUID } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import { first, reading, script, write } from "./helpers";

const PASSWORD = "correct horse battery";
const SAMPLES = ["2 * 3 = 6", "1 ÷ 4 = 1/4", "x^2 + 3x", "d/dx(x^3) = 3x^2", "7x + 1 = 15", "(x + 2)^2"];

// Each level's first question is its opener, so its answer is known here.
const PATHS = [
  { course: "arithmetic_foundations", level: "arithmetic_add_subtract", intro: "Line the numbers up", prompt: "Work out 34 + 21", wrong: "54", right: "55" },
  { course: "fractions_decimals", level: "fractions_equivalent", intro: "different fractions", prompt: "Simplify 6/10", wrong: "2/5", right: "3/5" },
  { course: "geometry", level: "geometry_perimeter", intro: "distance all the way round", prompt: "A rectangle is 7 cm long and 3 cm wide", wrong: "21", right: "20" },
  { course: "trigonometry", level: "trigonometry_ratios", intro: "hypotenuse", prompt: "The side opposite A is 5", wrong: "12/13", right: "5/13" },
  { course: "calculus", level: "calculus_power_rule", intro: "rate of change", prompt: "d/dx(x³)", wrong: "3x^3", right: "3x^2" },
];

async function signUp(page: Page) {
  await page.goto("/");
  await page.getByTestId("choose-create").click();
  await page.getByTestId("email").fill(`student-${randomUUID()}@example.com`);
  await page.getByTestId("password").fill(PASSWORD);
  await page.getByTestId("account-submit").click();
  await page.getByTestId("calibration-start").click();
  for (let index = 0; index < SAMPLES.length; index++) {
    await expect(page.getByTestId("calibration-progress")).toHaveAttribute("aria-valuenow", String(index));
    await write(page, SAMPLES[index]);
    await page.getByTestId("calibration-next").click();
  }
  await page.getByTestId("calibration-finish").click();
  await page.getByTestId("choose-notebook").click();
  await expect(page.getByTestId("ink")).toBeVisible();
  await page.evaluate(() => localStorage.setItem("quack.privacy-seen", "1"));
}

async function answer(page: Page, math: string) {
  await expect(page.getByTestId("lesson").getByTestId("ink")).toBeVisible();
  await script({ reading: reading(first(math)) });
  await write(page, math.replace(/\s/g, ""), 80, 80);
}

test.beforeEach(async () => {
  await script({ reset: true });
});

test("every path is open on the catalog", async ({ page }) => {
  test.setTimeout(120_000);
  await signUp(page);
  await page.getByTestId("open-learn").click();
  for (const course of ["algebra_foundations", ...PATHS.map((path) => path.course)]) {
    await expect(page.getByTestId(`path-${course}`)).toHaveAttribute("data-status", "available");
    await expect(page.getByTestId(`path-${course}`)).not.toContainText("Coming later");
  }
  await page.screenshot({ path: "test-results/paths-0-catalog.png" });
});

for (const path of PATHS) {
  test(`${path.course}: lesson, a mistake, a right answer, the next question, and progress kept`, async ({ page }) => {
    test.setTimeout(180_000);
    await signUp(page);
    await page.getByTestId("open-learn").click();
    await page.getByTestId(`path-${path.course}`).click();
    await expect(page.getByTestId(`level-${path.level}`)).toHaveAttribute("data-status", "available");
    await page.getByTestId("level-start").click();

    // The idea and a worked example.
    await expect(page.getByTestId("lesson-intro")).toBeVisible();
    await expect(page.getByTestId("lesson-intro")).toContainText(path.intro);
    await page.screenshot({ path: `test-results/paths-${path.course}-1-intro.png` });
    await page.getByTestId("lesson-try").click();
    await expect(page.getByTestId("lesson-prompt")).toContainText(path.prompt);

    // A wrong answer is a mistake, and isn't accepted.
    await answer(page, path.wrong);
    await expect(page.getByTestId("lesson").getByTestId("bubble")).toContainText("Quack!");
    await expect(page.getByTestId("lesson-correct")).toHaveCount(0);
    await page.screenshot({ path: `test-results/paths-${path.course}-2-mistake.png` });

    // Rubbed out and answered rightly: accepted, with the assisted credit for a corrected mistake.
    await page.getByTestId("lesson-clear").click();
    await answer(page, path.right);
    await expect(page.getByTestId("lesson-correct")).toBeVisible();
    await expect(page.getByTestId("lesson-progress")).toHaveText(/^0\.5 of \d+ points$/);
    await page.screenshot({ path: `test-results/paths-${path.course}-3-correct.png` });
    const progress = await page.getByTestId("lesson-progress").innerText();

    // The next question is a different one.
    const before = await page.getByTestId("lesson").getAttribute("data-question");
    await page.getByTestId("lesson-next").click();
    await expect(page.getByTestId("lesson")).not.toHaveAttribute("data-question", before!);

    // After a reload, the progress is still there.
    await page.reload();
    await page.getByTestId("open-learn").click();
    await page.getByTestId(`path-${path.course}`).click();
    await expect(page.getByTestId(`level-${path.level}`)).not.toHaveAttribute("data-status", "locked");
    await page.getByTestId(`level-${path.level}`).click();
    await page.getByTestId("level-start").click();
    const tryIt = page.getByTestId("lesson-try");
    if (await tryIt.isVisible().catch(() => false)) await tryIt.click();
    await expect(page.getByTestId("lesson-progress")).toHaveText(progress);
  });
}
