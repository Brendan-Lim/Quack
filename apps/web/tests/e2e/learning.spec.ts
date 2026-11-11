// My Mistakes, the recap's wording, and practice, with local development accounts and a fake model.
// None of this says how well a real model reads real handwriting, and local accounts aren't Supabase.
import { randomUUID } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import { bubble, duck, expectDuck, first, line, reading, script, settle, strokeCount, write } from "./helpers";

const PASSWORD = "correct horse battery";
const SAMPLES = ["2 * 3 = 6", "1 ÷ 4 = 1/4", "x^2 + 3x", "d/dx(x^3) = 3x^2", "7x + 1 = 15", "(x + 2)^2"];
const DEVELOPER_WORDS = /practice questions for yet|no need to invent|points to one thing|verified|unclassified|fingerprint/i;

async function signUp(page: Page): Promise<string> {
  const address = `student-${randomUUID()}@example.com`;
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
  // A first visit asks where to start. These tests start in the notebook.
  await page.getByTestId("choose-notebook").click();
  await expect(page.getByTestId("ink")).toBeVisible();
  await page.evaluate(() => localStorage.setItem("quack.privacy-seen", "1"));
  return address;
}

async function signIn(page: Page, address: string) {
  await page.goto("/");
  await page.getByTestId("choose-sign-in").click();
  await page.getByTestId("email").fill(address);
  await page.getByTestId("password").fill(PASSWORD);
  await page.getByTestId("account-submit").click();
  await expect(page.getByTestId("ink")).toBeVisible();
}

async function openMenu(page: Page, item: string) {
  await page.getByTestId("account-menu").click();
  await page.getByTestId(item).click();
}

const entries = (page: Page) => page.getByTestId("mistake-entry");

test.beforeEach(async () => {
  await script({ reset: true });
});

