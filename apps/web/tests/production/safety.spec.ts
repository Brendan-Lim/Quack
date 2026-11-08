import { expect, test } from "@playwright/test";
import { strokeCount, write } from "../e2e/helpers";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("quack.privacy-seen", "1");
    localStorage.setItem("quack.monitoring", "false");
  });
  // No backend or provider credentials: the compiled notebook handles unavailable checks.
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/config") return route.fulfill({ json: { mode: "off" } });
    if (path === "/api/health") return route.fulfill({ json: { status: "ok", modelConfigured: true, environment: "production" } });
    return route.fulfill({ status: 503, json: { detail: "unavailable" } });
  });
});

test("production sends browser security headers", async ({ request }) => {
  const response = await request.get("/");
  expect(response.ok()).toBe(true);
  expect(response.headers()["x-content-type-options"]).toBe("nosniff");
  expect(response.headers()["x-frame-options"]).toBe("DENY");
  expect(response.headers()["referrer-policy"]).toBe("strict-origin-when-cross-origin");
  expect(response.headers()["permissions-policy"]).toContain("microphone=(self)");
  expect(response.headers()["x-powered-by"]).toBeUndefined();
});

for (const viewport of [
  { width: 1280, height: 800 }, { width: 820, height: 1180 }, { width: 390, height: 844 },
]) {
  test(`compiled app loads without runtime errors at ${viewport.width}px`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.setViewportSize(viewport);
    await page.goto("/");
    await expect(page.getByTestId("ink")).toBeVisible();
    await expect(page.getByTestId("tool-pen")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    expect(errors).toEqual([]);
  });
}

test("loads both fonts from the app's own assets", async ({ page }) => {
  const requests: string[] = [];
  page.on("request", (request) => {
    if (request.resourceType() === "font") requests.push(request.url());
  });
  await page.goto("/");
  await page.evaluate(async () => { await document.fonts.ready; });
  expect(requests.length).toBeGreaterThanOrEqual(2);
  expect(requests.every((url) => new URL(url).origin === new URL(page.url()).origin)).toBe(true);
  const loaded = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    return ["--font-bricolage", "--font-figtree"].map((name) => {
      const family = style.getPropertyValue(name).trim().split(",")[0];
      return Boolean(family) && document.fonts.check(`700 16px ${family}`);
    });
  });
  expect(loaded).toEqual([true, true]);
});

test("a failed check keeps ink saved across reload", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("ink")).toBeVisible();
  await write(page, "2*3=5");
  const count = await strokeCount(page);
  expect(count).toBeGreaterThan(0);
  await page.getByTestId("check").click();
  await expect(page.getByText("Quack can't reach its server. Your notes are safe.")).toBeVisible();
  // Wait for the actual transaction, rather than assuming the debounce has fired.
  await expect.poll(() => page.evaluate(() => new Promise<number>((resolve, reject) => {
    const request = indexedDB.open("quack");
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const db = request.result;
      const read = db.transaction("notes").objectStore("notes").get("notebook");
      read.onsuccess = () => { resolve(read.result?.strokes?.length ?? 0); db.close(); };
      read.onerror = () => { reject(read.error); db.close(); };
    };
  }))).toBe(count);
  await page.reload();
  await expect.poll(() => strokeCount(page)).toBe(count);
});

test("corrupted saved notes produce a visible recovery message and remain intact", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("ink")).toBeVisible();
  await page.evaluate(() => new Promise<void>((resolve, reject) => {
    const request = indexedDB.open("quack");
    request.onsuccess = () => {
      const db = request.result;
      const tx = db.transaction("notes", "readwrite");
      tx.objectStore("notes").put({ version: 1, strokes: "recoverable-original" }, "notebook");
      tx.oncomplete = () => { db.close(); resolve(); };
      tx.onerror = () => { db.close(); reject(tx.error); };
    };
    request.onerror = () => reject(request.error);
  }));
  await page.reload();
  await expect(page.getByText("Quack couldn't open your saved notes, so new writing won't be saved.")).toBeVisible();
  await write(page, "2x");
  await page.waitForTimeout(700); // past the normal save debounce
  const stored = await page.evaluate(() => new Promise<unknown>((resolve, reject) => {
    const request = indexedDB.open("quack");
    request.onsuccess = () => {
      const db = request.result;
      const read = db.transaction("notes").objectStore("notes").get("notebook");
      read.onsuccess = () => { db.close(); resolve(read.result); };
      read.onerror = () => { db.close(); reject(read.error); };
    };
    request.onerror = () => reject(request.error);
  }));
  expect(stored).toEqual({ version: 1, strokes: "recoverable-original" });
});
