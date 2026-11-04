import { expect, test } from "@playwright/test";

test("collection is a real 404 in the production build", async ({ page }) => {
  const response = await page.goto("/collect");
  expect(response?.status()).toBe(404);
  await expect(page.getByText("I agree to my handwriting being used", { exact: false })).toHaveCount(0);
});

test("debug query and backtick expose no panel, export or developer requests", async ({ page }) => {
  const errors: string[] = [];
  const developerRequests: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/dev/")) developerRequests.push(request.url());
  });
  await page.route("**/api/**", async (route) => {
    if (new URL(route.request().url()).pathname === "/api/auth/config") {
      await route.fulfill({ json: { mode: "off", supabaseUrl: null, supabaseAnonKey: null } });
    } else {
      await route.fulfill({ status: 503, json: { detail: "inference disabled in this test" } });
    }
  });
  await page.addInitScript(() => localStorage.setItem("quack.privacy-seen", "1"));
  await page.goto("/?debug");
  await expect(page.getByTestId("ink")).toBeVisible();
  await expect(page.getByTestId("debug")).toHaveCount(0);
  await page.keyboard.press("Backquote");
  // An ordinary shortcut proves the notebook's keyboard listener has handled events.
  await page.keyboard.press("e");
  await expect(page.getByTestId("tool-eraser")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("debug")).toHaveCount(0);
  await expect(page.getByTestId("export-fixture")).toHaveCount(0);
  await page.keyboard.press("p");
  await expect(page.getByTestId("tool-pen")).toHaveAttribute("aria-pressed", "true");

  const canvas = await page.getByTestId("ink").boundingBox();
  expect(canvas).not.toBeNull();
  await page.mouse.move(canvas!.x + 80, canvas!.y + 80);
  await page.mouse.down();
  await page.mouse.move(canvas!.x + 150, canvas!.y + 100, { steps: 5 });
  await page.mouse.up();
  await expect(page.getByTestId("notebook")).toHaveAttribute("data-strokes", "1");
  await page.keyboard.press("ControlOrMeta+z");
  await expect(page.getByTestId("notebook")).toHaveAttribute("data-strokes", "0");
  await page.keyboard.press("ControlOrMeta+Shift+z");
  await expect(page.getByTestId("notebook")).toHaveAttribute("data-strokes", "1");
  expect(developerRequests).toEqual([]);
  expect(errors).toEqual([]);
});
