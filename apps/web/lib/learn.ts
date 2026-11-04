// The learning paths' side of the API. Everything is decided on the server: what's unlocked, which
// question comes next and whether a level is mastered. This only asks.

import { sendJson } from "./api";
import type { CourseOut, LearnHome, LessonRecap, LessonState, Mastery, Nudge, PracticeCheckResponse } from "./types";

export const fetchLearnHome = () => sendJson<LearnHome>("GET", "/api/learn");
export const fetchCourse = (courseId: string) => sendJson<CourseOut>("GET", `/api/learn/courses/${courseId}`);
export const startLevel = (courseId: string, levelId: string) =>
  sendJson<LessonState>("POST", `/api/learn/courses/${courseId}/levels/${levelId}/start`);
export const nextQuestion = (sessionId: string, currentProblemId: string | null) =>
  sendJson<LessonState>("POST", `/api/learn/sessions/${sessionId}/next`, { currentProblemId });
export const lessonState = (sessionId: string) => sendJson<LessonState>("GET", `/api/learn/sessions/${sessionId}`);
export const finishLesson = (sessionId: string) => sendJson<LessonRecap>("POST", `/api/learn/sessions/${sessionId}/finish`);
export const askNudge = (problemId: string, level: number) => sendJson<Nudge>("POST", `/api/learn/problems/${problemId}/nudge`, { level });
export const moveOn = (courseId: string, levelId: string) =>
  sendJson<CourseOut>("POST", `/api/learn/courses/${courseId}/levels/${levelId}/move-on`);

// How far a level's credit has got, from 0 to 1. Short of 1 until every condition is met, since credit
// alone isn't mastery. The conditions are shown as a checklist beside it.
export function masteryShare(mastery: Mastery): number {
  if (mastery.mastered) return 1;
  return Math.min(0.9, mastery.credits / mastery.creditsNeeded);
}

// Each condition of the mastery rule in words, and whether it's met.
export function masteryChecklist(mastery: Mastery): { label: string; met: boolean }[] {
  const items = [
    // Never more than are needed: the roadmap said "4.5 of 3 points" in round 4.
    { label: `${Math.min(mastery.credits, mastery.creditsNeeded)} of ${mastery.creditsNeeded} points`, met: mastery.credits >= mastery.creditsNeeded },
    { label: "Different kinds of question", met: mastery.distinctTemplates >= mastery.distinctTemplatesNeeded },
    { label: mastery.independentAt === "demonstrate" ? "A final challenge question on your own" : `A ${TIER_LABELS[mastery.independentAt].toLowerCase()} question on your own`, met: mastery.independentAtMet },
  ];
  // Only once something has tripped them up, so nothing looks earned before it is.
  for (const open of mastery.openMisconceptions) items.push({ label: `One right on your own after: ${open.label.toLowerCase()}`, met: false });
  if (mastery.distinctSkillsNeeded > 1) items.splice(2, 0, { label: `${mastery.distinctSkillsNeeded} different skills`, met: mastery.distinctSkills >= mastery.distinctSkillsNeeded });
  if (mastery.independentNeeded > 1) items.push({ label: `${mastery.independentNeeded} right on your own`, met: mastery.independent >= mastery.independentNeeded });
  return items;
}

// What the bar beside a lesson says. Points stop at what's needed, since more of them doesn't mean
// more done: on the iPad "4 of 3 points" sat beside a level that wasn't mastered, with nothing to say why.
export function progressLabel(mastery: Mastery, status: string): string {
  if (status === "mastered" || mastery.mastered) return "Mastered";
  if (mastery.credits < mastery.creditsNeeded) return `${mastery.credits} of ${mastery.creditsNeeded} points`;
  const left = stillNeeded(mastery).length;
  if (left === 0) return "Points done";
  return left === 1 ? "Points done, 1 thing left" : `Points done, ${left} things left`;
}

