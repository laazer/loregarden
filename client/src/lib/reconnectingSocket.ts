/**
 * The reconnect lifecycle every long-lived socket in here shares.
 *
 * Only the lifecycle: opening, backing off, and the difference between a drop
 * and a close we asked for. Framing, ordering and what a message *means* stay
 * with the subclass — those are the parts that actually differ between the
 * queue socket and a chat turn's thinking channel, and folding them in here
 * would trade one duplication for a switch statement.
 */

/**
 * Deliberately three states, not four.
 *
 * An earlier client had a separate 'error' that behaved identically to
 * 'disconnected' for every consumer, and an 'error' that never cleared was how
 * a dashboard got stuck. A socket is either trying, up, or down.
 */
export type SocketStatus = "connecting" | "open" | "closed";

/** How long to wait before each retry. Both bounds are per-socket: a turn that
 * lasts two minutes cannot afford the queue socket's thirty-second ceiling. */
export interface ReconnectPolicy {
  /** First reconnect delay, doubling from here. */
  baseDelayMs: number;
  /** Ceiling for the backoff. */
  maxDelayMs: number;
}

/** The one thing every socket's handlers must offer: where state goes. */
export interface SocketStatusHandler {
  onStatus: (status: SocketStatus) => void;
}

export abstract class ReconnectingSocket<THandlers extends SocketStatusHandler> {
  private socket: WebSocket | null = null;
  private closed = false;
  private attempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly url: string;
  private readonly factory: (url: string) => WebSocket;
  protected readonly handlers: THandlers;

  /**
   * Declared by the subclass rather than passed up through a constructor.
   *
   * Every subclass wired one identically — take a url, handlers and a factory,
   * hand the base a pair of delay constants — so the constructor was the same
   * function written twice. A field says the same thing without the ceremony,
   * and it is read only when a reconnect is scheduled, long after
   * initialization order stops mattering.
   */
  protected abstract readonly policy: ReconnectPolicy;

  constructor(
    url: string,
    handlers: THandlers,
    /** Injectable so tests can drive a fake without a live server. */
    factory: (url: string) => WebSocket = (u) => new WebSocket(u),
  ) {
    this.url = url;
    this.handlers = handlers;
    this.factory = factory;
  }

  open(): void {
    if (this.closed) return;

    this.emitStatus("connecting");
    const socket = this.factory(this.url);
    this.socket = socket;

    socket.onopen = () => {
      // Reset here rather than on the first message: the connection is up
      // whether or not the server has anything to say yet, and a backoff that
      // only resets on data would keep growing across quiet reconnects.
      this.attempts = 0;
      this.emitStatus("open");
    };

    socket.onmessage = (event: MessageEvent) => {
      if (typeof event.data !== "string") return;
      let message: unknown;
      try {
        message = JSON.parse(event.data);
      } catch {
        // A frame we cannot parse is the server's problem, not a reason to
        // tear down a working connection.
        return;
      }
      this.handleMessage(message);
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

  /** One parsed frame. Subclasses own the shape and what to do with it. */
  protected abstract handleMessage(message: unknown): void;

  /** Connection state, straight to the caller. Framing differs between
   * sockets; "trying, up, or down" does not. */
  protected emitStatus(status: SocketStatus): void {
    this.handlers.onStatus(status);
  }

  private scheduleReconnect(): void {
    this.socket = null;
    if (this.closed) return;

    // "closed", not "connecting" — the caller must start polling now, during
    // the wait, rather than sit on a hopeful state showing nothing.
    this.emitStatus("closed");

    const delay = Math.min(
      this.policy.baseDelayMs * 2 ** this.attempts,
      this.policy.maxDelayMs,
    );
    this.attempts += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.open();
    }, delay);
  }
}
