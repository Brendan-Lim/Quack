/**
 * The class for a tool button: `tool-on` when it's the chosen one, `tool-idle` otherwise.
 *
 * The chosen look is keyed on this class rather than on `[aria-pressed]` in CSS. On the iPad on 2026-11-04
 * (evening), Safari kept a tap's sticky :hover on the chosen pen and painted the idle hover's grey over it for up
 * to 30 seconds, though that hover rule excluded `[aria-pressed="true"]`. A chosen tool now carries no class that
 * any hover rule matches. `aria-pressed` stays on the button for assistive technology.
 */
export function toolClass(selected: boolean | undefined, extra = ""): string {
  return `tool ${selected ? "tool-on" : "tool-idle"}${extra ? ` ${extra}` : ""}`;
}
