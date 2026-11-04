// Circling working and asking. Real browser, proxy, API and verifier, with only Meta's API faked. Pen
// input here is scripted through the browser's protocol, which isn't the same as an Apple Pencil.
import { expect, test, type CDPSession, type Page } from "@playwright/test";
import { bubble, duck, expectDuck, first, line, open, reading, script, settle, stats, strokeCount, write } from "./helpers";

const sum = (math: string) => line(math, { continues_previous: "no", operation: "arithmetic" });
const selected = async (page: Page) => Number(await page.getByTestId("notebook").getAttribute("data-selected-strokes"));
const version = (page: Page) => page.getByTestId("notebook").getAttribute("data-version");
const panel = (page: Page) => page.getByTestId("ask-panel");

// A loop around a rectangle of the canvas, in CSS pixels from its corner.
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
  await open(page);
});

test.describe("selecting", () => {
  test("a single line, leaving the next alone and the ink untouched", async ({ page }) => {
    await script({ reading: reading(sum("2 + 3 = 5"), sum("4 + 4 = 8")) });
    await write(page, "2+3=5", 80, 60);
    await write(page, "4+4=8", 80, 200);
    await settle(page);
    const strokes = await strokeCount(page);
    const before = await version(page);
    await circle(page, 60, 40, 400, 110);
    await expect(panel(page)).toBeVisible();
    const chosen = await selected(page);
    expect(chosen).toBeGreaterThan(0);
    expect(chosen).toBeLessThan(strokes);
    expect(await strokeCount(page)).toBe(strokes);
    expect(await version(page)).toBe(before);
    await expect(page.getByTestId("undo")).toBeEnabled();
  });

  test("several lines together", async ({ page }) => {
    await write(page, "2+3", 80, 60);
    await write(page, "=5", 80, 120);
    await settle(page);
    const strokes = await strokeCount(page);
    await circle(page, 60, 40, 400, 170);
    await expect(panel(page)).toBeVisible();
    expect(await selected(page)).toBe(strokes);
  });

  test("the lasso leaves no ink behind, and writing goes on as before afterwards", async ({ page }) => {
    await write(page, "2+3=5", 80, 60);
    await settle(page);
    const strokes = await strokeCount(page);
    await circle(page, 60, 40, 400, 110);
    await page.getByTestId("ask-cancel").click();
    await expect(panel(page)).toHaveCount(0);
    expect(await strokeCount(page)).toBe(strokes);
    expect(await selected(page)).toBe(0);
    await expect(page.getByTestId("tool-pen")).toHaveAttribute("aria-pressed", "true");
    await write(page, "1", 80, 200);
    expect(await strokeCount(page)).toBeGreaterThan(strokes);
  });

  test("a tap with the lasso cancels, and leaves everything alone", async ({ page }) => {
    await write(page, "2+3=5", 80, 60);
    await settle(page);
    const strokes = await strokeCount(page);
    await circle(page, 60, 40, 400, 110);
    await expect(panel(page)).toBeVisible();
    const box = (await page.getByTestId("ink").boundingBox())!;
    await page.mouse.click(box.x + 600, box.y + 300);
    await expect(panel(page)).toHaveCount(0);
    expect(await strokeCount(page)).toBe(strokes);
    await expect(page.getByTestId("tool-pen")).toHaveAttribute("aria-pressed", "true");
    await write(page, "1", 80, 200);
    expect(await strokeCount(page)).toBeGreaterThan(strokes);
  });

  test("undo taking what's circled closes the question, and redo doesn't bring it back", async ({ page }) => {
    await write(page, "2+3=5", 80, 60);
    await settle(page);
    await circle(page, 60, 40, 400, 110);
    await expect(panel(page)).toBeVisible();
    await page.getByTestId("ask-cancel").click();
    await circle(page, 60, 40, 400, 110);
    await page.getByTestId("undo").click();
    await expect(panel(page)).toHaveCount(0);
    await expect(page.getByTestId("tool-pen")).toHaveAttribute("aria-pressed", "true");
    await page.getByTestId("redo").click();
    await expect(panel(page)).toHaveCount(0);
  });

  test("part of a line is neither right nor wrong, and costs nothing to ask", async ({ page }) => {
    await page.getByTestId("monitoring").click();
    await write(page, "3*4=12", 80, 60);
    await settle(page);
    const before = (await stats()).readingRequests;
    // All of the sum but the end of its answer.
    await circle(page, 60, 40, 205, 110);
    await page.getByTestId("ask-check").click();
    await expect(page.getByTestId("ask-reply")).toHaveAttribute("data-verdict", "unchecked");
    await expect(page.getByTestId("ask-reply")).toContainText("only part of a line");
    expect((await stats()).readingRequests).toBe(before);
  });

  test("Escape cancels", async ({ page }) => {
    await write(page, "2+3=5", 80, 60);
    await settle(page);
    await circle(page, 60, 40, 400, 110);
    await expect(panel(page)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(panel(page)).toHaveCount(0);
  });

  test("around nothing says so", async ({ page }) => {
    await write(page, "2+3=5", 80, 60);
    await settle(page);
    await circle(page, 300, 300, 500, 450);
    await expect(panel(page)).toHaveCount(0);
    await expect(bubble(page)).toContainText("no writing");
  });

  test("after scrolling, it selects what's on screen", async ({ page }) => {
    await write(page, "1+1=2", 80, 60);
    await write(page, "2+2=4", 80, 500);
    await settle(page);
    const top = await strokeCount(page);
    await page.getByTestId("notebook").evaluate((area) => {
      area.scrollTop = 420;
    });
    await settle(page, 100);
    await write(page, "3+3=6", 80, 260);
    await settle(page);
    const all = await strokeCount(page);
    expect(all).toBeGreaterThan(top);
    await circle(page, 60, 240, 400, 310);
    await expect(panel(page)).toBeVisible();
    const chosen = await selected(page);
    expect(chosen).toBe(all - top);
  });

  test("after resizing, what's circled is the same strokes", async ({ page }) => {
    await write(page, "2+3=5", 80, 60);
    await write(page, "4+4=8", 80, 200);
    await settle(page);
    const whole = await strokeCount(page);
    await page.setViewportSize({ width: 900, height: 700 });
    await settle(page, 200);
    const scale = Number(await page.getByTestId("notebook").getAttribute("data-scale"));
    await circle(page, 60 * scale, 40 * scale, 420 * scale, 110 * scale);
    await expect(panel(page)).toBeVisible();
    expect(await selected(page)).toBe(whole / 2);
  });
});

test.describe("at another screen density", () => {
  test.use({ deviceScaleFactor: 2 });

  test("the same line is selected", async ({ page }) => {
    await write(page, "2+3=5", 80, 60);
    await write(page, "4+4=8", 80, 200);
    await settle(page);
    await circle(page, 60, 40, 400, 110);
    await expect(panel(page)).toBeVisible();
    expect(await selected(page)).toBe((await strokeCount(page)) / 2);
  });
});

test.describe("with a pen", () => {
  test.use({ hasTouch: true, viewport: { width: 1024, height: 768 } });

  async function pen(cdp: CDPSession, type: "mousePressed" | "mouseMoved" | "mouseReleased", x: number, y: number) {
    await cdp.send("Input.dispatchMouseEvent", { type, x, y, button: "left", buttons: type === "mouseReleased" ? 0 : 1, clickCount: 1, pointerType: "pen", force: 0.5 });
  }

  test("a pen circles, and a resting palm doesn't get in the way", async ({ page }) => {
    await write(page, "2+3=5", 80, 60);
    await settle(page);
    const strokes = await strokeCount(page);
    const box = (await page.getByTestId("ink").boundingBox())!;
    await page.getByTestId("tool-lasso").click();
    const cdp = await page.context().newCDPSession(page);
    await pen(cdp, "mousePressed", box.x + 60, box.y + 40);
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: box.x + 600, y: box.y + 500, id: 7 }] });
    for (const [x, y] of [[300, 40], [400, 40], [400, 110], [200, 110], [60, 110], [60, 40]]) await pen(cdp, "mouseMoved", box.x + x, box.y + y);
    await pen(cdp, "mouseReleased", box.x + 60, box.y + 40);
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await expect(panel(page)).toBeVisible();
    expect(await selected(page)).toBe(strokes);
    expect(await strokeCount(page)).toBe(strokes);
  });
});

