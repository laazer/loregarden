import type { ChatMessageView } from "../components/chat/chatUtils";
import type { ChatMode } from "../api/client";

export type ChatSessionKind =
  | "ticket-triage"
  | "branch-triage"
  | "ticket-studio"
  | "baxter-home";

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
  /**
   * The turn in flight, or null when nothing is running.
   *
   * Server-derived like `isBusy`, and for the same reason. Every surface
   * already publishes it — as `active_turn_id`, or `active_run_id` where the
   * run *is* the turn — so a consumer wanting to watch that turn's reasoning
   * asks the session rather than working out which snapshot field to read.
   */
  activeTurnId: string | null;
  /**
   * Whether this conversation can act, or can only answer.
   *
   * Server-resolved from the adapter behind the rail, and published by all
   * three surfaces. It reads as a property of the screen but is not: two
   * tickets in different workspaces can have the same panel and different
   * answers, and nothing in the transcript distinguishes them until a turn is
   * asked for something it silently cannot do.
   */
  canAct: boolean;
  /**
   * Why this rail is in the mode it is, and what would change it.
   *
   * Undefined only for a snapshot that predates the field. `canAct` remains the
   * boolean the UI branches on; this carries what to *say* about it.
   */
  chatMode?: ChatMode;
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
  /**
   * Stop the in-flight turn. Settles the pending row so the composer unlocks.
   *
   * Required, and that is the point. This was optional, and the two surfaces
   * that quietly declined to implement it — branch triage and ticket triage —
   * spent months with no way out of a hung turn: `AppActionBar` simply renders
   * no control when a session omits one, so nothing ever failed and nothing
   * ever said so. A required field means the next surface cannot ship without
   * answering the question.
   *
   * Every surface settles its own pending row; what differs is only where that
   * row lives, and whether there is an `AgentRun` behind it to cancel as well.
   */
  stop: () => Promise<unknown>;
  isStopping: boolean;
}

export interface ChatSendOptions {
  /** Approve the agent's tool calls without prompting for this turn. */
  autoApprove?: boolean;
  /**
   * A skill to put in front of this turn, picked from the composer's `/` menu.
   *
   * Only the Home Baxter thread carries one today; the other surfaces ignore
   * it, which is why their composers do not offer skills in the first place.
   */
  skill?: string;
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
