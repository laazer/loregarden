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
export type ChatThinkingSocketStatus = "connecting" | "open" | "closed";

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

export class ChatThinkingSocket {
  private socket: WebSocket | null = null;
  private closed = false;
  private attempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private lastSeq = 0;
  private readonly url: string;
  private readonly handlers: ChatThinkingSocketHandlers;
  private readonly factory: (url: string) => WebSocket;

  constructor(
    url: string,
    handlers: ChatThinkingSocketHandlers,
    /** Injectable so tests can drive a fake without a live server. */
    factory: (url: string) => WebSocket = (u) => new WebSocket(u),
  ) {
    this.url = url;
    this.handlers = handlers;
    this.factory = factory;
  }

  open(): void {
    if (this.closed) return;

    this.handlers.onStatus("connecting");
    const socket = this.factory(this.url);
    this.socket = socket;

    socket.onopen = () => {
      this.attempts = 0;
      this.handlers.onStatus("open");
    };

    socket.onmessage = (event: MessageEvent) => {
      if (typeof event.data !== "string") return;
      let message: { type?: string; data?: ChatThinkingFrame };
      try {
        message = JSON.parse(event.data);
      } catch {
        // A frame we cannot parse is the server's problem, not a reason to
        // tear down a working connection.
        return;
      }
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
    };

    socket.onclose = () => this.scheduleReconnect();
    // onerror carries no detail by design; onclose always follows it and is
    // where the recovery belongs.
    socket.onerror = () => {};
  }

  close(): void {
    this.closed = true;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.socket) {
      // Drop the handlers first: a close we asked for must not be reported as
      // a connection that dropped, or the caller falls back to polling on its
      // way out.
      this.socket.onopen = null;
      this.socket.onmessage = null;
      this.socket.onclose = null;
      this.socket.onerror = null;
      this.socket.close();
      this.socket = null;
    }
  }

  private scheduleReconnect(): void {
    this.socket = null;
    if (this.closed) return;

    // "closed", not "connecting" — the caller polls during the wait rather
    // than sitting on a hopeful state showing a frozen transcript.
    this.handlers.onStatus("closed");

    const delay = Math.min(
      BASE_RECONNECT_DELAY_MS * 2 ** this.attempts,
      MAX_RECONNECT_DELAY_MS,
    );
    this.attempts += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.open();
    }, delay);
  }
}
