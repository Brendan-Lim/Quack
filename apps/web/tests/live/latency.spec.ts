// LIVE latency measurement: real browser, real proxy, real API, real Meta model. Spends credit.
//
// Times are taken in the page, from the student's last pen stroke to what they see, using the
// stage marks in lib/latency.ts. The ink is a synthetic stroke alphabet drawn by a script, so
// this measures the pipeline's speed, not how well Quack reads a person's handwriting.
//
//   QUACK_BUDGET_PROJECT_USD=7.6 QUACK_LATENCY_LABEL=before npx playwright test -c playwright.live.config.ts latency
import { mkdirSync, writeFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { expectDuck, openFresh, write } from "../e2e/helpers";

type Mark = { stage: string; at: number; id?: string; detail?: Record<string, unknown> };
type Measurement = Record<string, unknown>;

const LABEL = process.env.QUACK_LATENCY_LABEL ?? "run";
const WAIT_MS = 90_000;
// Identical synthetic ink makes identical images, which the server's reading cache would answer
// instantly. People never write the same strokes twice, so each run varies the size.
let SIZE = 44;
const results: Measurement[] = [];

const marks = (page: Page) => page.evaluate(() => ((window as unknown as { __quackLatency?: Mark[] }).__quackLatency ?? []).slice());
const now = (page: Page) => page.evaluate(() => performance.now());

async function lastStrokeEnd(page: Page): Promise<number> {
  const all = await marks(page);
  return all.filter((entry) => entry.stage === "stroke_end").at(-1)!.at;
}

// The first mark of this stage after `after`, or null if it doesn't come in time.
async function waitForMark(page: Page, stage: string, after: number, timeout = WAIT_MS, match?: (mark: Mark) => boolean) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const found = (await marks(page)).find((entry) => entry.stage === stage && entry.at > after && (!match || match(entry)));
    if (found) return found;
    await page.waitForTimeout(100);
  }
  return null;
}

// What happened to each request between two moments, for the report.
async function requestsBetween(page: Page, from: number, to: number) {
  const all = await marks(page);
  const sent = all.filter((entry) => entry.stage === "request_sent" && entry.at >= from && entry.at <= to);
  return sent.map((request) => {
    const reply = all.find((entry) => entry.stage === "response_received" && entry.id === request.id);
    return {
      id: request.id,
      trigger: request.detail?.trigger,
      sentAfterMs: Math.round(request.at - from),
      roundTripMs: reply ? Math.round(reply.at - request.at) : null,
      status: reply?.detail?.status ?? "aborted or unanswered",
      serverMs: reply?.detail?.serverMs ?? null,
      stages: reply?.detail?.stages ?? null,
      calls: reply?.detail?.calls ?? null,
    };
  });
}

async function timeToAlert(page: Page, name: string, extra: Measurement = {}) {
  const stroke = await lastStrokeEnd(page);
  const alert = await waitForMark(page, "alert_shown", stroke);
  const end = alert?.at ?? (await now(page));
  const entry = {
    scenario: name,
    alertMs: alert ? Math.round(alert.at - stroke) : null,
    requests: await requestsBetween(page, stroke - 5000, end),
    ...extra,
  };
  results.push(entry);
  console.log(`[latency] ${name}: ${entry.alertMs === null ? "NO ALERT" : `${entry.alertMs}ms`}`);
  return alert;
}

async function timeToHint(page: Page, testId: string, level: number, name: string) {
  const before = await now(page);
  await page.getByTestId(testId).click();
  const shown = await waitForMark(page, "hint_shown", before, WAIT_MS, (entry) => entry.detail?.level === level);
  // From the tap reaching the page, leaving out how long Playwright takes to click.
  const asked = (await marks(page)).find((entry) => entry.stage === "hint_requested" && entry.at > before && entry.detail?.level === level);
  const entry = {
    scenario: name,
    hintMs: shown && asked ? Math.round(shown.at - asked.at) : null,
    source: shown?.detail?.source ?? null,
    serverMs: shown?.detail?.serverMs ?? null,
  };
  results.push(entry);
  console.log(`[latency] ${name}: ${entry.hintMs}ms (${entry.source})`);
  return shown;
}

test.beforeEach(async ({ page }) => {
  SIZE = 38 + Math.random() * 12;
  await openFresh(page);
});

// Not under test-results, which Playwright empties at the start of every run.
test.afterAll(() => {
  const dir = process.env.QUACK_LATENCY_OUT ?? "latency-results";
  mkdirSync(dir, { recursive: true });
  writeFileSync(`${dir}/latency-${LABEL}-${Date.now()}.json`, JSON.stringify(results, null, 2));
});

