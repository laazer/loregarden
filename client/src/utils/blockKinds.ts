import type { BlockedOn, BlockKind, TicketState, WaitingOnRef } from "../api/types";
import { ticketStateLabel } from "../lib/ticketStates";

/** Who can unblock it, as a badge word. */
export function blockKindLabel(kind: BlockKind): string {
  switch (kind) {
    case "harness":
      return "harness";
    case "work":
      return "agent work";
    case "decision":
      return "your decision";
    case "human_action":
      return "your action";
    case "tickets":
      return "other tickets";
    case "conflict":
      return "merge conflict";
  }
}

/** What the kind means for the person reading it — whether they need to act. */
export function blockKindMeaning(kind: BlockKind): string {
  switch (kind) {
    case "harness":
      return "The environment or the control plane failed, not the work. No action needed from you; the control plane retries or repairs it.";
    case "work":
      return "The agent stopped on something an agent can still do. No action needed from you; a repair run picks it up.";
    case "decision":
      return "A choice only you should make. Answer the question in the inbox and the stage reruns on its own.";
    case "human_action":
      return "Needs your hands — credentials, hardware, or an external account. The inbox item says what.";
    case "tickets":
      return "Nothing is owed on this ticket itself: a ticket under it is blocked. Open the ones it is waiting on; this one follows when they clear.";
    case "conflict":
      return "Its branch conflicts with work that landed before it. A merge turn tries to resolve it while it has attempts left; after that, merge the target into the branch by hand and requeue.";
  }
}

/** The operator's sort of a stuck ticket: theirs to act on, or waiting on something else. */
export const BLOCKED_ON_LABELS: Record<BlockedOn, string> = {
  person: "Needs you",
  tickets: "Waiting on tickets",
  agent: "Agent can clear",
};

/** One blocker, as a link label: "lg-x-22 · needs you". */
export function waitingOnLabel(ref: WaitingOnRef): string {
  return ref.blocked_on === "person" ? `${ref.external_id} · needs you` : ref.external_id;
}

/** "Waiting on lg-x-22 · needs you, lg-x-23 +2" — short enough for a board tile. */
export function waitingOnSummary(refs: WaitingOnRef[], shown = 2): string {
  const names = refs.slice(0, shown).map(waitingOnLabel).join(", ");
  const more = refs.length > shown ? ` +${refs.length - shown}` : "";
  return `Waiting on ${names}${more}`;
}

/** "Blocked · Needs you", "Backlog · Waiting on tickets", or just the state. */
export function blockedOnStateLabel(state: TicketState, blockedOn: BlockedOn | null | undefined): string {
  const label = ticketStateLabel(state);
  return blockedOn ? `${label} · ${BLOCKED_ON_LABELS[blockedOn]}` : label;
}