test.describe("the journeys", () => {
  test("chain-rule correction leads to verified follow-up practice", async ({ page }) => {
    test.setTimeout(180_000);
    await signUp(page);
    await script({ reading: reading(first("d/dx(sin(x^2)) = cos(x^2)")) });
    await write(page, "d/dx(sin(x^2)) = cos(x^2)", 80, 80, 24);
    await expectDuck(page, "alert");
    await duck(page).click();
    await expect(page.getByTestId("hint-panel")).toContainText("function inside another");
    await expect(page.getByTestId("hint-panel")).not.toContainText("2x");
    await script({ reading: reading(first("d/dx(sin(x^2)) = cos(x^2)"), first("d/dx(sin(x^2)) = 2x*cos(x^2)")) });
    await write(page, "d/dx(sin(x^2)) = 2x*cos(x^2)", 80, 160, 24);
    await expect(bubble(page)).toHaveText("That's it.");
    await settle(page, 500);
    await openMenu(page, "menu-mistakes");
    await entries(page).click();
    await expect(page.getByTestId("mistake-practise")).toBeVisible();
    await page.getByTestId("mistakes-close").click();
    await page.getByTestId("finish-session").click();
    await expect(page.getByTestId("recommendation")).toContainText("Chain rule");
    await page.getByTestId("practice-this").click();
    const prompt = page.getByTestId("practice-prompt");
    await expect(prompt).toContainText("d/dx(");
    const match = (await prompt.innerText()).match(/d\/dx\((sin|cos|exp)\((\d+)x\)\)/);
    expect(match).not.toBeNull();
    const [, family, scale] = match!;
    const outer = family === "sin" ? `cos(${scale}x)` : family === "cos" ? `-sin(${scale}x)` : `exp(${scale}x)`;
    const answer = `${scale}*(${outer})`;
    await script({ reading: reading(first("0")) });
    await write(page, "0", 80, 80);
    await expect(page.getByTestId("practice").getByTestId("bubble")).toContainText("Quack!");
    await script({ reading: reading(first(answer)) });
    await page.getByTestId("practice-clear").click();
    await write(page, answer, 80, 80, 24);
    await expect(page.getByTestId("practice-correct")).toBeVisible();
    await page.getByTestId("practice-end").click();
    await expect(page.getByTestId("summary-correct")).toHaveText("1");
    await expect(page.getByTestId("summary-message")).toContainText("One question doesn't say much");
  });

  test("A: simplifying, a hint, a correction, My Mistakes, a recap, and practice", async ({ page }) => {
    test.setTimeout(180_000);
    await signUp(page);
    await page.getByTestId("task-context").click();
    await script({ reading: reading(first("3x + 2x = 5")) });
    await write(page, "3x+2x=5", 80, 80);
    await expectDuck(page, "alert");
    await duck(page).click();
    await expect(page.getByTestId("hint-panel")).toContainText("variable");
    await expect(page.getByTestId("hint-panel")).not.toContainText("5x");

    await script({ reading: reading(first("3x + 2x = 5"), first("3x + 2x = 5x")) });
    await write(page, "3x+2x=5x", 80, 160);
    await expect(bubble(page)).toHaveText("That's it.");

    await settle(page, 500);
    await openMenu(page, "menu-mistakes");
    await expect(entries(page)).toHaveCount(1);
    await expect(entries(page)).toHaveAttribute("data-status", "corrected");
    await entries(page).click();
    await expect(page.getByTestId("mistake-written")).toContainText("3x + 2x = 5");
    await expect(page.getByTestId("mistake-corrected")).toContainText("5x");
    await expect(page.getByTestId("mistake-standing")).toContainText("with a little help");
    await expect(page.getByTestId("mistake-hints")).toContainText("variable");
    await expect(page.getByTestId("mistake-ink")).toBeVisible();
    await page.getByTestId("mistakes-close").click();

    await page.getByTestId("finish-session").click();
    await expect(page.getByTestId("recap-opening")).toHaveText("You worked through the mistake I spotted this session.");
    await expect(page.getByTestId("recap-observations")).toContainText("combining like terms mistake with a little help");
    await expect(page.getByTestId("recommendation")).toContainText("Combining like terms");
    await page.getByTestId("practice-this").click();
    await expect(page.getByTestId("practice-prompt")).toContainText("x");
  });

  test("B: an integral gets the same treatment, and practice can follow it", async ({ page }) => {
    test.setTimeout(150_000);
    await signUp(page);
    await script({ reading: reading(first("∫ x dx = 2")) });
    await write(page, "x=2", 80, 80);
    await expectDuck(page, "alert");
    await duck(page).click();
    await expect(page.getByTestId("hint-panel")).toContainText("variable");
    await script({ reading: reading(first("∫ x dx = 2"), first("∫ x dx = x^2/2 + C")) });
    await write(page, "x=x^2", 80, 160);
    await expect(bubble(page)).toHaveText("That's it.");
    await settle(page, 500);

    await page.getByTestId("finish-session").click();
    await expect(page.getByTestId("glance-found")).toHaveText("1");
    await expect(page.getByTestId("glance-corrected")).toHaveText("1");
    await expect(page.getByTestId("glance-hints")).toHaveText("1");
    await expect(page.getByTestId("recap-topics")).toContainText("Integration");
    await page.getByTestId("practice-this").click();
    const prompt = page.getByTestId("practice-prompt");
    await expect(prompt).toContainText("∫");

    // A true answer with no constant isn't wrong, and isn't finished either.
    const shown = await prompt.innerText();
    const math = shown.replace(" = ?", "");
    const answer = answerTo(math);
    await script({ reading: reading(first(answer)) });
    await write(page, "x", 80, 80);
    await expect(page.getByTestId("practice").getByTestId("bubble")).toContainText("general");
    await expect(page.getByTestId("practice-correct")).toHaveCount(0);
    await script({ reading: reading(first(`${answer} + C`)) });
    await write(page, "+x", 180, 80);
    await expect(page.getByTestId("practice-correct")).toBeVisible();
  });

  test("D: two wrong sums tracked apart, one fixed, with separate histories", async ({ page }) => {
    test.setTimeout(150_000);
    await signUp(page);
    const sum = (math: string) => line(math, { continues_previous: "no", operation: "arithmetic" });
    await script({ reading: reading(sum("3 * 4 = 10"), sum("5 + 5 = 11")) });
    await write(page, "3*4=10", 80, 80);
    await page.waitForTimeout(50);
    await write(page, "5+5=11", 80, 200);
    await expect(page.getByTestId("mistake-count")).toHaveText("2");

    await page.getByTestId("mistake-choice").nth(0).click();
    await page.getByTestId("hint-button").click();
    await expect(page.getByTestId("hint-panel")).toBeVisible();

    await script({ reading: reading(sum("3 * 4 = 10"), sum("3 * 4 = 12"), sum("5 + 5 = 11")) });
    await write(page, "3*4=12", 80, 130);
    await expect(page.getByTestId("mistake-count")).toHaveText("1");
    await settle(page, 700);

    await openMenu(page, "menu-mistakes");
    await expect(entries(page)).toHaveCount(2);
    await page.getByTestId("mistakes-filter-open").click();
    await expect(entries(page)).toHaveCount(1);
    await expect(entries(page)).toContainText("5 + 5 = 11");
    await page.getByTestId("mistakes-filter-corrected").click();
    await expect(entries(page)).toHaveCount(1);
    await expect(entries(page)).toContainText("3 × 4 = 10");
    await entries(page).click();
    await expect(page.getByTestId("mistake-hints")).toBeVisible();
    await page.getByTestId("mistake-back").click();
    await page.getByTestId("mistakes-filter-open").click();
    await entries(page).click();
    await expect(page.getByTestId("mistake-hints")).toHaveCount(0);
  });

  test("F: a returning student isn't calibrated again, and finds it all, and practice goes to the same account", async ({ page, browser }) => {
    test.setTimeout(180_000);
    const address = await signUp(page);
    await script({ reading: reading(first("d/dx(x^3) = 2x")) });
    await write(page, "d/dx(x^3)=2x", 80, 80);
    await expectDuck(page, "alert");
    await settle(page, 600);
    await page.getByTestId("finish-session").click();
    await expect(page.getByTestId("recap")).toBeVisible();
    await page.getByTestId("recap-back").click();
    await settle(page, 300);

    const other = await browser.newContext();
    const tablet = await other.newPage();
    await signIn(tablet, address);
    await expect(tablet.getByTestId("calibration-intro")).toHaveCount(0);
    await openMenu(tablet, "menu-history");
    await expect(tablet.getByTestId("history-item")).toHaveCount(1);
    await tablet.getByTestId("history-back").click();
    await openMenu(tablet, "menu-mistakes");
    await expect(entries(tablet)).toHaveCount(1);
    await entries(tablet).click();
    // The notes aren't on this device, so nor is the picture of them. The rest follows the account.
    await expect(tablet.getByTestId("mistake-ink")).toHaveCount(0);
    await expect(tablet.getByTestId("mistake-show")).toHaveCount(0);
    await tablet.getByTestId("mistake-reveal").click();
    await expect(tablet.getByTestId("mistake-corrected")).toContainText("3x²");
    await tablet.getByTestId("mistake-practise").click();
    await expect(tablet.getByTestId("practice-prompt")).toContainText("d/dx");
    await other.close();
  });
});

