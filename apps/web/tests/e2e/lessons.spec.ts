// Learning paths and lessons, end to end: real browser, Next proxy, FastAPI, verifier, classifier and
// policy, with local development accounts. Only Meta's API is faked, so none of this says how well a
// real model reads real handwriting, and local accounts aren't Supabase.
import { randomUUID } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import { bubble, first, line, quacksPlayed, reading, script, spyOnQuacks, write } from "./helpers";

const PASSWORD = "correct horse battery";
const SAMPLES = ["2 * 3 = 6", "1 ÷ 4 = 1/4", "x^2 + 3x", "d/dx(x^3) = 3x^2", "7x + 1 = 15", "(x + 2)^2"];
const COURSE = "algebra_foundations";

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

// Development-only route, on local test accounts: the levels before this one count as moved on.
async function unlockUpTo(page: Page, levelId: string) {
  const status = await page.evaluate(async ([course, level]) => {
    const { accessToken } = JSON.parse(localStorage.getItem("quack.local-account") ?? "{}");
    const response = await fetch(`/api/learn/dev/courses/${course}/levels/${level}/unlock`, { method: "POST", headers: { authorization: `Bearer ${accessToken}` } });
    return response.status;
  }, [COURSE, levelId]);
  expect(status).toBe(200);
}

async function openLevel(page: Page, levelId: string) {
  await page.getByTestId("open-learn").click();
  await page.getByTestId(`path-${COURSE}`).click();
  await page.getByTestId(`level-${levelId}`).click();
  await page.getByTestId("level-start").click();
}

const prompt = (page: Page) => page.getByTestId("lesson-prompt");

// Works out a like-terms answer from the prompt, as the student would: "Simplify 5y + y + 3 + y" is 7y + 3.
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

// The fake model doesn't look at the ink, so any letter the test font lacks is drawn as an x.
const drawable = (math: string) => math.replace(/\s/g, "").replace(/[a-wyz]/g, "x");

async function answer(page: Page, math: string, y = 80) {
  // The question's own page, not the notebook underneath it.
  await expect(page.getByTestId("lesson").getByTestId("ink")).toBeVisible();
  await script({ reading: reading(first(math)) });
  await write(page, drawable(math), 80, y);
}

async function answerRight(page: Page) {
  await answer(page, simplified(await prompt(page).innerText()));
  await expect(page.getByTestId("lesson-correct")).toBeVisible();
}

async function next(page: Page) {
  const before = await page.getByTestId("lesson").getAttribute("data-question");
  await page.getByTestId("lesson-next").click();
  await expect(page.getByTestId("lesson")).not.toHaveAttribute("data-question", before!);
}

test.beforeEach(async () => {
  await script({ reset: true });
});