// Once the points are there, what else mastering the level needs, in words. Nothing before then, so a
// student working towards the points isn't shown a list.
export function stillNeeded(mastery: Mastery): string[] {
  if (mastery.mastered || mastery.credits < mastery.creditsNeeded) return [];
  return masteryChecklist(mastery).filter((item) => !item.met).map((item) => item.label);
}

// Asked to check, with no mistake and no finished answer: what can honestly be said. "Nothing looks wrong" only
// when Quack read every line and held nothing back that round 4 added. Two holds aren't that: a last line made
// only of terms copied from above (`3x + 2x`, `= 3x`), which may be half-written or may be a wrong answer, and a
// capital that could be the same letter (`= 3A`). Neither is called wrong, and neither may be called fine.
export type NotYetRight = "unread" | "unsure" | "unfinished";
export function notYetRight(checked: Pick<PracticeCheckResponse, "allRead" | "status" | "reason">): NotYetRight {
  if (checked.allRead === false) return "unread";
  if (checked.status === "uncertain" && checked.reason === "letter_case") return "unread";
  if (checked.status === "uncertain" && checked.reason === "still_writing") return "unsure";
  return "unfinished";
}

export const TIER_LABELS: Record<string, string> ={ learn: "Learn", apply: "Apply", challenge: "Challenge", demonstrate: "Show it" };

// How points are earned, as `CREDIT` and `Attempt.outcome` in services/api/quack/services/adaptive.py decide
// them. On the iPad "0.5 of 3 points" after three questions read as questions lost. Points aren't questions.
export const POINTS_RULES: string[] = [
  "Right on your own: 1 point.",
  "Right after fixing a slip, or with a hint or nudge: ½ point.",
  "Seeing the answer: no points, and no harm done.",
  "If Quack can't read your writing, that never counts against you.",
];

// What the level asks for besides points, in a sentence, for under the rules.
export function masteryRule(mastery: Mastery): string {
  const challenge = mastery.independentAt === "demonstrate" ? "a final challenge" : `a ${TIER_LABELS[mastery.independentAt].toLowerCase()} question`;
  // The final challenge asks for more than a level does, and the note mustn't promise less than it asks.
  const parts = [`${mastery.creditsNeeded} points`, "a few different kinds of question"];
  if (mastery.distinctSkillsNeeded > 1) parts.push(`${mastery.distinctSkillsNeeded} different skills`);
  if (mastery.independentNeeded > 1) parts.push(`${mastery.independentNeeded} right on your own`);
  const what = mastery.independentAt === "demonstrate" ? "To finish this challenge" : "To master a level";
  return `${what}: ${parts.join(", ")}, and ${challenge} on your own.`;
}

// The duck's words when a level is mastered. Chosen from what the student did, so nothing in it is invented:
// "on your own" is always true here, as mastery needs a challenge right with no mistake and no help. The
// level picks between phrasings, so the same words don't come back every level. No model is asked.
export function masteredMessage(mastery: Mastery, levelId: string, hasNextLevel: boolean): { cheer: string; line: string } {
  const pick = <T,>(options: T[]): T => options[[...levelId].reduce((sum, char) => sum + char.charCodeAt(0), 0) % options.length];
  const next = hasNextLevel ? " Ready for something trickier?" : "";
  if (mastery.independentAt === "demonstrate") {
    return { cheer: "Amazing work!", line: `You solved the final challenge on your own.${next}` };
  }
  if (mastery.corrected > 0) {
    return pick([
      { cheer: "Look at you go!", line: `You fixed your slips, kept going, and then nailed the challenge on your own.${next}` },
      { cheer: "You did it!", line: `You spotted and fixed your slips, then got the challenge right on your own.${next}` },
    ]);
  }
  if (mastery.assisted > 0) {
    return { cheer: "You did it!", line: `You got a little help, then solved the challenge on your own.${next}` };
  }
  return pick([
    { cheer: "You did it!", line: `You got the challenge right on your own.${next}` },
    { cheer: "Amazing work!", line: `You solved the challenge on your own. You've got the hang of this.${hasNextLevel ? " Let's see what's next!" : ""}` },
  ]);
}