function answerTo(math: string): string {
  // ∫ 3x² dx and so on, from the template, whose answers come out whole.
  const match = math.match(/∫\s*(\d*)x([²³⁴]?)\s*dx/)!;
  const coefficient = Number(match[1] || 1);
  const power = { "": 1, "²": 2, "³": 3, "⁴": 4 }[match[2]]!;
  const scale = coefficient / (power + 1);
  return `${scale === 1 ? "" : scale}x^${power + 1}`;
}

test.describe("my mistakes", () => {
  test("empty, it says so without making anything up", async ({ page }) => {
    await signUp(page);
    await openMenu(page, "menu-mistakes");
    await expect(page.getByTestId("mistakes-empty")).toContainText("Nothing here yet");
  });

  test("right working and an ambiguous line stay out of it", async ({ page }) => {
    await signUp(page);
    await script({ reading: reading(first("2 + 3 = 5"), first("3x + 2x = 5")) });
    await write(page, "2+3=5", 80, 80);
    await write(page, "3x+2x=5", 80, 160);
    await settle(page, 1200);
    await openMenu(page, "menu-mistakes");
    await expect(page.getByTestId("mistakes-empty")).toBeVisible();
  });

  test("a line read wrongly and then read rightly never counted", async ({ page }) => {
    await signUp(page);
    await script({ reading: reading(first("3 * 4 = 18")) });
    await write(page, "3*4=18", 80, 80);
    await expectDuck(page, "alert");
    // Asked directly, both readings now say it's what it should be, with no change to the ink.
    await script({ reading: reading(first("3 * 4 = 12")) });
    await page.getByTestId("check").click();
    await expect(bubble(page)).toHaveText("That's it.");
    await settle(page, 600);
    await openMenu(page, "menu-mistakes");
    await expect(page.getByTestId("mistakes-empty")).toBeVisible();
  });

  test("filters by topic, opens one, and can show it on the page", async ({ page }) => {
    test.setTimeout(120_000);
    await signUp(page);
    await script({ reading: reading(first("3 * 4 = 10"), first("d/dx(x^3) = 2x")) });
    await write(page, "3*4=10", 80, 80);
    await page.waitForTimeout(50);
    await write(page, "d/dx(x^3)=2x", 80, 400);
    await expect(page.getByTestId("mistake-count")).toHaveText("2");
    await settle(page, 600);
    await page.getByTestId("notebook").evaluate((area) => {
      area.scrollTop = 0;
    });
    await openMenu(page, "menu-mistakes");
    await page.getByTestId("mistakes-topic-multiplication").click();
    await expect(entries(page)).toHaveCount(1);
    await page.getByTestId("mistakes-topic-all").click();
    await expect(entries(page)).toHaveCount(2);
    await entries(page).first().click();
    await page.getByTestId("mistake-show").click();
    await expect(page.getByTestId("mistakes")).toHaveCount(0);
    await expect(page.getByTestId("ink")).toBeVisible();
  });

  test("an open one keeps its answer back until asked", async ({ page }) => {
    await signUp(page);
    await script({ reading: reading(first("3 * 4 = 10")) });
    await write(page, "3*4=10", 80, 80);
    await expectDuck(page, "alert");
    await settle(page, 600);
    await openMenu(page, "menu-mistakes");
    await entries(page).click();
    await expect(page.getByTestId("mistake-corrected")).toHaveCount(0);
    await page.getByTestId("mistake-reveal").click();
    await expect(page.getByTestId("mistake-corrected")).toContainText("12");
  });

  test("one can be removed, and notes aren't touched", async ({ page }) => {
    await signUp(page);
    await script({ reading: reading(first("3 * 4 = 10")) });
    await write(page, "3*4=10", 80, 80);
    await expectDuck(page, "alert");
    await settle(page, 600);
    const strokes = await strokeCount(page);
    await openMenu(page, "menu-mistakes");
    await entries(page).click();
    await page.getByTestId("mistake-delete").click();
    await page.getByTestId("mistake-delete-confirm").click();
    await expect(page.getByTestId("mistakes-empty")).toBeVisible();
    await page.getByTestId("mistakes-close").click();
    expect(await strokeCount(page)).toBe(strokes);
  });

  test("another account sees none of it", async ({ page }) => {
    test.setTimeout(150_000);
    await signUp(page);
    await script({ reading: reading(first("3 * 4 = 10")) });
    await write(page, "3*4=10", 80, 80);
    await expectDuck(page, "alert");
    await settle(page, 600);
    await page.getByTestId("account-menu").click();
    await page.getByTestId("menu-sign-out").click();
    await signUp(page);
    await openMenu(page, "menu-mistakes");
    await expect(page.getByTestId("mistakes-empty")).toBeVisible();
  });

  test("opens with no connection, from what's on the device", async ({ page }) => {
    await signUp(page);
    await script({ reading: reading(first("3 * 4 = 10")) });
    await write(page, "3*4=10", 80, 80);
    await expectDuck(page, "alert");
    await settle(page, 600);
    await openMenu(page, "menu-mistakes");
    await expect(entries(page)).toHaveCount(1);
    await page.getByTestId("mistakes-close").click();
    await page.route("**/api/mistakes**", (route) => route.abort());
    await openMenu(page, "menu-mistakes");
    await expect(page.getByTestId("mistakes-offline")).toBeVisible();
    await expect(entries(page)).toHaveCount(1);
  });

  test("a recap goes straight to its own", async ({ page }) => {
    await signUp(page);
    await script({ reading: reading(first("3 * 4 = 10")) });
    await write(page, "3*4=10", 80, 80);
    await expectDuck(page, "alert");
    await settle(page, 600);
    await page.getByTestId("finish-session").click();
    await page.getByTestId("review-mistakes").click();
    await expect(page.getByTestId("mistakes-from-session")).toBeVisible();
    await expect(entries(page)).toHaveCount(1);
  });
});

