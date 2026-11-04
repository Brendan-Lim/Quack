import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, type Page } from "@playwright/test";

export const FAKE_META = `http://127.0.0.1:${8101 + Number(process.env.QUACK_E2E_PORT_SHIFT ?? 0)}`;

type Glyphs = Record<string, number[][][]>;
const glyphs = JSON.parse(readFileSync(resolve(__dirname, "../../../../evals/synthetic/glyphs.json"), "utf8")) as Glyphs;

export function line(math: string, overrides: Record<string, unknown> = {}) {
  return {
    raw_text: math,
    math,
    kind: "math",
    legibility: "clear",
    alternatives: [],
    continues_previous: "yes",
    operation: "expand",
    y_top: 0.1,
    y_bottom: 0.4,
    ...overrides,
  };
}

export const first = (math: string) => line(math, { continues_previous: "no" });

export async function script(body: Record<string, unknown>) {
  const response = await fetch(`${FAKE_META}/__script`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  expect(response.ok).toBe(true);
}

export function reading(...lines: unknown[]) {
  return { lines };
}

export async function stats(): Promise<{ readingRequests: number; helpRequests: number; authHeaders: string[]; lineCounts: number[]; referenceCounts: number[]; imagesSent: number[]; efforts: string[] }> {
  return (await fetch(`${FAKE_META}/__stats`)).json();
}

// Write text with real pointer events, a stroke at a time.
export async function write(page: Page, text: string, x = 80, y = 80, size = 34) {
  // The last shown is on top: a practice page opens over the notebook, which stays underneath it, and a lesson left
  // for the notebook stays mounted, hidden, so its working is there on the way back.
  // Not one inside a hidden container: wait for the topmost page to be laid out, rather than write under it.
  const target = page.locator('[data-testid="ink"]:not([hidden] *)').last();
  await target.waitFor({ state: "visible" });
  const box = await target.boundingBox();
  if (!box) throw new Error("no canvas");
  let cursor = box.x + x;
  const baseTop = box.y + y;
  let raised = false;
  for (const character of text) {
    if (character === " ") {
      cursor += size * 0.5;
      continue;
    }
    if (character === "^") {
      raised = true;
      continue;
    }
    const glyph = glyphs[character];
    if (!glyph) throw new Error(`no glyph for ${character}`);
    const scale = raised ? size * 0.62 : size;
    const top = raised ? baseTop - size * 0.28 : baseTop;
    for (const stroke of glyph) {
      await page.mouse.move(cursor + stroke[0][0] * scale * 0.8, top + stroke[0][1] * scale);
      await page.mouse.down();
      for (const [px, py] of stroke.slice(1)) {
        await page.mouse.move(cursor + px * scale * 0.8, top + py * scale, { steps: 3 });
      }
      await page.mouse.up();
    }
    cursor += scale;
    raised = false;
  }
}

export const duck = (page: Page) => page.getByTestId("duck");
export const bubble = (page: Page) => page.getByTestId("bubble");

export async function expectDuck(page: Page, state: string) {
  await expect(duck(page)).toHaveAttribute("data-duck-state", state);
}

export async function strokeCount(page: Page): Promise<number> {
  // The notebook's own page: it's first, under anything opened over it, and a hidden lesson isn't it.
  return Number(await page.getByTestId("notebook").filter({ visible: true }).first().getAttribute("data-strokes"));
}

export async function open(page: Page) {
  await script({ reset: true });
  await openFresh(page);
}

export async function openFresh(page: Page) {
  await page.goto("/");
  await expect(page.getByTestId("ink")).toBeVisible();
  // Every test starts on a fresh page in a fresh browser context.
  await page.evaluate(() => localStorage.setItem("quack.privacy-seen", "1"));
  await page.reload();
  await expect(page.getByTestId("ink")).toBeVisible();
}

// Count the sounds the app tries to play.
export async function spyOnQuacks(page: Page) {
  await page.addInitScript(() => {
    const original = window.AudioContext.prototype.createOscillator;
    (window as unknown as { quacks: number }).quacks = 0;
    window.AudioContext.prototype.createOscillator = function (...args) {
      (window as unknown as { quacks: number }).quacks += 1;
      return original.apply(this, args);
    };
  });
}

export const quacksPlayed = (page: Page) => page.evaluate(() => (window as unknown as { quacks: number }).quacks);

export const settle = (page: Page, ms = 900) => page.waitForTimeout(ms);