test.describe("the adaptive loop", () => {
  test("A to D: right alone, a level mastered, then a dropped letter, its correction, and a targeted next question", async ({ page }) => {
    test.setTimeout(300_000);
    await page.setViewportSize({ width: 1180, height: 820 });
    await signUp(page);

    // Learn opens over the notebook, with the notebook one tap away.
    await page.getByTestId("open-learn").click();
    await expect(page.getByTestId(`path-${COURSE}`)).toHaveAttribute("data-status", "available");
    // Every path is open now: each has starter levels of its own (see paths.lessons.spec.ts).
    await expect(page.getByTestId("path-geometry")).toHaveAttribute("data-status", "available");
    await page.screenshot({ path: "test-results/lessons-1-paths.png" });

    await page.getByTestId(`path-${COURSE}`).click();
    await expect(page.getByTestId("level-meet_the_variables")).toHaveAttribute("data-status", "available");
    await expect(page.getByTestId("level-combine_like_terms")).toHaveAttribute("data-status", "locked");
    await expect(page.getByTestId("level-algebra_adventure")).toHaveAttribute("data-status", "locked");
    await page.screenshot({ path: "test-results/lessons-2-roadmap.png" });

    // Scenario A: Level 1, right every time, positively checked each time.
    await page.getByTestId("level-start").click();
    await expect(page.getByTestId("lesson-intro")).toBeVisible();
    await expect(page.getByTestId("lesson-intro")).toContainText("x + x + x is three lots of x");
    await page.waitForTimeout(1600);
    await page.screenshot({ path: "test-results/lessons-3-intro.png" });
    await page.getByTestId("lesson-try").click();
    await expect(prompt(page)).toHaveText("Simplify y + y + y + y + y");
    await expect(page.getByTestId("lesson-progress")).toHaveText("0 of 3 points");
    await answerRight(page);
    await expect(page.getByTestId("lesson-progress")).toHaveText("1 of 3 points");
    for (let question = 0; question < 4 && !(await page.getByTestId("level-mastered").isVisible()); question++) {
      await next(page);
      await expect(page.getByTestId("selection-reason")).toHaveAttribute("data-kind", /step_up|keep_going/);
      await answerRight(page);
      await page.waitForTimeout(400);
    }
    await expect(page.getByTestId("level-mastered")).toBeVisible();
    await expect(page.getByTestId("unlocked")).toContainText("Substitute and Evaluate");
    // Round 4: warm words from what happened, not "You showed it with your own working".
    await expect(page.getByTestId("mastered-cheer")).toHaveText(/!$/);
    await expect(page.getByTestId("mastered-line")).toContainText("on your own");
    await expect(page.getByTestId("level-mastered")).not.toContainText("You showed it with your own working");
    await page.waitForTimeout(700);
    await page.screenshot({ path: "test-results/lessons-7-mastered.png" });
    await page.getByTestId("mastered-roadmap").click();
    await expect(page.getByTestId("level-meet_the_variables")).toHaveAttribute("data-status", "mastered");
    await expect(page.getByTestId("level-combine_like_terms")).toHaveAttribute("data-status", "available");
    await expect(page.getByTestId("course-progress")).toHaveText("1 of 7 mastered");

    // Scenario B: 3x + 2x = 5, flagged, then put right on the same line.
    await page.getByTestId("level-combine_like_terms").click();
    await page.getByTestId("level-start").click();
    await page.getByTestId("lesson-try").click();
    await expect(prompt(page)).toHaveText("Simplify 3x + 2x");
    await answer(page, "5");
    await expect(page.getByTestId("lesson").getByTestId("bubble")).toContainText("Quack!");
    await expect(page.getByTestId("lesson-noticed")).toHaveAttribute("data-misconception", "variable_dropped");
    await expect(page.getByTestId("lesson-noticed")).toContainText("the letter went missing");
    await expect(page.getByTestId("lesson-correct")).toHaveCount(0);
    await page.screenshot({ path: "test-results/lessons-5-mistake.png" });

    // A hint, which never gives the answer away.
    await page.getByTestId("lesson").getByTestId("duck").click();
    await expect(page.getByTestId("hint-panel")).toBeVisible();
    await expect(page.getByTestId("hint-panel")).not.toContainText("5x");

    await script({ reading: reading(first("5x")) });
    await write(page, "x", 110, 80);
    await expect(page.getByTestId("lesson").getByTestId("bubble")).toHaveText("That's it.");
    await expect(page.getByTestId("lesson-correct")).toBeVisible();
    await expect(page.getByTestId("lesson-progress")).toHaveText("0.5 of 3 points");

    // Scenario C: the next question is about the same slip, and says why.
    await next(page);
    await expect(page.getByTestId("selection-reason")).toHaveAttribute("data-kind", "targeted");
    await expect(page.getByTestId("selection-reason")).toContainText("Nice correction! Here's a slightly different one");
    await expect(page.getByTestId("selection-target")).toContainText("the letter went missing");
    await expect(prompt(page)).not.toHaveText("Simplify 3x + 2x");
    await expect(prompt(page)).toHaveText(/^Simplify \d+[a-z] (\+|−) \d+[a-z]/);
    await page.screenshot({ path: "test-results/lessons-6-adaptive.png" });

    // Scenario D: that one right alone. The slip is practised, and the next steps up.
    await answerRight(page);
    await expect(page.getByTestId("lesson-progress")).toHaveText("1.5 of 3 points");
    await next(page);
    await expect(page.getByTestId("selection-reason")).toHaveAttribute("data-kind", "step_up");
    await expect(page.getByTestId("lesson-tier")).toHaveText("Apply");

    // The mistake is in My Mistakes once, with where it was made, and the notebook was never touched.
    await page.getByTestId("lesson-back").click();
    await page.getByTestId("to-notebook").click();
    await expect(page.getByTestId("ink")).toBeVisible();
    await page.getByTestId("account-menu").click();
    await page.getByTestId("menu-mistakes").click();
    await expect(page.getByTestId("mistake-entry")).toHaveCount(1);
    await expect(page.getByTestId("mistake-entry")).toHaveAttribute("data-status", "corrected");
    await page.getByTestId("mistake-entry").click();
    await expect(page.getByTestId("mistake-lesson")).toContainText("Combine Like Terms");
    await expect(page.getByTestId("mistake-lesson")).toContainText("the letter went missing");
    await page.getByTestId("mistake-practise-skill").click();
    await expect(page.getByTestId("lesson")).toBeVisible();
  });

  test("E: the same slip twice brings the idea back, and the student can always move on", async ({ page }) => {
    test.setTimeout(180_000);
    await signUp(page);
    await unlockUpTo(page, "combine_like_terms");
    await openLevel(page, "combine_like_terms");
    await page.getByTestId("lesson-try").click();

    await answer(page, "5");
    await expect(page.getByTestId("lesson-noticed")).toBeVisible();
    // Moving on without fixing it: an easier one about the same slip.
    await next(page);
    await expect(page.getByTestId("selection-reason")).toHaveAttribute("data-kind", "simpler");

    const question = await prompt(page).innerText();
    const dropped = simplified(question).replace(/[a-z]/g, "");
    await answer(page, dropped);
    await expect(page.getByTestId("lesson-noticed")).toHaveAttribute("data-misconception", "variable_dropped");
    await next(page);
    await expect(page.getByTestId("selection-reason")).toHaveAttribute("data-kind", "concept_review");
    await expect(page.getByTestId("lesson-idea-panel")).toBeVisible();
    await expect(page.getByTestId("lesson-idea-panel")).toContainText("Keep the letter");
    await page.screenshot({ path: "test-results/lessons-e-review.png" });

    // Skipping is never punished and never traps: after enough questions, moving on is offered.
    for (let index = 0; index < 6; index++) await next(page);
    await page.getByTestId("lesson-back").click();
    await expect(page.getByTestId("level-move-on")).toBeVisible();
    await page.getByTestId("level-move-on").click();
    await expect(page.getByTestId("level-combine_like_terms")).toHaveAttribute("data-status", "moved_on");
    await expect(page.getByTestId("level-simplify_expressions")).toHaveAttribute("data-status", "available");
    await expect(page.getByTestId("course-progress")).toHaveText("0 of 7 mastered");
  });

  test("F: an unsure reading is no mistake and no answer, and the student can try again", async ({ page }) => {
    test.setTimeout(120_000);
    await signUp(page);
    await unlockUpTo(page, "combine_like_terms");
    await openLevel(page, "combine_like_terms");
    await page.getByTestId("lesson-try").click();

    await expect(page.getByTestId("lesson").getByTestId("ink")).toBeVisible();
    await script({ reading: reading(line("5", { continues_previous: "no", legibility: "unsure", alternatives: ["5x"] })) });
    await write(page, "5", 80, 80);
    await page.getByTestId("lesson-check").click();
    // Round 4: not "Nothing looks wrong", which an unsure reading can't say. See test_ipad_qa_round4.py.
    await expect(page.getByTestId("lesson-unread")).toBeVisible();
    await expect(page.getByTestId("lesson-unfinished")).toHaveCount(0);
    await expect(bubble(page)).not.toContainText("Quack!");
    await expect(page.getByTestId("lesson-noticed")).toHaveCount(0);
    await expect(page.getByTestId("lesson-correct")).toHaveCount(0);
    await expect(page.getByTestId("lesson-progress")).toHaveText("0 of 3 points");

    // Written again clearly, it's checked as normal.
    await script({ reading: reading(first("5x")) });
    await page.getByTestId("lesson-clear").click();
    await write(page, "5x", 80, 80);
    await expect(page.getByTestId("lesson-correct")).toBeVisible();
    await expect(page.getByTestId("lesson-progress")).toHaveText("1 of 3 points");
  });

  test("G: coming back finds the same lesson and question, from Continue learning", async ({ page, browser }) => {
    test.setTimeout(150_000);
    const address = await signUp(page);
    await unlockUpTo(page, "combine_like_terms");
    await openLevel(page, "combine_like_terms");
    await page.getByTestId("lesson-try").click();
    await answerRight(page);
    await next(page);
    const asked = await prompt(page).innerText();

    // Another browser, as on another day or device: nothing comes from this one's storage.
    const later = await browser.newPage();
    await signIn(later, address);
    await expect(later.getByTestId("continue-chip")).toContainText("Algebra Foundations, Combine Like Terms");
    await later.screenshot({ path: "test-results/lessons-8-continue.png" });
    await later.getByTestId("continue-chip-open").click();
    await expect(later.getByTestId("lesson-prompt")).toHaveText(asked);
    await expect(later.getByTestId("lesson-progress")).toHaveText("1 of 3 points");
    await later.close();
  });

  test("H and J: levels keep their own progress, and the notebook works as before", async ({ page }) => {
    test.setTimeout(150_000);
    await signUp(page);
    await openLevel(page, "meet_the_variables");
    await page.getByTestId("lesson-try").click();
    await answerRight(page);
    await page.getByTestId("lesson-back").click();
    await expect(page.getByTestId("level-meet_the_variables")).toHaveAttribute("data-status", "in_progress");
    await expect(page.getByTestId("level-substitute_and_evaluate")).toHaveAttribute("data-status", "locked");

    // The notebook, untouched: nothing written in a lesson is on it, and checking still quacks.
    await page.getByTestId("to-notebook").click();
    await expect(page.getByTestId("notebook")).toHaveAttribute("data-strokes", "0");
    await page.getByTestId("task-context").click();
    await script({ reading: reading(first("3x + 2x = 5")) });
    await write(page, "3x+2x=5", 80, 80);
    await expect(bubble(page)).toContainText("Quack!");
    await expect(page.getByTestId("continue-chip")).toContainText("Meet the Variables");
  });
});

