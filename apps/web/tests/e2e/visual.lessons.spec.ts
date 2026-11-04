// Screenshots of the student's journey at each size, for design review. Not a correctness test: it only
// runs with QUACK_SCREENSHOTS=1, and writes to QUACK_SHOT_DIR (default test-results/visual). The model is
// the fake one, so nothing here says anything about reading real handwriting.
//
//   QUACK_SCREENSHOTS=1 QUACK_SHOT_DIR=/tmp/shots/after QUACK_E2E_PORT_SHIFT=2000 \
//     npx playwright test visual.lessons --project=accounts
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { first, line, reading, script, write } from "./helpers";

const DIR = process.env.QUACK_SHOT_DIR ?? "test-results/visual";
const PASSWORD = "correct horse battery";
const SAMPLES = ["2 * 3 = 6", "1 ÷ 4 = 1/4", "x^2 + 3x", "d/dx(x^3) = 3x^2", "7x + 1 = 15", "(x + 2)^2"];
const COURSE = "algebra_foundations";

type Size = { name: string; width: number; height: number };
// The iPad in the recordings is 810 × 1080 points; Safari's bars leave about 976 of it for the page.
const TOUCH: Size[] = [
  { name: "ipad-portrait", width: 810, height: 976 },
  { name: "tablet-landscape", width: 1024, height: 768 },
  { name: "mobile", width: 390, height: 844 },
];
const POINTER: Size[] = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "large", width: 1920, height: 1440 },
];

test.skip(!process.env.QUACK_SCREENSHOTS, "screenshots only on request");
test.beforeAll(() => mkdirSync(DIR, { recursive: true }));
test.beforeEach(async () => {
  await script({ reset: true });
});

async function shoot(page: Page, sizes: Size[], name: string, settleMs = 450) {
  const original = page.viewportSize();
  for (const size of sizes) {
    await page.setViewportSize({ width: size.width, height: size.height });
    await page.waitForTimeout(settleMs);
    await page.screenshot({ path: join(DIR, `${size.name}-${name}.png`) });
  }
  if (original) await page.setViewportSize(original);
  await page.waitForTimeout(150);
}

const prompt = (page: Page) => page.getByTestId("lesson-prompt");
const drawable = (math: string) => math.replace(/\s/g, "").replace(/[a-wyz]/g, "x");

function simplified(question: string): string {
  const terms = question.replace(/^Simplify\s+/, "").replace(/−/g, "-").replace(/\s+/g, "").split(/(?=[+-])/);
  const totals = new Map<string, number>();
  for (const term of terms) {
    const [, sign, digits, letter] = term.match(/^([+-]?)(\d*)([a-z]?)$/)!;
    const size = digits ? Number(digits) : 1;
    totals.set(letter, (totals.get(letter) ?? 0) + (sign === "-" ? -size : size));
  }
  const parts = [...totals.entries()].filter(([, value]) => value !== 0).sort(([a], [b]) => (a === "" ? 1 : b === "" ? -1 : 0));
  return parts
    .map(([letter, value], index) => {
      const size = Math.abs(value);
      const body = letter ? `${size === 1 ? "" : size}${letter}` : String(size);
      if (index === 0) return value < 0 ? `-${body}` : body;
      return value < 0 ? ` - ${body}` : ` + ${body}`;
    })
    .join("");
}

async function answer(page: Page, math: string, y = 80) {
  await expect(page.getByTestId("lesson").getByTestId("ink")).toBeVisible();
  await script({ reading: reading(first(math)) });
  await write(page, drawable(math), 80, y);
}

