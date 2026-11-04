import { describe, expect, it } from "vitest";
import { masteredMessage, masteryChecklist, masteryRule, masteryShare, notYetRight, POINTS_RULES, progressLabel, stillNeeded } from "@/lib/learn";
import type { Mastery } from "@/lib/types";

const mastery = (overrides: Partial<Mastery> = {}): Mastery => ({
  mastered: false,
  credits: 0,
  creditsNeeded: 3,
  distinctTemplates: 0,
  distinctTemplatesNeeded: 2,
  distinctSkills: 0,
  distinctSkillsNeeded: 1,
  independent: 0,
  independentNeeded: 1,
  independentAt: "challenge",
  independentAtMet: false,
  openMisconceptions: [],
  asked: 0,
  corrected: 0,
  assisted: 0,
  canMoveOn: false,
  ...overrides,
});

describe("mastery progress", () => {
  it("never looks finished on credit alone", () => {
    expect(masteryShare(mastery({ credits: 5 }))).toBeLessThan(1);
    expect(masteryShare(mastery({ credits: 1.5 }))).toBeCloseTo(0.5);
    expect(masteryShare(mastery({ mastered: true, credits: 3 }))).toBe(1);
  });

  it("says what's left, and nothing about misconceptions until one has come up", () => {
    const fresh = masteryChecklist(mastery());
    expect(fresh.map((item) => item.label)).toEqual(["0 of 3 points", "Different kinds of question", "A challenge question on your own"]);
    expect(fresh.every((item) => !item.met)).toBe(true);
    const tripped = masteryChecklist(mastery({ openMisconceptions: [{ id: "variable_dropped", label: "The letter went missing", idea: "" }] }));
    expect(tripped.at(-1)).toEqual({ label: "One right on your own after: the letter went missing", met: false });
  });

  it("asks for several skills only in a mixed challenge", () => {
    const final = masteryChecklist(mastery({ distinctSkillsNeeded: 3, distinctSkills: 3, independentAt: "demonstrate" }));
    expect(final.find((item) => item.label === "3 different skills")?.met).toBe(true);
    expect(final.find((item) => item.label === "A final challenge question on your own")).toBeDefined();
  });
});

describe("the lesson's progress, as on the iPad on 2026-11-03", () => {
  // Level 2 with 4 points from 3 kinds of question, and no challenge yet answered alone.
  const pastThePoints = mastery({ credits: 4, distinctTemplates: 3, independent: 2 });

  it("never shows more points than are needed", () => {
    expect(progressLabel(mastery({ credits: 2.5 }), "in_progress")).toBe("2.5 of 3 points");
    expect(progressLabel(pastThePoints, "in_progress")).toBe("Points done, 1 thing left");
    expect(progressLabel(pastThePoints, "in_progress")).not.toContain("4 of 3");
    expect(progressLabel(mastery({ credits: 4, mastered: true }), "mastered")).toBe("Mastered");
  });

  it("names what's still needed once the points are there, and not before", () => {
    expect(stillNeeded(pastThePoints)).toEqual(["A challenge question on your own"]);
    expect(stillNeeded(mastery({ credits: 2 }))).toEqual([]);
    expect(stillNeeded(mastery({ credits: 3, mastered: true }))).toEqual([]);
  });

  it("counts a final challenge's answers on your own, so it never says nothing is left when something is", () => {
    const final = mastery({
      credits: 4, creditsNeeded: 4, distinctTemplates: 3, distinctTemplatesNeeded: 3, distinctSkills: 3, distinctSkillsNeeded: 3,
      independentAt: "demonstrate", independentAtMet: true, independent: 2, independentNeeded: 3,
    });
    expect(stillNeeded(final)).toEqual(["3 right on your own"]);
  });
});