test("standalone 2 × 3 = 5: alert, two hints, then a correction", async ({ page }) => {
  await write(page, "2*3=5", 60, 60, SIZE);
  const alert = await timeToAlert(page, "alert: 2*3=5");
  expect(alert).not.toBeNull();

  await timeToHint(page, "hint-button", 1, "hint 1: 2*3=5");
  await timeToHint(page, "another-hint", 2, "hint 2: 2*3=5");
  await timeToHint(page, "explain", 3, "explanation: 2*3=5");

  // The 5 is one stroke. Undo it and write a 6 in its place.
  await page.getByTestId("undo").click();
  await write(page, "6", 60 + SIZE * 4, 60, SIZE);
  const stroke = await lastStrokeEnd(page);
  const cleared = await waitForMark(page, "alert_cleared", stroke);
  const entry = {
    scenario: "correction: 2*3=5 to 2*3=6",
    clearedMs: cleared ? Math.round(cleared.at - stroke) : null,
    touchedMs: await waitForMark(page, "mistake_touched", stroke - 3000, 1).then((m) => (m ? Math.round(m.at - stroke) : null)),
    requests: await requestsBetween(page, stroke - 3000, cleared?.at ?? (await now(page))),
  };
  results.push(entry);
  console.log(`[latency] correction: ${entry.clearedMs === null ? "NOT CLEARED" : `${entry.clearedMs}ms`}`);
});

test("Check my work, with monitoring off", async ({ page }) => {
  await page.getByTestId("monitoring").click();
  await write(page, "4*5=21", 60, 60, SIZE);
  const before = await now(page);
  await page.getByTestId("check").click();
  const alert = await waitForMark(page, "alert_shown", before);
  const asked = (await marks(page)).find((entry) => entry.stage === "request_sent" && entry.at > before);
  const entry = {
    scenario: "manual: 4*5=21",
    alertMs: alert && asked ? Math.round(alert.at - asked.at) : null,
    requests: await requestsBetween(page, before, alert?.at ?? (await now(page))),
  };
  results.push(entry);
  console.log(`[latency] manual: ${entry.alertMs === null ? "NO ALERT" : `${entry.alertMs}ms`}`);
  expect(alert).not.toBeNull();
});

test("standalone 3 × 4 = 10", async ({ page }) => {
  await write(page, "3*4=10", 60, 60, SIZE);
  expect(await timeToAlert(page, "alert: 3*4=10")).not.toBeNull();
});

test("standalone 2 × 3 = 6 stays silent", async ({ page }) => {
  await write(page, "2*3=6", 60, 60, SIZE);
  const stroke = await lastStrokeEnd(page);
  const reply = await waitForMark(page, "response_received", stroke - 2000);
  await page.waitForTimeout(1500);
  const alerted = await waitForMark(page, "alert_shown", stroke, 1);
  const entry = {
    scenario: "silent: 2*3=6",
    replyMs: reply ? Math.round(reply.at - stroke) : null,
    status: reply?.detail?.status ?? null,
    alerted: Boolean(alerted),
    requests: await requestsBetween(page, stroke - 5000, await now(page)),
  };
  results.push(entry);
  console.log(`[latency] silent 2*3=6: reply ${entry.replyMs}ms ${entry.status}, alerted=${entry.alerted}`);
  expect(alerted).toBeNull();
  await expectDuck(page, "idle");
});

test("single-line derivative mistake", async ({ page }) => {
  await write(page, "d/dx(x^3+2x)=3x+2", 40, 60, SIZE - 4);
  expect(await timeToAlert(page, "alert: d/dx(x^3+2x)=3x+2")).not.toBeNull();
});

test("a mistake, then the student keeps writing the next line", async ({ page }) => {
  await write(page, "3*4=10", 60, 60, SIZE);
  const firstLine = await lastStrokeEnd(page);
  // Long enough for a check to start, and the way people actually move on to the next sum.
  await page.waitForTimeout(2500);
  await write(page, "7+5=12", 60, 160, SIZE);
  const alert = await waitForMark(page, "alert_shown", firstLine);
  const secondLine = await lastStrokeEnd(page);
  const entry = {
    scenario: "keep writing: 3*4=10 then 7+5=12",
    alertAfterFirstLineMs: alert ? Math.round(alert.at - firstLine) : null,
    alertAfterSecondLineMs: alert ? Math.round(alert.at - secondLine) : null,
    requests: await requestsBetween(page, firstLine - 5000, alert?.at ?? (await now(page))),
  };
  results.push(entry);
  console.log(`[latency] keep writing: ${entry.alertAfterFirstLineMs}ms after line 1, ${entry.alertAfterSecondLineMs}ms after line 2`);
  expect(alert).not.toBeNull();
});

