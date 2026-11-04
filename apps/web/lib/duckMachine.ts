// The duck's behaviour as a pure reducer. No timers, audio or network: the UI reads
// `quackCount` to play a sound once per increment.

import type { AnalyzeResponse, Clarification, DuckState, Mistake, ModelIssue, Trigger } from "./types";

export type AssistanceEntry = { level: number; text: string; source: "model" | "template" };

export type MistakeSource = "notebook" | "import";

// A mistake the student isn't looking at right now, with everything needed to go back to it.
export type ParkedMistake = { mistake: Mistake; assistance: AssistanceEntry[]; dismissed: boolean; suspended: boolean; undecided?: boolean };

export type DuckModel = {
  state: Exclude<DuckState, "muted">;
  mistake: Mistake | null;
  mistakeSource: MistakeSource;
  dismissed: boolean;
  // The student has changed the ink where this mistake is. Its alert is put away while that's
  // checked, but it isn't fixed until a check says so.
  suspended: boolean;
  // That's been checked, and what's there now could neither be read clearly enough nor called fixed.
  undecided: boolean;
  bubble: string | null;
  assistance: AssistanceEntry[];
  loadingHelp: boolean;
  issue: ModelIssue | null;
  quackCount: number;
  resolvedCount: number;
  // Notebook mistakes the student has been told about and hasn't fixed, whether or not one is
  // on screen. These are what the server must not alert about again.
  seen: Record<string, { dismissed: boolean }>;
  // The other mistakes on the page. `mistake` above is the one selected, and these wait their turn.
  parked: Record<string, ParkedMistake>;
  // The duck was tapped with several mistakes open, so it's asking which.
  choosing: boolean;
  // A line that's fine one way and wrong another, which only the student can settle.
  clarification: Clarification | null;
};

export const initialDuck: DuckModel = {
  state: "idle",
  mistake: null,
  mistakeSource: "notebook",
  dismissed: false,
  suspended: false,
  undecided: false,
  bubble: null,
  assistance: [],
  loadingHelp: false,
  issue: null,
  quackCount: 0,
  resolvedCount: 0,
  seen: {},
  parked: {},
  choosing: false,
  clarification: null,
};

export const ALERT_TEXT = "Quack!";
export const RESOLVED_TEXT = "That's it.";
export const MANUAL_NO_ERROR_TEXT = "Looks right to me.";
export const MANUAL_UNCERTAIN_TEXT = "I can't quite read that. Could you write it a little more clearly?";
export const MANUAL_UNSUPPORTED_TEXT = "That's beyond what I can check so far.";
export const MANUAL_EMPTY_TEXT = "Nothing to check yet.";
export const MANUAL_AMBIGUOUS_TEXT = "That could be an equation or a simplification, so I haven't called it either way.";
export const SOLVING_TEXT = "Got it, you're solving it. I'll follow along.";
export const NEEDS_CONSTANT_TEXT = "That works as one answer. What would make it the general one?";
export const OFFLINE_TEXT = "I can't check that right now. Your notes are safe.";
export const IMPORT_FAILED_TEXT = "I couldn't open that image.";
export const STILL_WRONG_TEXT = "Not quite yet.";
export const CHECKING_CHANGE_TEXT = "Checking your change…";
export const UNDECIDED_TEXT = "I can't tell yet. Finish or rewrite that line, and I'll look again.";

export type DuckEvent =
  | { type: "check_started" }
  | { type: "check_finished" }
  | { type: "restore"; seen: Record<string, { dismissed: boolean }> }
  // `replaced` maps a new mistake to the one on the same line that it took over from.
  | { type: "result"; result: AnalyzeResponse; trigger: Trigger; replaced?: Record<string, string> }
  // An erased mistake's line was written out again, and two readings agree it's now right.
  | { type: "rewritten" }
  | { type: "select"; id: string }
  | { type: "choose" }
  | { type: "import_result"; result: AnalyzeResponse }
  | { type: "request_failed"; issue: ModelIssue; trigger: Trigger }
  | { type: "page_empty"; trigger: Trigger }
  | { type: "mistakes_erased"; ids: string[] }
  | { type: "mistake_touched"; id: string }
  // Checks have looked at these edited lines, and have nothing more to say until they change.
  | { type: "changes_undecided"; ids: string[] }
  | { type: "help_requested" }
  | { type: "help_received"; level: number; text: string; source: "model" | "template"; issue: ModelIssue | null }
  | { type: "help_failed"; issue: ModelIssue }
  | { type: "help_settled" }
  | { type: "dismiss" }
  | { type: "reopen" }
  | { type: "bubble_expired" }
  | { type: "clarified"; text?: string }
  | { type: "say"; text: string };