describe("round 4: points, and the words when a level is mastered", () => {
  it("never shows more points than a level needs, on the roadmap either", () => {
    // The roadmap said "4.5 of 3 points" once Level 3 was mastered.
    const past = masteryChecklist(mastery({ credits: 4.5, mastered: true }));
    expect(past[0]).toEqual({ label: "3 of 3 points", met: true });
    expect(masteryChecklist(mastery({ credits: 1.5 }))[0].label).toBe("1.5 of 3 points");
  });

  it("says how points are earned, as the server counts them", () => {
    // CREDIT in services/api/quack/services/adaptive.py: independent 1, self_corrected and assisted 0.5, revealed 0.
    expect(POINTS_RULES.join(" ")).toMatch(/on your own: 1 point/);
    expect(POINTS_RULES.join(" ")).toMatch(/fixing a slip, or with a hint or nudge: ½ point/);
    expect(POINTS_RULES.join(" ")).toMatch(/can't read your writing, that never counts against you/);
    expect(masteryRule(mastery())).toBe("To master a level: 3 points, a few different kinds of question, and a challenge question on your own.");
    expect(masteryRule(mastery({ independentAt: "demonstrate", creditsNeeded: 4 }))).toContain("4 points");
    // The final challenge's own rule, as curriculum.py sets it: not the levels' rule with a bigger number.
    expect(masteryRule(mastery({ independentAt: "demonstrate", creditsNeeded: 4, distinctSkillsNeeded: 3, independentNeeded: 3 }))).toBe(
      "To finish this challenge: 4 points, a few different kinds of question, 3 different skills, 3 right on your own, and a final challenge on your own.",
    );
  });

  it("is warm, short and only says what happened", () => {
    const alone = masteredMessage(mastery({ mastered: true, independent: 3 }), "combine_like_terms", true);
    const afterSlips = masteredMessage(mastery({ mastered: true, independent: 3, corrected: 2 }), "combine_like_terms", true);
    const helped = masteredMessage(mastery({ mastered: true, independent: 2, assisted: 1 }), "substitute_and_evaluate", true);
    const last = masteredMessage(mastery({ mastered: true, independentAt: "demonstrate", independent: 3 }), "algebra_adventure", false);
    for (const { cheer, line } of [alone, afterSlips, helped, last]) {
      expect(cheer).toMatch(/!$/);
      expect(line.length).toBeLessThan(110);
      expect(line).toContain("on your own");
      // Not the old wording, and nothing mechanical.
      expect(line).not.toMatch(/showed it with your own working|demonstrated|competency/);
    }
    // Slips are only mentioned when there were some, and help only when it was asked for.
    expect(alone.line).not.toMatch(/slip|help/);
    expect(afterSlips.line).toMatch(/fixed your slips/);
    expect(helped.line).toMatch(/a little help/);
    expect(last.line).toBe("You solved the final challenge on your own.");
    expect(alone.line).toMatch(/trickier|next/);
  });

  it("is the same for the same level and record", () => {
    const record = mastery({ mastered: true, independent: 3 });
    expect(masteredMessage(record, "meet_the_variables", true)).toEqual(masteredMessage(record, "meet_the_variables", true));
  });
});

describe("notYetRight", () => {
  // What Check may say with no mistake and no finished answer. "Nothing looks wrong" claims Quack held nothing back.
  it("says it can't read the page when a reading wasn't agreed", () => {
    expect(notYetRight({ allRead: false, status: "no_error", reason: null })).toBe("unread");
  });
  it("says it can't read a capital that may be the same letter", () => {
    expect(notYetRight({ allRead: true, status: "uncertain", reason: "letter_case" })).toBe("unread");
  });
  it("doesn't call a half-copied last line fine: it may be a wrong answer", () => {
    expect(notYetRight({ allRead: true, status: "uncertain", reason: "still_writing" })).toBe("unsure");
  });
  it("keeps the old message for working that's right so far, and for an API without allRead", () => {
    expect(notYetRight({ allRead: true, status: "no_error", reason: null })).toBe("unfinished");
    expect(notYetRight({ status: "no_error", reason: null })).toBe("unfinished");
  });
});
