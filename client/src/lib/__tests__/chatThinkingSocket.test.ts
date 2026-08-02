/**
 * The thinking channel's protocol, driven by a fake that starts in CONNECTING
 * and only reaches OPEN when told to — the state a socket spends its first
 * moments in, and the one a caller must poll through rather than sit on.
 */

import {
  BASE_RECONNECT_DELAY_MS,
  ChatThinkingSocket,
  MAX_RECONNECT_DELAY_MS,
  chatThinkingSocketUrl,
} from "../chatThinkingSocket";
import type { ChatThinkingFrame, ChatThinkingSocketStatus } from "../chatThinkingSocket";

class FakeWebSocket {
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closeCalls = 0;

  readonly url: string;

  constructor(url: string) {
    this.url = url;
  }

  finishHandshake(): void {
    this.onopen?.();
  }

  deliver(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
  }

  deliverRaw(data: unknown): void {
    this.onmessage?.({ data } as MessageEvent);
  }

  drop(): void {
    this.onclose?.();
  }

  close(): void {
    this.closeCalls += 1;
  }
}

function frame(overrides: Partial<ChatThinkingFrame> = {}): ChatThinkingFrame {
  return {
    turn_id: "t1",
    content: "thinking",
    answer: "",
    activity: "Thinking",
    seq: 1,
    ...overrides,
  };
}

function harness() {
  const sockets: FakeWebSocket[] = [];
  const frames: ChatThinkingFrame[] = [];
  const statuses: ChatThinkingSocketStatus[] = [];
  let done = 0;

  const socket = new ChatThinkingSocket(
    "ws://test/ws/chat-turns/t1",
    {
      onFrame: (f) => frames.push(f),
      onStatus: (s) => statuses.push(s),
      onDone: () => {
        done += 1;
      },
    },
    (url) => {
      const fake = new FakeWebSocket(url);
      sockets.push(fake);
      return fake as unknown as WebSocket;
    },
  );

  return { socket, sockets, frames, statuses, doneCount: () => done };
}

describe("chatThinkingSocketUrl", () => {
  it("keys the channel by turn, over the api base's ws scheme", () => {
    expect(chatThinkingSocketUrl("http://127.0.0.1:8000", "abc")).toBe(
      "ws://127.0.0.1:8000/ws/chat-turns/abc",
    );
  });

  it("escapes a turn id rather than trusting it to be path-safe", () => {
    expect(chatThinkingSocketUrl("http://h", "a/b")).toBe("ws://h/ws/chat-turns/a%2Fb");
  });
});

describe("ChatThinkingSocket", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it("reports connecting before the handshake and open after it", () => {
    const h = harness();
    h.socket.open();
    expect(h.statuses).toEqual(["connecting"]);

    h.sockets[0].finishHandshake();
    expect(h.statuses).toEqual(["connecting", "open"]);
  });

  it("hands each frame to the caller", () => {
    const h = harness();
    h.socket.open();
    h.sockets[0].finishHandshake();
    h.sockets[0].deliver({ type: "chat_thinking", data: frame({ content: "one" }) });

    expect(h.frames.map((f) => f.content)).toEqual(["one"]);
  });

  it("drops a frame older than one already shown", () => {
    // Frames carry the whole transcript, so an out-of-order one would rewind
    // the panel mid-read.
    const h = harness();
    h.socket.open();
    h.sockets[0].finishHandshake();
    h.sockets[0].deliver({ type: "chat_thinking", data: frame({ content: "later", seq: 5 }) });
    h.sockets[0].deliver({ type: "chat_thinking", data: frame({ content: "earlier", seq: 2 }) });

    expect(h.frames.map((f) => f.content)).toEqual(["later"]);
  });

  it("ignores frames it cannot parse rather than tearing down the connection", () => {
    const h = harness();
    h.socket.open();
    h.sockets[0].finishHandshake();
    h.sockets[0].deliverRaw("{not json");
    h.sockets[0].deliver({ type: "chat_thinking", data: frame() });

    expect(h.frames).toHaveLength(1);
    expect(h.sockets[0].closeCalls).toBe(0);
  });

  it("stops for good when the turn settles", () => {
    // A settled turn's channel has nothing more to say; reconnecting to it
    // would retry forever.
    const h = harness();
    h.socket.open();
    h.sockets[0].finishHandshake();
    h.sockets[0].deliver({ type: "chat_thinking_done", data: {} });

    expect(h.doneCount()).toBe(1);
    h.sockets[0].drop();
    jest.advanceTimersByTime(MAX_RECONNECT_DELAY_MS * 4);
    expect(h.sockets).toHaveLength(1);
  });

  it("reports closed on a drop so the caller polls during the wait", () => {
    const h = harness();
    h.socket.open();
    h.sockets[0].finishHandshake();
    h.sockets[0].drop();

    expect(h.statuses).toEqual(["connecting", "open", "closed"]);
  });

  it("reconnects with a backoff that stops growing at the ceiling", () => {
    const h = harness();
    h.socket.open();

    for (let attempt = 0; attempt < 6; attempt += 1) {
      h.sockets[h.sockets.length - 1].drop();
      jest.advanceTimersByTime(MAX_RECONNECT_DELAY_MS);
    }

    expect(h.sockets).toHaveLength(7);
    expect(BASE_RECONNECT_DELAY_MS).toBeLessThan(MAX_RECONNECT_DELAY_MS);
  });

  it("does not report a close it was asked for as a dropped connection", () => {
    const h = harness();
    h.socket.open();
    h.sockets[0].finishHandshake();
    h.socket.close();
    jest.advanceTimersByTime(MAX_RECONNECT_DELAY_MS * 4);

    expect(h.statuses).toEqual(["connecting", "open"]);
    expect(h.sockets).toHaveLength(1);
  });
});