export function isAlertShowing(model: DuckModel): boolean {
  return model.state === "alert" && (model.bubble === ALERT_TEXT || model.bubble === STILL_WRONG_TEXT);
}

function clearMistake(model: DuckModel): DuckModel {
  return { ...model, mistake: null, mistakeSource: "notebook", dismissed: false, suspended: false, undecided: false, assistance: [], loadingHelp: false };
}

function resolve(model: DuckModel): DuckModel {
  return { ...clearMistake(model), state: "resolved", bubble: RESOLVED_TEXT, resolvedCount: model.resolvedCount + 1 };
}

function quietlyGone(model: DuckModel): DuckModel {
  return { ...clearMistake(model), state: "idle", bubble: null, resolvedCount: model.resolvedCount + 1 };
}

function reopen(model: DuckModel): DuckModel {
  if (!model.mistake) return model;
  // The line has changed since this was found, so what's on record isn't about what's there now.
  if (model.suspended) {
    const waiting = model.undecided ? UNDECIDED_TEXT : CHECKING_CHANGE_TEXT;
    return { ...model, state: model.assistance.length ? "hint" : "idle", dismissed: false, bubble: model.assistance.length ? null : waiting };
  }
  return {
    ...model,
    state: model.assistance.length ? "hint" : "alert",
    dismissed: false,
    suspended: false,
    bubble: model.assistance.length ? null : ALERT_TEXT,
  };
}

function isBusyWithStudent(model: DuckModel): boolean {
  return model.state === "alert" || model.state === "hint" || model.state === "explaining";
}

function without(seen: DuckModel["seen"], ids: string[]): DuckModel["seen"] {
  const next = { ...seen };
  for (const id of ids) delete next[id];
  return next;
}

// Tell the student something without losing an alert they haven't dealt with.
function note(model: DuckModel, text: string): DuckModel {
  if (isBusyWithStudent(model)) return model;
  return { ...model, state: "idle", bubble: text };
}

function showAlert(model: DuckModel, mistake: Mistake, source: MistakeSource): DuckModel {
  const same = model.mistake?.id === mistake.id;
  return {
    ...model,
    state: "alert",
    mistake,
    mistakeSource: source,
    dismissed: false,
    suspended: false,
    undecided: false,
    bubble: ALERT_TEXT,
    assistance: same ? model.assistance : [],
    loadingHelp: false,
    choosing: false,
  };
}

export type MistakeRecord = ParkedMistake;

// Every mistake open on the notebook page, the selected one included, in the order they were found.
export function activeMistakes(model: DuckModel): MistakeRecord[] {
  const parked = Object.values(model.parked);
  if (!model.mistake || model.mistakeSource !== "notebook") return parked;
  return [{ mistake: model.mistake, assistance: model.assistance, dismissed: model.dismissed, suspended: model.suspended, undecided: model.undecided }, ...parked];
}

function park(model: DuckModel): DuckModel {
  if (!model.mistake || model.mistakeSource !== "notebook") return model;
  const record = { mistake: model.mistake, assistance: model.assistance, dismissed: model.dismissed, suspended: model.suspended, undecided: model.undecided };
  return { ...clearMistake(model), parked: { ...model.parked, [model.mistake.id]: record } };
}

function unpark(model: DuckModel, id: string): DuckModel {
  const record = model.parked[id];
  if (!record) return model;
  const parked = { ...model.parked };
  delete parked[id];
  return { ...model, parked, mistake: record.mistake, mistakeSource: "notebook", assistance: record.assistance, dismissed: record.dismissed, suspended: record.suspended, undecided: record.undecided ?? false, loadingHelp: false };
}

// Nothing is selected, and something is waiting. It comes forward without any fuss.
function promote(model: DuckModel): DuckModel {
  const waiting = Object.keys(model.parked)[0];
  if (model.mistake || !waiting) return model;
  return unpark(model, waiting);
}

function mistakesIn(result: AnalyzeResponse): Mistake[] {
  if (result.mistakes?.length) return result.mistakes;
  return result.mistake ? [{ ...result.mistake, isNew: result.intervention.shouldQuack }] : [];
}