test.describe("the recap's wording", () => {
  test("empty, it's friendly and claims nothing", async ({ page }) => {
    await signUp(page);
    await page.getByTestId("finish-session").click();
    await expect(page.getByTestId("recap-opening")).toHaveText("We didn't check any working this time.");
    await expect(page.getByTestId("recap-observations")).toHaveCount(0);
    expect(await page.getByTestId("recap").innerText()).not.toMatch(DEVELOPER_WORDS);
  });

  test("an adding mistake leads to real practice, and it's still open", async ({ page }) => {
    await signUp(page);
    await script({ reading: reading(first("12 + 5 = 18")) });
    await write(page, "12+5=18", 80, 80);
    await expectDuck(page, "alert");
    await settle(page, 600);
    await page.getByTestId("finish-session").click();
    const text = await page.getByTestId("recap").innerText();
    expect(text).not.toMatch(DEVELOPER_WORDS);
    expect(text).not.toMatch(/weak|struggl/i);
    await expect(page.getByTestId("glance-unresolved")).toHaveText("1");
    await expect(page.getByTestId("recap-left-open")).toContainText("still open");
    await page.getByTestId("practice-this").click();
    await expect(page.getByTestId("practice-prompt")).toHaveText(/^\d+ [+-] \d+ = \?$/);
  });

  test("what has no questions of its own says something real", async ({ page }) => {
    await signUp(page);
    await script({ reading: reading(line("x^2 * x^3 = x^6", { continues_previous: "no", operation: "simplify" })) });
    await write(page, "x^2*x^3=x^6", 80, 80);
    await expectDuck(page, "alert");
    await settle(page, 600);
    await page.getByTestId("finish-session").click();
    await expect(page.getByTestId("recap-note")).toHaveText("Want to keep going? Try a quick practice question with me.");
    await page.getByTestId("general-practice").click();
    await expect(page.getByTestId("practice-topic-integration")).toBeVisible();
  });
});