test.describe("tablet layouts", () => {
  for (const size of [{ width: 1180, height: 820 }, { width: 820, height: 1180 }]) {
    test(`a lesson and its path fit at ${size.width} wide`, async ({ page }) => {
      test.setTimeout(120_000);
      await page.setViewportSize(size);
      await signUp(page);
      await unlockUpTo(page, "combine_like_terms");
      await openLevel(page, "combine_like_terms");
      await page.getByTestId("lesson-try").click();
      await answer(page, "5");
      await expect(page.getByTestId("lesson-noticed")).toBeVisible();
      await page.screenshot({ path: `test-results/lessons-${size.width}-question.png` });
      // The page doesn't scroll sideways, and the writing space keeps most of the screen.
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      const canvas = await page.getByTestId("lesson").getByTestId("ink").boundingBox();
      expect(canvas!.height).toBeGreaterThan(size.height * 0.55);
      for (const id of ["lesson-check", "lesson-next", "lesson-finish"]) {
        const box = await page.getByTestId(id).boundingBox();
        expect(box!.x + box!.width).toBeLessThanOrEqual(size.width);
        expect(box!.height).toBeGreaterThanOrEqual(44);
      }
      await page.getByTestId("lesson-back").click();
      await expect(page.getByTestId("roadmap")).toBeVisible();
      await page.screenshot({ path: `test-results/lessons-${size.width}-roadmap.png`, fullPage: true });
      const roadOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(roadOverflow).toBeLessThanOrEqual(0);
    });
  }
});