function applyResolved(model: DuckModel, resolved: string[], replaced: ReadonlySet<string>): DuckModel {
  let next = model;
  const gone = resolved.filter((id) => !replaced.has(id));
  const parkedGone = resolved.filter((id) => next.parked[id]);
  if (parkedGone.length) {
    const parked = { ...next.parked };
    for (const id of parkedGone) delete parked[id];
    next = { ...next, parked };
  }
  const selectedGone = next.mistakeSource === "notebook" && next.mistake !== null && resolved.includes(next.mistake.id);
  // Another attempt at the same line isn't a fix. What replaces it takes the selection.
  if (selectedGone && replaced.has(next.mistake!.id)) return clearMistake(next);
  if (selectedGone) return resolve(next);
  const parkedFixed = parkedGone.filter((id) => gone.includes(id)).length;
  if (!parkedFixed) return next;
  const counted = { ...next, resolvedCount: next.resolvedCount + parkedFixed };
  // A student reading help isn't interrupted. A bare alert gives way for a moment, and they asked for neither.
  const reading = counted.state === "hint" || counted.state === "explaining";
  return reading ? counted : { ...counted, state: "resolved", bubble: RESOLVED_TEXT };
}

// What this reply says about one mistake.
function applyMistake(model: DuckModel, reported: Mistake, trigger: Trigger, replaces: string | undefined): DuckModel {
  // Being new is about this reply. Once it's on record, it isn't.
  const mistake = { ...reported, isNew: false };
  const entry = model.seen[mistake.id];
  const next: DuckModel = entry ? model : { ...model, seen: { ...model.seen, [mistake.id]: { dismissed: false } } };

  if (next.parked[mistake.id]) {
    const record = next.parked[mistake.id];
    // Its line was edited and it's still wrong, or it's just still there. Either way, quietly.
    return { ...next, parked: { ...next.parked, [mistake.id]: { ...record, mistake, suspended: false, undecided: false } } };
  }

  // The student's next try at a line, still wrong. They should hear about it, without a quack.
  if (replaces) {
    return { ...showAlert(park(next), mistake, "notebook"), bubble: STILL_WRONG_TEXT };
  }

  // New to the server, but already on record here: two checks under way at once each reported it, the second
  // sent before the first's reply was in. One mistake, one quack.
  if (reported.isNew && entry) return next.mistake?.id === mistake.id ? { ...next, mistake } : next;

  if (reported.isNew) {
    const counted = { ...next, quackCount: next.quackCount + 1 };
    // Someone reading help on one mistake isn't pulled away from it by another.
    const reading = (counted.state === "hint" || counted.state === "explaining") && counted.mistake !== null;
    if (reading) {
      const record = { mistake, assistance: [], dismissed: false, suspended: false };
      return { ...counted, parked: { ...counted.parked, [mistake.id]: record } };
    }
    return showAlert(park(counted), mistake, "notebook");
  }

  // Already reported, so no sound.
  if (next.mistake?.id === mistake.id) {
    // The student changed those lines, and it's still wrong. Say so again, quietly.
    if (next.suspended) {
      const restored = { ...next, mistake, suspended: false, undecided: false };
      return restored.dismissed || isBusyWithStudent(restored) ? restored : showAlert(restored, mistake, "notebook");
    }
    if (trigger === "manual" && next.dismissed) return showAlert(next, mistake, "notebook");
    if (trigger === "manual" && next.assistance.length === 0) return showAlert(next, mistake, "notebook");
    return { ...next, mistake };
  }
  // A different one is in hand. This one waits.
  if (next.mistake && !next.suspended && trigger !== "manual") {
    const record = { mistake, assistance: [], dismissed: entry?.dismissed ?? false, suspended: false };
    return { ...next, parked: { ...next.parked, [mistake.id]: record } };
  }
  if (entry?.dismissed && trigger !== "manual") {
    const parked = park(next);
    return { ...parked, mistake, mistakeSource: "notebook", dismissed: true, suspended: false, undecided: false, assistance: [] };
  }
  return showAlert(park(next), mistake, "notebook");
}

