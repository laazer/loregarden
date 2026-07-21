/**
 * The wire between a browser terminal and the workspace shell behind it.
 *
 * Kept free of xterm so the protocol can be tested without a renderer: jsdom
 * has no canvas, and a bug in framing should not need a DOM to catch.
 */

export type TerminalStatus = "connecting" | "open" | "closed";

/** What ended the session, in words an operator can act on. */
export interface TerminalEnd {
  code: number;
  reason: string;
}

export interface TerminalSocketHandlers {
  onData: (chunk: string) => void;
  onStatus: (status: TerminalStatus) => void;
  onEnd: (end: TerminalEnd) => void;
}

/** Close code for "the server refused this", per RFC 6455. */
export const POLICY_VIOLATION = 1008;

/**
 * Browsers report an abnormal close with no reason when the handshake itself
 * fails, so there is nothing to show unless we supply the words ourselves.
 */
export const ABNORMAL_CLOSURE = 1006;

export function terminalSocketUrl(workspaceSlug: string, apiBase: string): string {
  const base = apiBase.replace(/\/$/, "").replace(/^http/, "ws");
  return `${base}/terminal/${encodeURIComponent(workspaceSlug)}`;
}

/**
 * A shell session for as long as the socket is open.
 *
 * Deliberately does not reconnect. The server reaps the shell when the socket
 * drops, so a reconnect is a *new* shell with a fresh cwd and no history —
 * reconnecting silently would look like the terminal wiped itself. Ending
 * visibly and letting the operator restart says what actually happened.
 */
export class TerminalSocket {
  private socket: WebSocket | null = null;
  private ended = false;
  private readonly url: string;
  private readonly handlers: TerminalSocketHandlers;
  private readonly factory: (url: string) => WebSocket;

  constructor(
    url: string,
    handlers: TerminalSocketHandlers,
    /** Injectable so tests can drive a fake without a live server. */
    factory: (url: string) => WebSocket = (u) => new WebSocket(u),
  ) {
    this.url = url;
    this.handlers = handlers;
    this.factory = factory;
  }

  open(): void {
    this.handlers.onStatus("connecting");
    const socket = this.factory(this.url);
    this.socket = socket;

    socket.onopen = () => this.handlers.onStatus("open");
    socket.onmessage = (event: MessageEvent) => {
      this.handlers.onData(typeof event.data === "string" ? event.data : "");
    };
    socket.onclose = (event: CloseEvent) => this.finish(event.code, event.reason);
    // onerror carries no detail by design (it would leak cross-origin info), so
    // there is nothing to report here that onclose will not report better.
    socket.onerror = () => {};
  }

  /**
   * Keystrokes, always wrapped rather than sent raw.
   *
   * The server treats a frame starting `{"type"` as a control message, so
   * pasting JSON that happens to begin that way — an MCP server config, say,
   * which is very much a thing you would paste in *this* app — would be
   * swallowed instead of typed. Wrapping every keystroke removes the ambiguity:
   * pasted text is always payload, never protocol.
   */
  send(data: string): void {
    this.control({ type: "input", data });
  }

  resize(rows: number, cols: number): void {
    this.control({ type: "resize", rows, cols });
  }

  close(): void {
    // Mark first: closing on purpose should not surface as a session that died.
    this.ended = true;
    this.socket?.close();
    this.socket = null;
  }

  private control(message: Record<string, unknown>): void {
    if (this.socket?.readyState !== WebSocket.OPEN) return;
    // `type` must serialise first — the server sniffs the literal prefix
    // `{"type"` to tell a control frame from raw input.
    this.socket.send(JSON.stringify(message));
  }

  private finish(code: number, reason: string): void {
    if (this.ended) return;
    this.ended = true;
    this.socket = null;
    this.handlers.onStatus("closed");
    this.handlers.onEnd({ code, reason: reason || describeClose(code) });
  }
}

/**
 * Words for a close the server did not explain.
 *
 * A rejected handshake never carries a reason — the browser only exposes one
 * when a close frame arrives on an established connection — so without this an
 * operator sees an empty terminal and no cause.
 */
export function describeClose(code: number): string {
  if (code === POLICY_VIOLATION) return "The server refused the connection.";
  if (code === ABNORMAL_CLOSURE) {
    return "Could not reach the terminal. Is the control plane running, and is LOREGARDEN_API_TOKEN unset?";
  }
  return "The shell exited.";
}