// The physical iPad session of 2026-11-03, Level 2. The readings are the real model's from that session's
// trace; the fake model returns them, so this is about what Quack does with them, not how well it reads.
test.describe("substitute and evaluate, as on the iPad", () => {
  async function questionOne(page: Page) {
    await signUp(page);
    await unlockUpTo(page, "substitute_and_evaluate");
    await openLevel(page, "substitute_and_evaluate");
    await page.getByTestId("lesson-try").click();
    await expect(prompt(page)).toHaveText("If x = 3, work out 5x + 4");
    await expect(page.getByTestId("lesson").getByTestId("ink")).toBeVisible();
  }

  test("x = 3 written first is no mistake, the working is right alone, and the duck cheers once", async ({ page }) => {
    test.setTimeout(150_000);
    // The iPad showed Next's red "Issues" badge. Whatever it was, a lesson mustn't add to it.
    const problems: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") problems.push(message.text());
    });
    page.on("pageerror", (error) => problems.push(error.message));
    await spyOnQuacks(page);
    await questionOne(page);
    // As it was written on the iPad: the given, then the working underneath.
    await script({ reading: reading(first("x = 3"), line("5(3)+4"), line("= 15+4"), line("= 19")) });
    await write(page, "x=3", 80, 70);
    await write(page, "5(3)+4", 80, 150);
    await write(page, "=15+4", 80, 230);
    await write(page, "=19", 80, 310);

    await expect(page.getByTestId("lesson-correct")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("mistake-count")).toHaveCount(0);
    await expect(page.getByTestId("lesson-progress")).toHaveText("1 of 3 points");
    // The notebook's own duck is underneath the lesson.
    const lesson = page.getByTestId("lesson");
    const cheer = lesson.getByTestId("cheer");
    await expect(cheer).toHaveText(/Good job!|You got it!|Nicely done!/);
    await expect(lesson.getByTestId("duck")).toHaveAttribute("data-cheering", "true");
    await expect(lesson.getByTestId("announcer")).toHaveText(/Good job!|You got it!|Nicely done!/);
    await page.screenshot({ path: "test-results/lessons-substitution-cheer.png" });
    // Two short notes, and no quack: nothing was wrong.
    expect(await quacksPlayed(page)).toBe(2);
    await expect(cheer).toHaveCount(0, { timeout: 5_000 });

    // Another check of the same page is no new success.
    await lesson.getByTestId("duck").click();
    await page.waitForTimeout(2_000);
    await expect(cheer).toHaveCount(0);
    expect(await quacksPlayed(page)).toBe(2);

    // Nor is coming back to it.
    await page.reload();
    await page.getByTestId("open-learn").click();
    await page.getByTestId(`path-${COURSE}`).click();
    await page.getByTestId("level-substitute_and_evaluate").click();
    await page.getByTestId("level-start").click();
    await expect(page.getByTestId("lesson-correct")).toBeVisible();
    await page.waitForTimeout(1_000);
    await expect(cheer).toHaveCount(0);
    expect(problems).toEqual([]);
  });

  test("with sound off, a right answer still cheers, silently", async ({ page }) => {
    test.setTimeout(150_000);
    await spyOnQuacks(page);
    await questionOne(page);
    await page.evaluate(() => localStorage.setItem("quack.muted", "true"));
    await page.reload();
    await page.getByTestId("open-learn").click();
    await page.getByTestId(`path-${COURSE}`).click();
    await page.getByTestId("level-substitute_and_evaluate").click();
    await page.getByTestId("level-start").click();
    await page.getByTestId("lesson-try").click();
    await script({ reading: reading(first("19")) });
    await write(page, "19", 80, 80);
    await expect(page.getByTestId("lesson").getByTestId("cheer")).toBeVisible({ timeout: 20_000 });
    expect(await quacksPlayed(page)).toBe(0);
  });

  test("a check Quack starts by itself doesn't ask for a final answer; asking does", async ({ page }) => {
    test.setTimeout(150_000);
    await questionOne(page);
    await script({ reading: reading(first("x = 3")) });
    await write(page, "x=3", 80, 80);
    // Long enough for an automatic check to come and go.
    await page.waitForTimeout(3_000);
    await expect(page.getByTestId("lesson-unfinished")).toHaveCount(0);
    await expect(page.getByTestId("mistake-count")).toHaveCount(0);
    await page.getByTestId("lesson-check").click();
    await expect(page.getByTestId("lesson-unfinished")).toBeVisible();
  });

  test("a check that fails doesn't leave Check my answer stuck, and an empty page can't be checked", async ({ page }) => {
    test.setTimeout(150_000);
    await questionOne(page);
    await expect(page.getByTestId("lesson-check")).toBeDisabled();
    await page.route("**/api/practice/problems/*/check", (route) => route.abort("failed"));
    await script({ reading: reading(first("19")) });
    await write(page, "19", 80, 80);
    await page.getByTestId("lesson-check").click();
    await expect(page.getByTestId("lesson-check")).toHaveText("Check my answer", { timeout: 20_000 });
    await expect(page.getByTestId("lesson-check")).toBeEnabled();
    await page.unroute("**/api/practice/problems/*/check");
    await page.getByTestId("lesson-check").click();
    await expect(page.getByTestId("lesson-correct")).toBeVisible({ timeout: 20_000 });
  });

  test("Stop for now says the level stays in progress", async ({ page }) => {
    test.setTimeout(120_000);
    await questionOne(page);
    await expect(page.getByTestId("lesson-finish")).toHaveAccessibleName("Stop for now. The level stays in progress");
  });
});