function applyResult(model: DuckModel, result: AnalyzeResponse, trigger: Trigger, replaced: Record<string, string> = {}): DuckModel {
  // A stale reply is not information about the current page.
  if (result.stale) return model;

  const replacedIds = new Set(Object.values(replaced));
  const resolved = result.resolvedMistakeIds;
  let next: DuckModel = { ...model, issue: result.modelIssue, seen: without(model.seen, resolved), clarification: result.clarification ?? null };
  next = applyResolved(next, resolved, replacedIds);

  if (result.status === "confirmed_error") {
    const mistakes = mistakesIn(result);
    if (mistakes.length) {
      // The one that wants showing goes last, so that it's the one left selected.
      const primary = result.mistake?.id;
      const ordered = [...mistakes.filter((mistake) => mistake.id !== primary), ...mistakes.filter((mistake) => mistake.id === primary)];
      const settledDown = next.state === "resolved";
      const before = next;
      for (const mistake of ordered) next = applyMistake(next, mistake, trigger, replaced[mistake.id]);
      // A mistake just fixed gets its "That's it". Others found again aren't news, and stay put.
      if (settledDown && next.quackCount === before.quackCount && !Object.keys(replaced).length) {
        return { ...next, ...parkAllButResolved(next, before) };
      }
      return next;
    }
  }

  // One reading still finds it, so as far as Quack can tell it's still there.
  if (next.suspended && next.mistake && result.reason === "fix_disputed") {
    const restored = { ...next, suspended: false, undecided: false };
    return restored.dismissed || isBusyWithStudent(restored) ? restored : showAlert(restored, next.mistake, "notebook");
  }
  // Right as far as it goes. Quiet, and only when the question asked for more.
  if (result.reminder === "missing_constant" && next.state !== "resolved") return note(next, NEEDS_CONSTANT_TEXT);
  if (trigger !== "manual") return next;
  if (result.modelIssue) return note(next, result.modelIssue.message);
  if (next.state === "resolved") return next;
  // The question is on screen, which says all there is to say.
  if (next.clarification) return next;
  // Never "looks right" with a mistake still held, unresolved.
  if (next.suspended && next.mistake) return note(next, MANUAL_UNCERTAIN_TEXT);
  if (result.status === "no_error") return note(next, MANUAL_NO_ERROR_TEXT);
  return note(next, result.status === "unsupported" ? MANUAL_UNSUPPORTED_TEXT : MANUAL_UNCERTAIN_TEXT);
}

// The student has just fixed something. Mistakes that are still there wait, rather than taking the
// duck's bubble straight back, and come forward once "That's it" has been read.
function parkAllButResolved(next: DuckModel, resolved: DuckModel): Partial<DuckModel> {
  const parked = park(next);
  return { ...parked, state: "resolved", bubble: RESOLVED_TEXT, mistake: null, assistance: [], dismissed: false, suspended: false, undecided: false, resolvedCount: resolved.resolvedCount };
}

function applyImportResult(model: DuckModel, result: AnalyzeResponse): DuckModel {
  const next = { ...model, issue: result.modelIssue };
  if (result.status === "confirmed_error" && result.mistake) {
    return { ...showAlert(park(next), result.mistake, "import"), quackCount: next.quackCount + 1 };
  }
  if (result.modelIssue) return note(next, result.modelIssue.message);
  if (result.status === "no_error") return note(next, MANUAL_NO_ERROR_TEXT);
  if (result.reason === "ambiguous_intent") return note(next, MANUAL_AMBIGUOUS_TEXT);
  return note(next, result.status === "unsupported" ? MANUAL_UNSUPPORTED_TEXT : MANUAL_UNCERTAIN_TEXT);
}

