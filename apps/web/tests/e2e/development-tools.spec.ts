import { expect, test } from "@playwright/test";

test("development keeps collection, debug activation and fixture export", async ({ page }) => {
  await page.goto("/collect");
  await expect(page.getByText("I agree to my handwriting being used", { exact: false })).toBeVisible();
  await page.addInitScript(() => localStorage.setItem("quack.privacy-seen", "1"));
  await page.goto("/?debug");
  await expect(page.getByTestId("ink")).toBeVisible();
  await expect(page.getByTestId("debug")).toBeVisible();
  const downloaded = page.waitForEvent("download");
  await page.getByTestId("export-fixture").click();
  expect((await downloaded).suggestedFilename()).toMatch(/^quack-qa-.*\.json$/);
  await page.keyboard.press("Backquote");
  await expect(page.getByTestId("debug")).toHaveCount(0);
  await page.keyboard.press("Backquote");
  await expect(page.getByTestId("debug")).toBeVisible();
});
