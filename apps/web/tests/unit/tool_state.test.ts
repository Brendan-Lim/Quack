// The chosen pen or eraser on the iPad, 2026-11-04 evening: the hover's grey under the chosen look's yellow edge,
// for up to 30 s at a time. Safari kept the tap's :hover and didn't re-match `:not([aria-pressed="true"])`.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { toolClass } from "@/lib/toolClass";

const css = readFileSync(join(__dirname, "../../app/globals.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

function selectorsWith(fragment: string): string[] {
  const selectors: string[] = [];
  for (const match of css.matchAll(/([^{}]+)\{/g)) {
    for (const selector of match[1].split(",")) {
      const trimmed = selector.trim();
      if (trimmed.includes(fragment)) selectors.push(trimmed);
    }
  }
  return selectors;
}

describe("the chosen tool's look", () => {
  it("is a class React sets, so the chosen tool never carries the idle class", () => {
    expect(toolClass(true)).toBe("tool tool-on");
    expect(toolClass(false)).toBe("tool tool-idle");
    expect(toolClass(true, "tool-compact")).toBe("tool tool-on tool-compact");
    expect(toolClass(true).split(" ")).not.toContain("tool-idle");
  });

  it("has no hover rule that could reach a chosen tool", () => {
    const hovers = selectorsWith(":hover").filter((selector) => /\.tool\b|\.tool-/.test(selector));
    expect(hovers.length).toBeGreaterThan(0);
    for (const selector of hovers) {
      // Every hover on a tool is either the idle one or part of the chosen look itself.
      expect(selector.includes(".tool-idle") || selector.includes(".tool-on")).toBe(true);
    }
    expect(selectorsWith('.tool[aria-pressed="true"]')).toEqual([]);
  });

  it("paints the chosen fill with nothing able to override or animate it", () => {
    const rule = css.match(/\.tool-on,[^{]*\{([^}]*)\}/);
    expect(rule?.[1]).toContain("background: var(--color-duck) !important");
    const base = css.match(/\n\s*\.tool \{([^}]*)\}/);
    expect(base?.[1]).not.toContain("background-color");
  });
});