export function duckReducer(model: DuckModel, event: DuckEvent): DuckModel {
  switch (event.type) {
    case "check_started":
      return model.state === "idle" ? { ...model, state: "checking" } : model;
    case "check_finished":
      return model.state === "checking" ? { ...model, state: "idle" } : model;
    case "restore":
      return { ...model, seen: { ...event.seen, ...model.seen } };
    case "result":
      return applyResult(model.state === "checking" ? { ...model, state: "idle" } : model, event.result, event.trigger, event.replaced);
    case "import_result":
      return applyImportResult(model.state === "checking" ? { ...model, state: "idle" } : model, event.result);
    case "request_failed": {
      const state = model.state === "checking" ? "idle" : model.state;
      const next = { ...model, state, issue: event.issue };
      // The pill shows the problem. An alert the student hasn't dealt with stays as it is.
      if (event.trigger !== "manual" || isAlertShowing(next) || isBusyWithStudent(next)) return next;
      return { ...next, bubble: OFFLINE_TEXT };
    }
    case "page_empty": {
      const next = { ...model, seen: {}, parked: {}, choosing: false };
      // Gone, but nothing has shown it was put right, so there's nothing to congratulate.
      if (next.mistake && next.mistakeSource === "notebook") return quietlyGone(next);
      return event.trigger === "manual" ? note(next, MANUAL_EMPTY_TEXT) : next;
    }
    case "mistakes_erased": {
      const parked = { ...model.parked };
      for (const id of event.ids) delete parked[id];
      const next = { ...model, parked, seen: without(model.seen, event.ids) };
      if (next.mistake && next.mistakeSource === "notebook" && event.ids.includes(next.mistake.id)) return promote(quietlyGone(next));
      return next;
    }
    case "mistake_touched": {
      const waiting = model.parked[event.id];
      if (waiting) {
        return waiting.suspended && !waiting.undecided ? model : { ...model, parked: { ...model.parked, [event.id]: { ...waiting, suspended: true, undecided: false } } };
      }
      if (model.mistake?.id !== event.id || model.mistakeSource !== "notebook") return model;
      if (model.suspended) return model.undecided ? { ...model, undecided: false } : model;
      // A hint the student is following stays open while they fix it, marked as being about what
      // was there before. A bare alert goes.
      const next = { ...model, suspended: true, undecided: false, loadingHelp: false };
      return model.state === "alert" ? { ...next, state: "idle", bubble: null } : next;
    }
    case "changes_undecided": {
      const parked = { ...model.parked };
      for (const id of event.ids) {
        if (parked[id]?.suspended) parked[id] = { ...parked[id], undecided: true };
      }
      if (!model.mistake || !model.suspended || !event.ids.includes(model.mistake.id)) return { ...model, parked };
      return { ...model, parked, undecided: true };
    }
    case "select": {
      if (model.mistake?.id === event.id) return reopen({ ...model, choosing: false });
      if (!model.parked[event.id]) return { ...model, choosing: false };
      return reopen({ ...unpark(park(model), event.id), choosing: false });
    }
    case "rewritten": {
      const counted = { ...model, resolvedCount: model.resolvedCount + 1 };
      return model.state === "hint" || model.state === "explaining" ? counted : { ...counted, state: "resolved", bubble: RESOLVED_TEXT };
    }
    case "choose":
      return { ...model, choosing: activeMistakes(model).length > 1 && !model.choosing };
    case "help_requested":
      return { ...model, loadingHelp: true };
    case "help_received": {
      if (!model.mistake || !model.loadingHelp) return model;
      // Written for what was on the line before it was changed, and the student hasn't seen it.
      if (model.suspended) return { ...model, loadingHelp: false };
      return {
        ...model,
        state: event.level >= 3 ? "explaining" : "hint",
        bubble: null,
        dismissed: false,
        loadingHelp: false,
        issue: event.issue,
        assistance: [...model.assistance, { level: event.level, text: event.text, source: event.source }],
      };
    }
    case "help_failed":
      return { ...model, loadingHelp: false, issue: event.issue };
    case "help_settled":
      return { ...model, loadingHelp: false };
    case "dismiss": {
      const id = model.mistake?.id;
      const seen = id && model.seen[id] ? { ...model.seen, [id]: { dismissed: true } } : model.seen;
      return { ...model, state: "idle", bubble: null, seen, dismissed: model.mistake !== null, loadingHelp: false };
    }
    case "reopen":
      return reopen(model);
    case "clarified":
      return event.text ? note({ ...model, clarification: null }, event.text) : { ...model, clarification: null };
    case "bubble_expired":
      return model.state === "resolved"
        ? promote({ ...model, state: "idle", bubble: null })
        : model.state === "idle"
          ? { ...model, bubble: null }
          : model;
    case "say":
      return note(model, event.text);
  }
}

// What the duck should look like, given that sound is a separate setting.
export function visibleState(state: DuckModel["state"], muted: boolean): DuckState {
  return state === "idle" && muted ? "muted" : state;
}

export function nextHelpLevel(model: DuckModel): 1 | 2 | 3 {
  const highest = model.assistance.reduce((top, entry) => Math.max(top, entry.level), 0);
  return Math.min(highest + 1, 3) as 1 | 2 | 3;
}
