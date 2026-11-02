import type { AgentRetry, BlockedOn, BlockKind, TicketState, WaitingOnRef } from "../api/types";
import type { ChipStatus, StatusTone } from "../lib/statusTone";
import { ticketStateChip, ticketStateLabel } from "../lib/ticketStates";

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
  agent: "Agent can fix",
};

/** Who starts the fix for a block an agent can clear: the tag a row wears instead of "Blocked". */
export const AGENT_RETRY_LABELS: Record<AgentRetry, string> = {
  automatic: "Agent fixing",
  on_request: "Agent can fix",
};

/** The tag's hover text: that nothing is owed, and what (if anything) starts the fix. */
export const AGENT_RETRY_MEANING: Record<AgentRetry, string> = {
  automatic: "Nothing for you to do: the autopilot retries it on its next pass.",
  on_request: "No decision needed from you: running its blocked stage again starts the fix.",
};

interface StuckTicket {
  state: TicketState;
  blocked_on?: BlockedOn | null;
  agent_retry?: AgentRetry | null;
}

/** The agent's fix, when the block is one an agent can clear; null for any other ticket. */
export function agentFix(ticket: StuckTicket): AgentRetry | null {
  if (ticket.state !== "blocked" || ticket.blocked_on !== "agent") return null;
  // A server that predates `agent_retry` still said agent: nothing starts it on its own.
  return ticket.agent_retry ?? "on_request";
}

/** Its state chip, with an agent's block told apart: amber and named, not red "Blocked". */
export function stuckTicketChip(ticket: StuckTicket): ChipStatus & { title?: string } {
  const fix = agentFix(ticket);
  if (!fix) return ticketStateChip(ticket.state);
  return { tone: "wait", label: AGENT_RETRY_LABELS[fix], title: AGENT_RETRY_MEANING[fix] };
}

/** What a blocked ticket's own page says first: whose move it is, and what that means.
 *  An agent's kind with no re-run left reads as a person's, since the kind alone
 *  promised a repair that is not coming. */
export function blockHeadline(ticket: StuckTicket & { block_kind?: BlockKind | null }): {
  label: string;
  meaning: string;
} {
  const fix = agentFix(ticket);
  if (fix) return { label: `Blocked · ${AGENT_RETRY_LABELS[fix].toLowerCase()}`, meaning: AGENT_RETRY_MEANING[fix] };
  const kind = ticket.block_kind;
  if ((kind === "harness" || kind === "work") && ticket.blocked_on === "person") {
    return {
      label: "Blocked · needs you",
      meaning:
        "An agent already had its retry here and it blocked again. Read the block, then requeue it or decide how to go on.",
    };
  }
  return kind ? { label: `Blocked · ${blockKindLabel(kind)}`, meaning: blockKindMeaning(kind) } : { label: "Blocked", meaning: "" };
}

/** The tone alone, for a dot that carries no word of its own. */
export function stuckTicketTone(ticket: StuckTicket): StatusTone {
  return stuckTicketChip(ticket).tone;
}

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

/** "Blocked · Needs you", "Backlog · Waiting on tickets", "Blocked · Agent fixing", or just the state. */
export function blockedOnStateLabel(
  state: TicketState,
  blockedOn: BlockedOn | null | undefined,
  agentRetry?: AgentRetry | null,
): string {
  const label = ticketStateLabel(state);
  if (!blockedOn) return label;
  const fix = agentFix({ state, blocked_on: blockedOn, agent_retry: agentRetry });
  return `${label} · ${fix ? AGENT_RETRY_LABELS[fix] : BLOCKED_ON_LABELS[blockedOn]}`;
}
