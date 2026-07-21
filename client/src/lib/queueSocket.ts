/**
 * The wire between the queue dashboard and the control plane's queue socket.
 *
 * Kept out of React so the protocol — framing, reconnect, what "connected"
 * means — can be tested without rendering anything, and so a bug in it does
 * not need a component to reproduce.
 *
 * Replaces a Socket.IO client that could never connect: nothing on the server
 * ever instantiated the Flask-SocketIO server it dialled, so it sat in
 * 'connecting' forever and the dashboard's badge could only ever read
 * "Polling".
 */

export interface ActiveRun {
  run_id: string;
  ticket_id: string;
  slot_number: number;
  elapsed_seconds: number;
  status: string;
  agent_id: string;
}

export interface QueuedRun {
  run_id: string;
  ticket_id: string;
  position: number;
  estimated_start_at: string;
  wait_seconds: number;
  agent_id: string;
}

export interface ParallelStats {
  max_concurrent: number;
  active_count: number;
  available_slots: number;
  queued_count: number;
  total_slots_occupied: number;
  queue_wait_time_minutes: number;
}

/** What to show before the first snapshot arrives. */
export const DEFAULT_PARALLEL_STATS: ParallelStats = {
  max_concurrent: 3,
  active_count: 0,
  available_slots: 3,
  queued_count: 0,
  total_slots_occupied: 0,
  queue_wait_time_minutes: 0,
};

/** What `/api/parallel/status/{id}` returns, and what the socket pushes. */
export interface QueueStatusSnapshot {
  active_runs: ActiveRun[];
  queued_runs: QueuedRun[];
  available_slots: number;
  total_slots: number;
  queue_length: number;
  stats: ParallelStats;
}

/**
 * Deliberately three states, not four.
 *
 * The old client had a separate 'error' that behaved identically to
 * 'disconnected' for every consumer, and an 'error' that never cleared was
 * how the dashboard got stuck. A socket is either trying, up, or down.
 */
export type QueueSocketStatus = "connecting" | "open" | "closed";

export interface QueueSocketHandlers {
  onSnapshot: (snapshot: QueueStatusSnapshot) => void;
  onStatus: (status: QueueSocketStatus) => void;
}

/** First reconnect delay, doubling from here. */
export const BASE_RECONNECT_DELAY_MS = 1000;

/** Ceiling for the backoff. Long enough to be polite, short enough that a
 * backend restart is picked up while the operator is still looking. */
export const MAX_RECONNECT_DELAY_MS = 30000;

export function queueSocketUrl(workspaceId: string, apiBase: string): string {
  const base = apiBase.replace(/\/$/, "").replace(/^http/, "ws");
  return `${base}/ws/queue/${encodeURIComponent(workspaceId)}`;
}

/**
 * A queue subscription that keeps trying.
 *
 * Reconnects, unlike TerminalSocket — there is no session to lose here, only a
 * snapshot to refresh, and the control plane restarts on every backend edit.
 * Callers are told the truth about the connection at every point so they can
 * poll while it is down instead of pretending.
 */
export class QueueSocket {
  private socket: WebSocket | null = null;
  private closed = false;
  private attempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly url: string;
  private readonly handlers: QueueSocketHandlers;
  private readonly factory: (url: string) => WebSocket;

  constructor(
    url: string,
    handlers: QueueSocketHandlers,
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
      // Reset here rather than on the first message: the connection is up
      // whether or not the server has anything to say yet, and a backoff that
      // only resets on data would keep growing across quiet reconnects.
      this.attempts = 0;
      this.handlers.onStatus("open");
    };

    socket.onmessage = (event: MessageEvent) => {
      if (typeof event.data !== "string") return;
      let message: { type?: string; data?: QueueStatusSnapshot };
      try {
        message = JSON.parse(event.data);
      } catch {
        // A frame we cannot parse is the server's problem, not a reason to
        // tear down a working connection.
        return;
      }
      if (message?.type === "queue_status" && message.data) {
        this.handlers.onSnapshot(message.data);
      }
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
      // way out of the page.
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

    // "closed", not "connecting" — the caller must start polling now, during
    // the wait, rather than sit on a hopeful state showing nothing.
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