test.describe("tablet layouts", () => {
  for (const size of [{ width: 1180, height: 820 }, { width: 820, height: 1180 }]) {
    test(`a long name fits at ${size.width} wide, with nothing running off`, async ({ browser }) => {
      test.setTimeout(150_000);
      const context = await browser.newContext({ viewport: size, hasTouch: true });
      const page = await context.newPage();
      await signUp(page);
      await script({ reading: reading(first("d/dx(x^5 + 3x^4 + 2x^3 + 7x^2 + 4x + 12) = 5x^4 + 12x^3 + 6x^2 + 14x + 40")) });
      await write(page, "d/dx(x^3)=2x", 80, 80);
      await expectDuck(page, "alert");
      await settle(page, 600);
      await page.getByTestId("finish-session").click();
      const fits = () => page.evaluate(() => [...document.querySelectorAll("[data-testid='recap'], [data-testid='mistakes'], main")].every((el) => el.scrollWidth <= el.clientWidth + 1));
      await expect(page.getByTestId("recap")).toBeVisible();
      expect(await fits()).toBe(true);
      await page.screenshot({ path: `test-results/recap-${size.width}.png`, fullPage: true });
      await page.getByTestId("review-mistakes").click();
      await expect(entries(page)).toHaveCount(1);
      expect(await fits()).toBe(true);
      await page.screenshot({ path: `test-results/mistakes-${size.width}.png`, fullPage: true });
      await context.close();
    });
  }
});
