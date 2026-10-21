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

/** One blocker, as a link label: "lg-x-22 · needs you". A blocker in another
 *  workspace than `here` is prefixed with it, since it is not on this board. */
export function waitingOnLabel(ref: WaitingOnRef, here?: string): string {
  const where = here && ref.workspace_slug && ref.workspace_slug !== here ? `${ref.workspace_slug}: ` : "";
  return ref.blocked_on === "person"
    ? `${where}${ref.external_id} · needs you`
    : `${where}${ref.external_id}`;
}

/** What the blocker itself is doing, in words: "needs you", "in progress". */
export function waitingOnStatus(ref: WaitingOnRef): string {
  if (ref.blocked_on === "person") return "needs you";
  if (ref.blocked_on === "tickets") return "waiting on others";
  return ticketStateLabel(ref.state).toLowerCase();
}

/** "Blocked · Needs you", "Backlog · Waiting on tickets", or just the state. */
export function blockedOnStateLabel(state: TicketState, blockedOn: BlockedOn | null | undefined): string {
  const label = ticketStateLabel(state);
  return blockedOn ? `${label} · ${BLOCKED_ON_LABELS[blockedOn]}` : label;
}
