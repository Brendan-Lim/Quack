// Captures the layout at the sizes that matter, for review by eye, and checks that controls stay on screen.
import { expect, test } from "@playwright/test";
import { bubble, expectDuck, first, line, open, reading, script, write } from "./helpers";

const SIZES = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "ipad-landscape", width: 1180, height: 820 },
  { name: "ipad-portrait", width: 820, height: 1180 },
  { name: "phone", width: 390, height: 844 },
];

for (const size of SIZES) {
  test(`layout at ${size.name}`, async ({ page }) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    await open(page);
    await page.screenshot({ path: `test-results/screens/${size.name}-1-empty.png` });

    await script({
      reading: reading(first("2(x + 3)"), line("= 2x + 3")),
      help: ["Check whether you multiplied both terms inside the bracket.", "You multiplied the x by 2. What should happen to the 3?", "Both terms inside the bracket get multiplied by 2. So 2(x + 3) becomes 2x + 6, not 2x + 3."],
    });
    await write(page, "2(x+3)", 40, 60, 30);
    await write(page, "=2x+3", 40, 120, 30);
    await expectDuck(page, "alert");
    await page.screenshot({ path: `test-results/screens/${size.name}-2-alert.png` });
    await expect(bubble(page)).toBeInViewport({ ratio: 1 });

    await page.getByTestId("hint-button").click();
    await expect(page.getByTestId("help-level-1")).toBeVisible();
    await page.getByTestId("another-hint").click();
    await expect(page.getByTestId("help-level-2")).toBeVisible();
    await page.getByTestId("explain").click();
    await expect(page.getByTestId("help-level-3")).toBeVisible();
    await page.screenshot({ path: `test-results/screens/${size.name}-3-explain.png` });
    await expect(page.getByTestId("hint-panel")).toBeInViewport({ ratio: 1 });

    for (const id of ["tool-pen", "tool-eraser", "undo", "redo", "clear", "monitoring", "sound", "import", "check"]) {
      const target = page.getByTestId(id);
      await target.scrollIntoViewIfNeeded();
      await expect(target).toBeInViewport();
      const box = (await target.boundingBox())!;
      expect(Math.min(box.width, box.height), `${id} tap target`).toBeGreaterThanOrEqual(36);
    }
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(overflow).toBe(false);
  });
}