async function journey(page: Page, sizes: Size[]) {
  const main = sizes[0];
  await page.setViewportSize({ width: main.width, height: main.height });

  await page.goto("/");
  await expect(page.getByTestId("choose-create")).toBeVisible();
  await shoot(page, sizes, "00-welcome");
  await page.getByTestId("choose-create").click();
  await page.getByTestId("email").fill(`student-${randomUUID()}@example.com`);
  await page.getByTestId("password").fill(PASSWORD);
  await shoot(page, sizes, "01-sign-up");
  await page.getByTestId("account-submit").click();
  await page.getByTestId("calibration-start").click();
  for (let index = 0; index < SAMPLES.length; index++) {
    await expect(page.getByTestId("calibration-progress")).toHaveAttribute("aria-valuenow", String(index));
    await write(page, SAMPLES[index]);
    if (index === 1) await shoot(page, sizes, "02-calibration");
    await page.getByTestId("calibration-next").click();
  }
  await page.getByTestId("calibration-finish").click();
  // A first visit asks where to start. This journey starts in the notebook.
  await expect(page.getByTestId("choose-mode")).toBeVisible();
  await shoot(page, sizes, "02b-choose");
  await page.getByTestId("choose-notebook").click();
  await expect(page.getByTestId("ink")).toBeVisible();
  await page.evaluate(() => localStorage.setItem("quack.privacy-seen", "1"));
  await page.reload();
  await expect(page.getByTestId("mode-switch")).toBeVisible();
  await shoot(page, sizes, "02c-notebook-switch");

  // The notebook, with a slip on it.
  await script({ reading: reading(first("3 × 4 = 10")) });
  await write(page, "3*4=10", 120, 140);
  await expect(page.getByTestId("bubble")).toContainText("Quack!");
  await shoot(page, sizes, "03-notebook-mistake");
  await page.getByTestId("duck").click();
  await expect(page.getByTestId("hint-panel")).toBeVisible();
  await shoot(page, sizes, "04-notebook-hint");
  await page.getByTestId("panel-close").click();

  await page.getByTestId("open-learn").click();
  await expect(page.getByTestId(`path-${COURSE}`)).toBeVisible();
  await shoot(page, sizes, "05-catalog");
  await page.getByTestId(`path-${COURSE}`).click();
  await expect(page.getByTestId("roadmap")).toBeVisible();
  await shoot(page, sizes, "06-roadmap");
  await page.getByTestId("level-start").click();
  await expect(page.getByTestId("lesson-intro")).toBeVisible();
  await page.waitForTimeout(1800);
  await shoot(page, sizes, "07-intro");
  await page.getByTestId("lesson-try").click();
  await expect(prompt(page)).toBeVisible();
  await shoot(page, sizes, "08-question");
  // The eraser chosen, and still yellow with the pointer on it, then the pen again.
  await page.getByTestId("lesson").getByRole("button", { name: "Eraser", exact: true }).click();
  await page.waitForTimeout(300);
  await shoot(page, sizes.slice(0, 1), "08b-eraser-selected");
  await page.getByTestId("lesson").getByRole("button", { name: "Pen", exact: true }).click();

  // Checking, held open by a slow model.
  const right = simplified(await prompt(page).innerText());
  await script({ reading: reading(first(right)), delay_ms: 6000 });
  await write(page, drawable(right), 80, 80);
  await expect(page.getByTestId("lesson-checking")).toBeVisible({ timeout: 8000 });
  await shoot(page, sizes.slice(0, 1), "09-checking", 200);
  await script({ delay_ms: 0 });
  await expect(page.getByTestId("lesson-correct")).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(250);
  await page.screenshot({ path: join(DIR, `${main.name}-10-correct-moment.png`) });
  await page.waitForTimeout(2600);
  await shoot(page, sizes, "11-correct");

  for (let question = 0; question < 5 && !(await page.getByTestId("level-mastered").isVisible()); question++) {
    const before = await page.getByTestId("lesson").getAttribute("data-question");
    await page.getByTestId("lesson-next").click();
    await expect(page.getByTestId("lesson")).not.toHaveAttribute("data-question", before!);
    await answer(page, simplified(await prompt(page).innerText()));
    await expect(page.getByTestId("lesson-correct")).toBeVisible();
    await page.waitForTimeout(500);
  }
  await expect(page.getByTestId("level-mastered")).toBeVisible();
  await page.waitForTimeout(400);
  await page.screenshot({ path: join(DIR, `${main.name}-12-mastered-moment.png`) });
  await page.waitForTimeout(2200);
  await shoot(page, sizes, "13-mastered");
  await page.getByTestId("mastered-roadmap").click();
  await expect(page.getByTestId("roadmap")).toBeVisible();
  await page.waitForTimeout(600);
  await shoot(page, sizes, "14-roadmap-after");

  // A mistake in a lesson, its hint, and its fix.
  await page.getByTestId("level-combine_like_terms").click();
  await page.getByTestId("level-start").click();
  await page.getByTestId("lesson-try").click();
  await expect(prompt(page)).toHaveText("Simplify 3x + 2x");
  await answer(page, "5");
  await expect(page.getByTestId("lesson").getByTestId("bubble")).toContainText("Quack!");
  await page.waitForTimeout(600);
  await shoot(page, sizes, "15-lesson-mistake");
  await page.getByTestId("lesson").getByTestId("duck").click();
  await expect(page.getByTestId("hint-panel")).toBeVisible();
  await shoot(page, sizes, "16-lesson-hint");
  await script({ reading: reading(first("5x")) });
  await write(page, "x", 110, 80);
  await expect(page.getByTestId("lesson-correct")).toBeVisible();
  await page.waitForTimeout(2600);
  await shoot(page, sizes, "17-lesson-fixed");
  const before = await page.getByTestId("lesson").getAttribute("data-question");
  await page.getByTestId("lesson-next").click();
  await expect(page.getByTestId("lesson")).not.toHaveAttribute("data-question", before!);
  await shoot(page, sizes, "18-adaptive");
  await page.getByTestId("lesson-progress").click();
  await expect(page.getByTestId("points-explained")).toBeVisible();
  await shoot(page, sizes.slice(0, 1), "19-points");
  await page.getByTestId("lesson-progress").click();

  // Can't read it yet.
  await script({ reading: reading(line("4x", { continues_previous: "no", legibility: "unsure", alternatives: ["4n"] })) });
  await write(page, "4x", 80, 80);
  await page.waitForTimeout(1500);
  await page.getByTestId("lesson-check").click();
  await page.waitForTimeout(2500);
  await shoot(page, sizes.slice(0, 1), "20-unread");

  // Returning: Learn home with something to continue, then My Mistakes.
  await page.getByTestId("lesson-back").click();
  await page.getByTestId("learn-home").click();
  await expect(page.getByTestId("continue-learning")).toBeVisible();
  await shoot(page, sizes, "21-catalog-returning");
  await page.getByTestId("to-notebook").click();
  await page.getByTestId("account-menu").click();
  await shoot(page, sizes.slice(0, 1), "22-account-menu");
  await page.getByTestId("menu-mistakes").click();
  await expect(page.getByTestId("mistake-entry").first()).toBeVisible();
  await shoot(page, sizes, "23-mistakes");
  await page.getByTestId("mistake-entry").first().click();
  await page.waitForTimeout(400);
  await shoot(page, sizes, "24-mistake-detail");
}

test.describe("touch sizes", () => {
  test.use({ hasTouch: true });
  test("the journey on tablets and phones", async ({ page }) => {
    test.setTimeout(600_000);
    await journey(page, TOUCH);
  });
});

test("the journey on desktops", async ({ page }) => {
  test.setTimeout(600_000);
  await journey(page, POINTER);
});
