// The learning screens, rendered in jsdom with the server stubbed. What's shown comes from the server's
// answer, so these check that each state of it is shown as it should be, including failures.
import { createElement } from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LearnView } from "@/components/learn/LearnView";
import { Roadmap } from "@/components/learn/Roadmap";
import type { LearningQueue } from "@/lib/learning";
import type { CourseOut, LearnHome, LessonState, LevelOut, LevelStatus, Mastery } from "@/lib/types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mastery = (overrides: Partial<Mastery> = {}): Mastery => ({
  mastered: false, credits: 0, creditsNeeded: 3, distinctTemplates: 0, distinctTemplatesNeeded: 2, distinctSkills: 0,
  distinctSkillsNeeded: 1, independent: 0, independentNeeded: 1, independentAt: "challenge", independentAtMet: false,
  openMisconceptions: [], asked: 0, corrected: 0, assisted: 0, canMoveOn: false, ...overrides,
});

const level = (number: number, id: string, status: LevelStatus, overrides: Partial<LevelOut> = {}): LevelOut => ({
  id, number, kind: "level", title: `Level ${id}`, objective: "An objective.", skills: ["A skill"], prerequisites: [], status,
  mastery: mastery(), tiers: ["learn", "apply", "challenge"],
  lesson: { intro: "An intro.", steps: ["One step.", "4x means 4 * x."], example: { question: "Simplify x + x", working: ["x + x", "= 2x"], note: "" }, remember: "Remember this." },
  ...overrides,
});

const COURSE: CourseOut = {
  id: "algebra_foundations", version: 1, title: "Algebra Foundations", tagline: "A tagline.", mastered: 1, total: 4,
  levels: [
    level(1, "one", "mastered", { mastery: mastery({ mastered: true, credits: 3 }) }),
    level(2, "two", "available"),
    level(3, "three", "in_progress", { mastery: mastery({ credits: 1.5, canMoveOn: true, openMisconceptions: [{ id: "variable_dropped", label: "The letter went missing", idea: "" }] }) }),
    level(4, "four", "locked", { prerequisites: [{ id: "three", number: 3, title: "Level three" }] }),
  ],
};

const HOME: LearnHome = {
  paths: [
    { id: "algebra_foundations", title: "Algebra Foundations", tagline: "A tagline.", status: "available", preview: [], mastered: 1, total: 7 },
    { id: "geometry", title: "Geometry", tagline: "Shapes.", status: "coming_later", preview: ["Angles"], mastered: null, total: null },
  ],
  continueLearning: { courseId: "algebra_foundations", courseTitle: "Algebra Foundations", levelId: "three", levelTitle: "Combine Like Terms", levelNumber: 3, status: "in_progress" },
};

const STATE: LessonState = {
  courseId: "algebra_foundations", levelId: "three", levelNumber: 3, levelTitle: "Level three", sessionId: "s1",
  question: { id: "q1", sessionId: "s1", checkSessionId: "practice-q1", instruction: "Simplify", prompt: "Simplify 3x + 2x", skill: "algebra.like_terms", skillLabel: "Like terms", tier: "learn", number: 1, status: "open", nudges: 2 },
  selection: { kind: "start", reason: "Let's start with one like the example.", target: null, basis: {} },
  mastery: mastery(), status: "in_progress", nextLevel: null,
};

let root: Root;
let host: HTMLDivElement;
let replies: Record<string, unknown | Error>;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  replies = {};
  vi.stubGlobal("fetch", vi.fn(async (path: string) => {
    // The most specific route that matches, so a lesson's start isn't answered with its course.
    const reply = Object.entries(replies)
      .filter(([prefix]) => path.startsWith(prefix))
      .sort(([a], [b]) => b.length - a.length)[0]?.[1];
    if (reply === undefined || reply instanceof Error) return new Response("{}", { status: 503 });
    return new Response(JSON.stringify(reply), { status: 200, headers: { "content-type": "application/json" } });
  }));
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

async function show(element: ReturnType<typeof createElement>) {
  await act(async () => root.render(element));
  await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
}

const byTestId = (id: string) => host.querySelector(`[data-testid="${id}"]`);
const queue = { flush: async () => {}, forSession: () => null } as unknown as LearningQueue;