// Round 2. A full page is where the old crop got slow, as every check read all of it again.
const CORRECT_PAGE = ["7+5=12", "8+6=14", "9-4=5", "6*2=12", "5*5=25", "9+9=18"];

async function writeCorrectPage(page: Page, size: number) {
  for (const [index, text] of CORRECT_PAGE.entries()) {
    await write(page, text, 60, 40 + index * (size + 24), size);
    // A short pause, as between sums, so each is checked and remembered.
    await page.waitForTimeout(4500);
  }
}

test("a mistake at the bottom of a full page, then corrected", async ({ page }) => {
  test.setTimeout(240_000);
  const size = 30;
  await writeCorrectPage(page, size);
  const y = 40 + CORRECT_PAGE.length * (size + 24);
  await write(page, "3*4=10", 60, y, size);
  const alert = await timeToAlert(page, "full page alert: 3*4=10");
  expect(alert).not.toBeNull();
  const sent = (await marks(page)).filter((entry) => entry.stage === "region_selected").at(-1);
  console.log(`[latency] full page: ${JSON.stringify(sent?.detail)}`);

  // The 0 is the last stroke of the answer's last digit. Replace the whole answer's last glyph.
  await page.getByTestId("undo").click();
  await write(page, "2", 60 + size * 5, y, size);
  const stroke = await lastStrokeEnd(page);
  const cleared = await waitForMark(page, "alert_cleared", stroke);
  const entry = {
    scenario: "full page correction: 3*4=10 to 12",
    clearedMs: cleared ? Math.round(cleared.at - stroke) : null,
    requests: await requestsBetween(page, stroke - 3000, cleared?.at ?? (await now(page))),
  };
  results.push(entry);
  console.log(`[latency] full page correction: ${entry.clearedMs === null ? "NOT CLEARED" : `${entry.clearedMs}ms`}`);
});

test("two independent mistakes", async ({ page }) => {
  await write(page, "3*4=10", 60, 60, SIZE);
  await write(page, "6/2=4", 60, 60 + SIZE * 2.5, SIZE);
  const stroke = await lastStrokeEnd(page);
  await expect(page.getByTestId("mistake-count")).toHaveText("2", { timeout: 30_000 });
  const entry = {
    scenario: "two mistakes: both found",
    bothMs: Math.round((await now(page)) - stroke),
    requests: await requestsBetween(page, stroke - 5000, await now(page)),
  };
  results.push(entry);
  console.log(`[latency] two mistakes: ${entry.bothMs}ms`);
  const started = await now(page);
  await page.getByTestId("mistake-choice").nth(1).click();
  console.log(`[latency] switching: ${Math.round((await now(page)) - started)}ms including the click`);
  await page.getByTestId("hint-button").click();
  await expect(page.getByTestId("help-level-1")).toHaveText("Check that division again.");
});

test("right sums on separate lines stay silent", async ({ page }) => {
  await write(page, "2*3=6", 60, 60, SIZE);
  await write(page, "6/2=3", 60, 60 + SIZE * 2.5, SIZE);
  await write(page, "7+5=12", 60, 60 + SIZE * 5, SIZE);
  const stroke = await lastStrokeEnd(page);
  await waitForMark(page, "response_received", stroke - 1000);
  await page.waitForTimeout(4000);
  const alerted = await waitForMark(page, "alert_shown", 0, 1);
  results.push({ scenario: "silent page", alerted: Boolean(alerted), requests: await requestsBetween(page, stroke - 30000, await now(page)) });
  expect(alerted).toBeNull();
});

test("right algebra above a wrong derivative: one mark, and the right words", async ({ page }) => {
  const size = 30;
  await write(page, "2(x+3)=2x+6", 40, 60, size);
  await page.waitForTimeout(4000);
  await write(page, "d/dx(x^2+2x)=3x+2", 40, 60 + size * 2, size);
  const alert = await timeToAlert(page, "alert: derivative under algebra");
  expect(alert).not.toBeNull();
  const marked = JSON.parse((await page.getByTestId("notebook").getAttribute("data-markers")) ?? "[]");
  console.log(`[latency] markers: ${JSON.stringify(marked)}`);
  expect(marked).toHaveLength(1);
  await timeToHint(page, "hint-button", 1, "hint 1: derivative");
  await timeToHint(page, "another-hint", 2, "hint 2: derivative");
  await timeToHint(page, "explain", 3, "explanation: derivative");
  await expect(page.getByTestId("help-level-3")).toContainText("is 2x, not 3x");
});