test.describe("round 4, as on the iPad on 2026-11-03", () => {
  test("a changed letter is named, and replaces what an earlier slip said", async ({ page }) => {
    test.setTimeout(120_000);
    await signUp(page);
    await unlockUpTo(page, "combine_like_terms");
    await openLevel(page, "combine_like_terms");
    await page.getByTestId("lesson-try").click();
    await expect(prompt(page)).toHaveText("Simplify 3x + 2x");

    // First the letter dropped, as `= 3` was on the iPad.
    await answer(page, "5");
    await expect(page.getByTestId("lesson-noticed")).toHaveAttribute("data-misconception", "variable_dropped");

    // Then a different letter. On the iPad, `= 3x` still said "the letter went missing", from before.
    await script({ reading: reading(first("5y")) });
    await write(page, "y", 110, 80);
    await expect(page.getByTestId("lesson-noticed")).toHaveAttribute("data-misconception", "letter_changed");
    await expect(page.getByTestId("lesson-noticed")).toContainText("a letter changed");
    await expect(page.getByTestId("lesson-noticed")).not.toContainText("went missing");

    // Its hint is about the letter, not adding up matching terms.
    await page.getByTestId("lesson").getByTestId("duck").click();
    await expect(page.getByTestId("hint-panel")).toContainText("letter");
    await expect(page.getByTestId("hint-panel")).not.toContainText("added up the matching terms");
    await expect(page.getByTestId("hint-panel")).not.toContainText("5x");
    await page.screenshot({ path: "test-results/round4-letter-changed.png" });

    // Put right, it's right, and nothing about either slip is left on the page.
    await script({ reading: reading(first("5x")) });
    await page.getByTestId("lesson-clear").click();
    await write(page, "5x", 80, 80);
    await expect(page.getByTestId("lesson-correct")).toBeVisible();
    await expect(page.getByTestId("lesson-noticed")).toHaveCount(0);
  });

  test("a last line that is only some of the question's terms isn't called fine, or wrong, on Check", async ({ page }) => {
    test.setTimeout(120_000);
    await signUp(page);
    await unlockUpTo(page, "combine_like_terms");
    await openLevel(page, "combine_like_terms");
    await page.getByTestId("lesson-try").click();
    await expect(prompt(page)).toHaveText("Simplify 3x + 2x");

    // `3x` may be `3x + 2x` being copied out, or a wrong answer. Quack holds back, and mustn't say "Nothing looks wrong".
    await script({ reading: reading(first("3x")) });
    await write(page, "3x", 80, 80);
    await page.getByTestId("lesson-check").click();
    await expect(page.getByTestId("lesson-unsure")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("lesson-unfinished")).toHaveCount(0);
    await expect(page.getByTestId("mistake-count")).toHaveCount(0);
    await expect(page.getByTestId("lesson-correct")).toHaveCount(0);
    await expect(page.getByTestId("lesson-progress")).toHaveText("0 of 3 points");
  });

  test("points say how they're earned, and aren't questions", async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1180, height: 820 });
    await signUp(page);
    await unlockUpTo(page, "combine_like_terms");
    await openLevel(page, "combine_like_terms");
    await page.getByTestId("lesson-try").click();
    await expect(page.getByTestId("lesson-progress")).toHaveText("0 of 3 points");
    await page.getByTestId("lesson-progress").click();
    const explained = page.getByTestId("points-explained");
    await expect(explained).toContainText("Points aren't questions");
    await expect(explained).toContainText("Right on your own: 1 point.");
    await expect(explained).toContainText("½ point");
    await expect(explained).toContainText("can't read your writing, that never counts against you");
    await expect(explained).toContainText("a challenge question on your own");
    await page.screenshot({ path: "test-results/round4-points.png" });
    await page.getByTestId("lesson-progress").click();
    await expect(explained).toHaveCount(0);
  });
});
