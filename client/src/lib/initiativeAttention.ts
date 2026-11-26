import type { InitiativeView } from "../api/client";
import type { TicketState } from "../api/types";

/** Row states an operator acts on, in the order the "Needs you" cell names them. */
const ATTENTION: readonly TicketState[] = ["blocked", "in_progress", "parked"];
const RESOLVED: ReadonlySet<TicketState> = new Set(["done", "wont_do"]);

export function attentionOf(initiative: InitiativeView): [TicketState, number][] {
  return ATTENTION.map((state): [TicketState, number] => [
    state,
    initiative.milestones.filter((m) => m.state === state).length,
  ]).filter(([, n]) => n > 0);
}

function countOf(initiative: InitiativeView, state: TicketState): number {
  return initiative.milestones.filter((m) => m.state === state).length;
}

/**
 * The order the overview lists initiatives in, and so which one opens by default:
 * blocked work first, then the most work in flight, then by priority; finished
 * initiatives last.
 */
export function sortByAttention(list: readonly InitiativeView[]): InitiativeView[] {
  return [...list].sort(
    (a, b) =>
      Number(RESOLVED.has(a.state)) - Number(RESOLVED.has(b.state)) ||
      countOf(b, "blocked") - countOf(a, "blocked") ||
      countOf(b, "in_progress") - countOf(a, "in_progress") ||
      a.priority - b.priority ||
      a.title.localeCompare(b.title),
  );
}
