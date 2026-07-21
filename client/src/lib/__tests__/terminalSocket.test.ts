import {
  ABNORMAL_CLOSURE,
  POLICY_VIOLATION,
  TerminalSocket,
  describeClose,
  terminalSocketUrl,
  type TerminalEnd,
  type TerminalStatus,
} from "../terminalSocket";

/** Enough of a WebSocket to drive the protocol without a server. */
class FakeSocket {
  static readonly OPEN = 1;
  readyState = FakeSocket.OPEN;
  sent: string[] = [];
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: (() => void) | null = null;

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.closed = true;
  }
}

function connect() {
  const socket = new FakeSocket();
  const data: string[] = [];
  const statuses: TerminalStatus[] = [];
  const ends: TerminalEnd[] = [];
  const terminal = new TerminalSocket(
    "ws://x/terminal/demo",
    {
      onData: (chunk) => data.push(chunk),
      onStatus: (status) => statuses.push(status),
      onEnd: (end) => ends.push(end),
    },
    () => socket as unknown as WebSocket,
  );
  terminal.open();
  return { terminal, socket, data, statuses, ends };
}

describe("terminalSocketUrl", () => {
  it("switches the scheme to ws, keeping the host the API is served from", () => {
    expect(terminalSocketUrl("demo", "http://127.0.0.1:8000")).toBe(
      "ws://127.0.0.1:8000/terminal/demo",
    );
  });

  it("upgrades to wss so a served-over-https app is not blocked as mixed content", () => {
    expect(terminalSocketUrl("demo", "https://lore.example")).toBe(
      "wss://lore.example/terminal/demo",
    );
  });

  it("encodes the slug rather than letting it reshape the path", () => {
    expect(terminalSocketUrl("a/../b", "http://x")).toBe("ws://x/terminal/a%2F..%2Fb");
  });
});

describe("framing", () => {
  it("wraps keystrokes so pasted JSON is typed, not parsed as a control frame", () => {
    const { terminal, socket } = connect();

    // The exact shape the server would otherwise mistake for protocol — and a
    // very plausible paste in an app whose whole job is MCP server configs.
    terminal.send('{"type":"resize","rows":1,"cols":1}');

    expect(JSON.parse(socket.sent[0])).toEqual({
      type: "input",
      data: '{"type":"resize","rows":1,"cols":1}',
    });
  });

  it("puts `type` first, which is the prefix the server sniffs for", () => {
    const { terminal, socket } = connect();

    terminal.resize(40, 120);

    expect(socket.sent[0].startsWith('{"type"')).toBe(true);
    expect(JSON.parse(socket.sent[0])).toEqual({ type: "resize", rows: 40, cols: 120 });
  });

  it("drops writes when the socket is not open instead of throwing at the caller", () => {
    const { terminal, socket } = connect();
    socket.readyState = 3; // CLOSED

    expect(() => terminal.send("ls\n")).not.toThrow();
    expect(socket.sent).toHaveLength(0);
  });
});

describe("lifecycle", () => {
  it("reports output as it arrives", () => {
    const { socket, data } = connect();

    socket.onmessage?.({ data: "hello" } as MessageEvent);

    expect(data).toEqual(["hello"]);
  });

  it("goes connecting → open → closed", () => {
    const { socket, statuses } = connect();

    socket.onopen?.();
    socket.onclose?.({ code: 1000, reason: "" } as CloseEvent);

    expect(statuses).toEqual(["connecting", "open", "closed"]);
  });

  it("passes the server's own words through when it explains itself", () => {
    const { socket, ends } = connect();

    socket.onclose?.({ code: POLICY_VIOLATION, reason: "Unknown workspace" } as CloseEvent);

    expect(ends).toEqual([{ code: POLICY_VIOLATION, reason: "Unknown workspace" }]);
  });

  it("supplies words when the server gives none, so a failure is never silent", () => {
    const { socket, ends } = connect();

    // What a browser reports for a handshake that never completed: no reason.
    socket.onclose?.({ code: ABNORMAL_CLOSURE, reason: "" } as CloseEvent);

    expect(ends[0].reason).toBe(describeClose(ABNORMAL_CLOSURE));
    expect(ends[0].reason.length).toBeGreaterThan(0);
  });

  it("does not report a session end when we are the ones closing it", () => {
    const { terminal, socket, ends } = connect();

    terminal.close();
    socket.onclose?.({ code: 1000, reason: "" } as CloseEvent);

    // Unmounting the panel is not a shell that died, and must not offer to
    // "start a new shell" over a component that is already gone.
    expect(ends).toEqual([]);
    expect(socket.closed).toBe(true);
  });

  it("reports an end once, even if close events repeat", () => {
    const { socket, ends } = connect();

    socket.onclose?.({ code: 1000, reason: "done" } as CloseEvent);
    socket.onclose?.({ code: 1000, reason: "done" } as CloseEvent);

    expect(ends).toHaveLength(1);
  });
});