test.describe("asking", () => {
  test("journey C: a wrong derivative, circled, gets a hint about it, with the ink untouched", async ({ page }) => {
    // Watching is off, so this is the first Quack has seen of the line.
    await page.getByTestId("monitoring").click();
    await write(page, "d/dx(x^3)=2x", 80, 60);
    await settle(page);
    const strokes = await strokeCount(page);
    await script({ reading: reading(first("d/dx(x^3) = 2x")) });
    await circle(page, 60, 30, 700, 120);
    await page.getByTestId("ask-check").click();
    await expect(page.getByTestId("ask-reply")).toHaveAttribute("data-verdict", "incorrect");
    await page.getByTestId("ask-hint").click();
    await expect(page.getByTestId("ask-reply")).not.toContainText("hold up");
    await expect(page.getByTestId("ask-reply")).not.toContainText("3x");
    expect(await strokeCount(page)).toBe(strokes);
    // It's a question and not an alert, so the duck stays out of it.
    await expect(page.getByTestId("mistake-count")).toHaveCount(0);
    // A second hint goes further.
    const firstHint = await page.getByTestId("ask-reply").innerText();
    await page.getByTestId("ask-hint").click();
    await expect(page.getByTestId("ask-reply")).not.toHaveText(firstHint);
  });

  test("correct working is never given a made-up mistake", async ({ page }) => {
    await page.getByTestId("monitoring").click();
    await write(page, "2+3=5", 80, 60);
    await settle(page);
    await script({ reading: reading(sum("2 + 3 = 5")) });
    await circle(page, 60, 40, 400, 110);
    await page.getByTestId("ask-check").click();
    await expect(page.getByTestId("ask-reply")).toHaveAttribute("data-verdict", "correct");
    await expect(page.getByTestId("ask-hint")).toHaveCount(0);
    await expect(page.getByTestId("mistake-count")).toHaveCount(0);
  });

  test("unsupported maths is called that", async ({ page }) => {
    await page.getByTestId("monitoring").click();
    await write(page, "2+3=5", 80, 60);
    await settle(page);
    await script({ reading: reading(first("lim(x) = 3")) });
    await circle(page, 60, 40, 400, 110);
    await page.getByTestId("ask-check").click();
    await expect(page.getByTestId("ask-reply")).toHaveAttribute("data-verdict", "unsupported");
    await expect(page.getByTestId("ask-reply")).toContainText("beyond");
  });

  test("a free question goes to the model, and it's told only the finding", async ({ page }) => {
    await page.getByTestId("monitoring").click();
    await write(page, "d/dx(x^3)=2x", 80, 60);
    await settle(page);
    await script({ reading: reading(first("d/dx(x^3) = 2x")), help: ["The power rule says what happens to the exponent."] });
    await circle(page, 60, 30, 700, 120);
    await page.getByTestId("ask-open-question").click();
    await page.getByTestId("ask-input").fill("What rule should I use?");
    await page.getByTestId("ask-send").click();
    await expect(page.getByTestId("ask-reply")).toContainText("power rule");
  });

  test("a line already checked costs no reading", async ({ page }) => {
    await page.evaluate(() => localStorage.setItem("quack.incremental", "1"));
    await page.reload();
    await script({ reading: reading(sum("2 + 3 = 5")) });
    await write(page, "2+3=5", 80, 60);
    await expect.poll(async () => (await stats()).readingRequests).toBeGreaterThan(1);
    await settle(page, 1500);
    const before = (await stats()).readingRequests;
    await circle(page, 60, 40, 400, 110);
    await page.getByTestId("ask-explain").click();
    await expect(page.getByTestId("ask-reply")).toHaveAttribute("data-verdict", "correct");
    expect((await stats()).readingRequests).toBe(before);
  });

  test("a known mistake uses its own hints, and its alert stays one alert", async ({ page }) => {
    await script({ reading: reading(sum("3 * 4 = 10")) });
    await write(page, "3*4=10", 80, 60);
    await expectDuck(page, "alert");
    await page.getByTestId("dismiss-button").click();
    await circle(page, 60, 40, 400, 110);
    await page.getByTestId("ask-hint").click();
    await expect(page.getByTestId("hint-panel")).toBeVisible();
    await expect(panel(page)).toHaveCount(0);
    await expect(page.getByTestId("mistake-count")).toHaveText("1");
    await expect(page.getByTestId("tool-pen")).toHaveAttribute("aria-pressed", "true");
  });

  test("circling near a highlight only takes what's inside", async ({ page }) => {
    await script({ reading: reading(sum("3 * 4 = 10"), sum("2 + 2 = 4")) });
    await write(page, "3*4=10", 80, 60);
    await expectDuck(page, "alert");
    await page.getByTestId("dismiss-button").click();
    await write(page, "2+2=4", 80, 160);
    await settle(page);
    await script({ reading: reading(sum("2 + 2 = 4")) });
    await circle(page, 60, 145, 400, 215);
    await page.getByTestId("ask-explain").click();
    await expect(page.getByTestId("ask-reply")).toHaveAttribute("data-verdict", "correct");
    await expect(page.getByTestId("mistake-count")).toHaveText("1");
  });

  test("works with the server down", async ({ page }) => {
    await page.getByTestId("monitoring").click();
    await write(page, "2+3=5", 80, 60);
    await settle(page);
    await page.route("**/api/ask", (route) => route.abort());
    await circle(page, 60, 40, 400, 110);
    await page.getByTestId("ask-check").click();
    await expect(page.getByTestId("ask-problem")).toContainText("can't reach");
    expect(await strokeCount(page)).toBeGreaterThan(0);
  });
});