describe("the learning paths", () => {
  it("shows playable paths with progress, and the rest as coming later with none", async () => {
    replies["/api/learn"] = HOME;
    await show(createElement(LearnView, { queue, onNotebook: () => {} }));
    expect(byTestId("path-algebra_foundations")?.textContent).toContain("1 of 7 levels mastered");
    expect(byTestId("path-geometry")?.getAttribute("data-status")).toBe("coming_later");
    expect(byTestId("path-geometry")?.textContent).toContain("Coming later");
    expect(byTestId("path-geometry")?.textContent).not.toContain("mastered");
    expect(byTestId("path-geometry")?.tagName).not.toBe("BUTTON");
  });

  it("offers to continue where the student left off, and the notebook", async () => {
    replies["/api/learn"] = HOME;
    const onNotebook = vi.fn();
    await show(createElement(LearnView, { queue, onNotebook }));
    expect(byTestId("continue-learning")?.textContent).toContain("Algebra Foundations: Combine Like Terms");
    await act(async () => (byTestId("notebook-card") as HTMLButtonElement).click());
    expect(onNotebook).toHaveBeenCalled();
  });

  it("says when it can't load, and tries again", async () => {
    replies["/api/learn"] = new Error();
    await show(createElement(LearnView, { queue, onNotebook: () => {} }));
    expect(host.textContent).toContain("can't load the learning paths");
    replies["/api/learn"] = HOME;
    const retry = [...host.querySelectorAll("button")].find((button) => button.textContent === "Try again")!;
    await act(async () => retry.click());
    await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
    expect(byTestId("path-algebra_foundations")).not.toBeNull();
  });
});

describe("the roadmap", () => {
  it("marks each level's state, and puts the duck by the level under way", async () => {
    replies["/api/learn/courses/"] = COURSE;
    await show(createElement(Roadmap, { courseId: "algebra_foundations", onStart: () => {} }));
    expect(byTestId("level-one")?.getAttribute("data-status")).toBe("mastered");
    expect(byTestId("level-four")?.getAttribute("data-status")).toBe("locked");
    expect(byTestId("level-three")?.getAttribute("aria-current")).toBe("step");
    expect(byTestId("course-progress")?.textContent).toBe("1 of 4 mastered");
    expect(byTestId("level-card")?.getAttribute("data-level")).toBe("three");
    expect(byTestId("level-start")?.textContent).toBe("Continue");
  });

  it("names what's still open, and lets the student move on when they may", async () => {
    replies["/api/learn/courses/"] = COURSE;
    await show(createElement(Roadmap, { courseId: "algebra_foundations", onStart: () => {} }));
    expect(byTestId("mastery-checklist")?.textContent).toContain("One right on your own after: the letter went missing");
    expect(byTestId("level-move-on")).not.toBeNull();
  });

  it("says what a locked level needs, and offers no start", async () => {
    replies["/api/learn/courses/"] = COURSE;
    await show(createElement(Roadmap, { courseId: "algebra_foundations", focus: "four", onStart: () => {} }));
    expect(byTestId("level-locked")?.textContent).toContain("Finish Level three first.");
    expect(byTestId("level-start")).toBeNull();
  });

  it("offers practice again on a mastered level", async () => {
    replies["/api/learn/courses/"] = COURSE;
    const onStart = vi.fn();
    await show(createElement(Roadmap, { courseId: "algebra_foundations", focus: "one", onStart }));
    expect(byTestId("level-start")?.textContent).toBe("Practise again");
    await act(async () => (byTestId("level-start") as HTMLButtonElement).click());
    expect(onStart).toHaveBeenCalledWith("one");
  });
});

describe("a lesson", () => {
  it("opens on the idea and an example before any writing", async () => {
    replies["/api/learn/courses/"] = COURSE;
    replies["/api/learn/courses/algebra_foundations/levels/three/start"] = STATE;
    await show(createElement(LearnView, { queue, target: { courseId: "algebra_foundations", levelId: "three" }, onNotebook: () => {} }));
    expect(byTestId("lesson-intro")).not.toBeNull();
    expect(byTestId("lesson-intro")?.textContent).toContain("4x means 4 × x.");
    expect(byTestId("lesson-intro")?.querySelectorAll("ol li")).toHaveLength(2);
    expect(byTestId("lesson-try")).not.toBeNull();
  });

  it("says when it can't open, with a way back", async () => {
    replies["/api/learn/courses/"] = new Error();
    await show(createElement(LearnView, { queue, target: { courseId: "algebra_foundations", levelId: "three" }, onNotebook: () => {} }));
    expect(byTestId("lesson-loading")?.textContent).toContain("can't open this level");
  });
});
