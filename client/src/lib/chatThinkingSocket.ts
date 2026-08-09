/**
 * The wire between a chat panel and the reasoning of the turn it is watching.
 *
 * Kept out of React for the same reason `queueSocket` is: the protocol —
 * framing, ordering, reconnect, what "the turn is over" means — is testable
 * without rendering anything, and a bug in it does not need a component to
 * reproduce.
 *
 * One socket per turn, not per surface. The server keys the channel by the
 * `active_turn_id` every chat surface already publishes, so Home chat, branch
 * triage and ticket triage all speak this without knowing about each other.
 */

import { ReconnectingSocket, type SocketStatus } from "./reconnectingSocket";

/** One frame of a turn's reasoning. Always the whole transcript, not a delta. */
export interface ChatThinkingFrame {
  turn_id: string;
  /** Reasoning and tool steps, interleaved in the order they happened. */
  content: string;
  /**
   * The reply as it is being written.
   *
   * Separate from `content` because it is not reasoning and never becomes part
   * of the settled message — that message *is* the reply. It streams because a
   * read-only turn emits an empty thinking block, so the reply is the only
   * thing that moves and the panel would otherwise sit blank.
   */
  answer: string;
  /** What the agent is doing right now, e.g. "Read · src/app.tsx". */
  activity: string;
  /**
   * Monotonic within a turn. Frames carry the full transcript, so one that
   * arrives late would rewind the panel — the reader drops anything older than
   * what it has already shown.
   */
  seq: number;
}

export const EMPTY_THINKING_FRAME: ChatThinkingFrame = {
  turn_id: "",
  content: "",
  answer: "",
  activity: "",
  seq: 0,
};

/** Three states, matching `QueueSocket`: a socket is trying, up, or down. */
export type ChatThinkingSocketStatus = SocketStatus;

export interface ChatThinkingSocketHandlers {
  onFrame: (frame: ChatThinkingFrame) => void;
  onStatus: (status: ChatThinkingSocketStatus) => void;
  /** The turn settled; no more reasoning is coming on this channel. */
  onDone?: () => void;
}

/** First reconnect delay, doubling from here. */
export const BASE_RECONNECT_DELAY_MS = 500;

/**
 * Ceiling for the backoff.
 *
 * Much lower than the queue socket's: a turn lasts minutes at most, and a
 * thirty-second gap in a two-minute turn is most of it. There is no long quiet
 * period to be polite about here.
 */
export const MAX_RECONNECT_DELAY_MS = 4000;

export function chatThinkingSocketUrl(apiBase: string, turnId: string): string {
  const base = apiBase.replace(/\/$/, "").replace(/^http/, "ws");
  return `${base}/ws/chat-turns/${encodeURIComponent(turnId)}`;
}

export class ChatThinkingSocket extends ReconnectingSocket<ChatThinkingSocketHandlers> {
  private lastSeq = 0;
  protected readonly policy = {
    baseDelayMs: BASE_RECONNECT_DELAY_MS,
    maxDelayMs: MAX_RECONNECT_DELAY_MS,
  };

  protected handleMessage(raw: unknown): void {
    const message = raw as { type?: string; data?: ChatThinkingFrame };
    if (message?.type === "chat_thinking_done") {
      // Stop reconnecting: the turn is over, and a channel for a settled
      // turn would retry forever against a server with nothing to say.
      this.close();
      this.handlers.onDone?.();
      return;
    }
    if (message?.type !== "chat_thinking" || !message.data) return;
    const frame = message.data;
    if (frame.seq < this.lastSeq) return;
    this.lastSeq = frame.seq;
    this.handlers.onFrame(frame);
  }
}
