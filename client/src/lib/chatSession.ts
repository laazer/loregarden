import type { ChatMessageView } from "../components/chat/chatUtils";

export type ChatSessionKind = "ticket-triage" | "branch-triage" | "ticket-studio";

/**
 * One conversation, however it is transported.
 *
 * The four chat surfaces already render through the same components and speak
 * the same `ChatMessageView`. What they did not share was a description of the
 * conversation itself, so anything wanting to bind to "whichever chat this
 * screen is showing" had to know which of the four it was talking to.
 *
 * Every field here is one all the real surfaces already had. Nothing is
 * included on the strength of what the dock might want later — a field with no
 * current implementation is a guess, and guesses in an interface are paid for
 * by every implementer.
 */
export interface ChatSession {
  kind: ChatSessionKind;
  /** Stable identity of the bound conversation: ticket id, or slug+branch. */
  id: string;
  messages: ChatMessageView[];
  /**
   * Server-derived, never promise-derived. A turn outlives the request that
   * started it, so a dropped connection or a reload must not strand the
   * composer with a stuck spinner or a falsely idle one.
   */
  isBusy: boolean;
  /** First load only — distinct from `isBusy`, which means the agent is working. */
  isLoading: boolean;
  /** Last send failure, already formatted for display, or null. */
  error: string | null;
  /**
   * The conversation itself could not be loaded — a different thing from a
   * send that failed, and shown differently: one means "this chat is
   * unavailable", the other means "your message did not go".
   */
  loadError: boolean;
  /** Resolves once the turn is accepted; rejects if the send failed. */
  send: (content: string, options?: ChatSendOptions) => Promise<unknown>;
}

export interface ChatSendOptions {
  /** Approve the agent's tool calls without prompting for this turn. */
  autoApprove?: boolean;
}

/**
 * Whether a run status means the agent is still working.
 *
 * All three session owners derived this identically from their own snapshot;
 * stating it once keeps "busy" from drifting between surfaces.
 */
export function isRunStatusBusy(runStatus: string | undefined | null): boolean {
  return runStatus ? runStatus !== "idle" : false;
}