test.describe("journey E: 3x + 2x = 5, with nothing to say what it's for", () => {
  const LINE = "3x + 2x = 5";

  test("isn't called wrong, and solving is the end of it", async ({ page }) => {
    await script({ reading: reading(first(LINE)) });
    await write(page, "3x+2x=5", 80, 60);
    await expect(page.getByTestId("clarify")).toBeVisible();
    await expect(page.getByTestId("mistake-count")).toHaveCount(0);
    await expectDuck(page, "idle");
    await page.getByTestId("clarify-solve").click();
    await expect(page.getByTestId("clarify")).toHaveCount(0);
    await expect(bubble(page)).toContainText("solving");
    await settle(page, 1500);
    await expect(page.getByTestId("mistake-count")).toHaveCount(0);
    // And the working carries on from it.
    await script({ reading: reading(first(LINE), line("5x = 5")) });
    await write(page, "5x=5", 80, 130);
    await settle(page, 1500);
    await expect(page.getByTestId("mistake-count")).toHaveCount(0);
  });

  test("simplifying makes it a mistake, with a hint that keeps the answer back", async ({ page }) => {
    await script({ reading: reading(first(LINE)) });
    await write(page, "3x+2x=5", 80, 60);
    await page.getByTestId("clarify-simplify").click();
    await expectDuck(page, "alert");
    await duck(page).click();
    await expect(page.getByTestId("hint-panel")).toContainText("variable");
    await expect(page.getByTestId("hint-panel")).not.toContainText("5x");
  });

  test("the next line answers the question by itself", async ({ page }) => {
    await script({ reading: reading(first(LINE)) });
    await write(page, "3x+2x=5", 80, 60);
    await expect(page.getByTestId("clarify")).toBeVisible();
    await script({ reading: reading(first(LINE), line("5x = 5")) });
    await write(page, "5x=5", 80, 130);
    await expect(page.getByTestId("clarify")).toHaveCount(0);
    await expect(page.getByTestId("mistake-count")).toHaveCount(0);
  });

  test("the toolbar setting says it for the whole page, and Auto takes it back", async ({ page }) => {
    await script({ reading: reading(first(LINE)) });
    await write(page, "3x+2x=5", 80, 60);
    await expect(page.getByTestId("clarify")).toBeVisible();
    await page.getByTestId("task-context").click();
    await expect(page.getByTestId("task-context")).toHaveAttribute("data-context", "simplify");
    await expectDuck(page, "alert");
    await page.getByTestId("dismiss-button").click();
    await page.getByTestId("task-context").click();
    await expect(page.getByTestId("task-context")).toHaveAttribute("data-context", "solve");
    await expect(page.getByTestId("mistake-count")).toHaveCount(0);
  });

  test("two readings that disagree start no conversation", async ({ page }) => {
    await script({ reading: reading(first(LINE)), queue: [reading(first(LINE)), reading(first("3x + 2x = 5x")), reading(first(LINE)), reading(first("3x + 2x = 5x")), reading(first(LINE)), reading(first("3x + 2x = 5x"))] });
    await write(page, "3x+2x=5", 80, 60);
    await settle(page, 2000);
    await expect(page.getByTestId("clarify")).toHaveCount(0);
  });

  test("circled, it asks the same question, and takes the answer", async ({ page }) => {
    await page.getByTestId("monitoring").click();
    await write(page, "3x+2x=5", 80, 60);
    await settle(page);
    await script({ reading: reading(first(LINE)) });
    await circle(page, 60, 40, 500, 110);
    await page.getByTestId("ask-check").click();
    await expect(page.getByTestId("ask-reply")).toHaveAttribute("data-verdict", "ambiguous_intent");
    await page.getByTestId("ask-clarify-solve").click();
    // An equation to solve is where working starts. It isn't right or wrong, so it isn't called either.
    await expect(page.getByTestId("ask-reply")).toHaveAttribute("data-verdict", "unchecked");
    await expect(page.getByTestId("ask-reply")).toContainText("where this working starts");
    await expect(page.getByTestId("mistake-count")).toHaveCount(0);
  });
});

test.describe("integration in the notebook", () => {
  test("journey B's first half: a wrong integral alerts, and the right one clears it", async ({ page }) => {
    await script({ reading: reading(first("∫ x dx = 2")) });
    await write(page, "x=2", 80, 60);
    await expectDuck(page, "alert");
    await duck(page).click();
    await expect(page.getByTestId("hint-panel")).toContainText("variable");
    await script({ reading: reading(first("∫ x dx = 2"), first("∫ x dx = x^2/2 + C")) });
    await write(page, "x=x^2", 80, 160);
    await expect(bubble(page)).toHaveText("That's it.");
  });

  test("a right one with no constant isn't accused of anything", async ({ page }) => {
    await script({ reading: reading(first("∫ 2x dx = x^2")) });
    await write(page, "2x=x^2", 80, 60);
    await settle(page, 1500);
    await expect(page.getByTestId("mistake-count")).toHaveCount(0);
    await expectDuck(page, "idle");
  });
});
