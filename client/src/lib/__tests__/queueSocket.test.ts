/**
 * The queue socket's protocol, driven by a fake that behaves like the real
 * thing — most importantly, one that starts in CONNECTING and only reaches
 * OPEN when told to. A fake that is OPEN from birth hides every bug about
 * what happens before a connection completes, which is exactly the state the
 * old Socket.IO client was stuck in forever.
 */

import {
  BASE_RECONNECT_DELAY_MS,
  MAX_RECONNECT_DELAY_MS,
  QueueSocket,
  queueSocketUrl,
} from '../queueSocket';
import type { QueueEvent, QueueSocketStatus, QueueStatusSnapshot } from '../queueSocket';

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;

  readyState = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closeCalls = 0;

  readonly url: string;

  constructor(url: string) {
    this.url = url;
  }

  /** The handshake completing, which is never instantaneous in a browser. */
  finishHandshake(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  deliver(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
  }

  deliverRaw(data: unknown): void {
    this.onmessage?.({ data } as MessageEvent);
  }

  /** The connection dropping — a server restart, a network blip. */
  drop(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  close(): void {
    this.closeCalls += 1;
    this.readyState = FakeWebSocket.CLOSED;
  }
}

const SNAPSHOT: QueueStatusSnapshot = {
  active_runs: [],
  queued_runs: [],
  lanes: [],
  available_slots: 3,
  total_slots: 3,
  queue_length: 0,
  stats: {
    max_concurrent: 3,
    active_count: 0,
    available_slots: 3,
    queued_count: 0,
    total_slots_occupied: 0,
    longest_wait_seconds: 0,
  },
};

function build() {
  const sockets: FakeWebSocket[] = [];
  const statuses: QueueSocketStatus[] = [];
  const snapshots: QueueStatusSnapshot[] = [];
  const events: QueueEvent[] = [];

  const socket = new QueueSocket(
    'ws://localhost:8000/ws/queue/ws-1',
    {
      onSnapshot: (snapshot) => snapshots.push(snapshot),
      onStatus: (status) => statuses.push(status),
      onEvent: (event) => events.push(event),
    },
    (url) => {
      const fake = new FakeWebSocket(url);
      sockets.push(fake);
      return fake as unknown as WebSocket;
    },
  );

  return { socket, sockets, statuses, snapshots, events };
}

describe('queueSocketUrl', () => {
  it('turns the API base into a websocket URL', () => {
    expect(queueSocketUrl('http://127.0.0.1:8000')).toBe('ws://127.0.0.1:8000/ws/queue');
  });

  it('upgrades https to wss', () => {
    expect(queueSocketUrl('https://example.test')).toBe('wss://example.test/ws/queue');
  });

  it('takes no workspace, because the queue has none', () => {
    // The path used to end in a workspace id, which is why it had to escape
    // one. One shared slot pool means one socket and a fixed path.
    expect(queueSocketUrl('http://x/')).toBe('ws://x/ws/queue');
  });
});

describe('QueueSocket', () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it('reports connecting before the handshake completes', () => {
    const { socket, statuses } = build();

    socket.open();

    // Not 'open' — the socket exists but the connection does not yet.
    expect(statuses).toEqual(['connecting']);
  });

  it('reports open only once the handshake completes', () => {
    const { socket, sockets, statuses } = build();
    socket.open();

    sockets[0].finishHandshake();

    expect(statuses).toEqual(['connecting', 'open']);
  });

  it('passes snapshots through', () => {
    const { socket, sockets, snapshots } = build();
    socket.open();
    sockets[0].finishHandshake();

    sockets[0].deliver({ type: 'queue_status', data: SNAPSHOT });

    expect(snapshots).toEqual([SNAPSHOT]);
  });

  it('ignores frames of other types', () => {
    const { socket, sockets, snapshots } = build();
    socket.open();
    sockets[0].finishHandshake();

    sockets[0].deliver({ type: 'something_else', data: SNAPSHOT });

    expect(snapshots).toEqual([]);
  });

  it('survives a frame it cannot parse', () => {
    const { socket, sockets, snapshots, statuses } = build();
    socket.open();
    sockets[0].finishHandshake();

    sockets[0].deliverRaw('not json at all');
    sockets[0].deliver({ type: 'queue_status', data: SNAPSHOT });

    // A bad frame is the server's problem; the connection keeps working.
    expect(snapshots).toEqual([SNAPSHOT]);
    expect(statuses).toEqual(['connecting', 'open']);
  });

  it('reports closed the moment the connection drops, not when it gives up', () => {
    const { socket, sockets, statuses } = build();
    socket.open();
    sockets[0].finishHandshake();

    sockets[0].drop();

    // The caller has to start polling now, during the backoff — reporting
    // 'connecting' here is how the old client showed nothing for 30 seconds.
    expect(statuses).toEqual(['connecting', 'open', 'closed']);
  });

  it('reconnects after a drop', () => {
    const { socket, sockets } = build();
    socket.open();
    sockets[0].finishHandshake();
    sockets[0].drop();

    jest.advanceTimersByTime(BASE_RECONNECT_DELAY_MS);

    expect(sockets).toHaveLength(2);
  });

  it('backs off between attempts and stops growing at the ceiling', () => {
    const { socket, sockets } = build();
    socket.open();

    sockets[0].drop();
    jest.advanceTimersByTime(BASE_RECONNECT_DELAY_MS - 1);
    expect(sockets).toHaveLength(1); // not yet

    jest.advanceTimersByTime(1);
    expect(sockets).toHaveLength(2);

    sockets[1].drop();
    jest.advanceTimersByTime(BASE_RECONNECT_DELAY_MS); // too soon now
    expect(sockets).toHaveLength(2);

    jest.advanceTimersByTime(BASE_RECONNECT_DELAY_MS);
    expect(sockets).toHaveLength(3);

    // However many failures, the wait never exceeds the ceiling.
    for (let attempt = 3; attempt < 12; attempt += 1) {
      sockets[sockets.length - 1].drop();
      jest.advanceTimersByTime(MAX_RECONNECT_DELAY_MS);
    }
    expect(sockets).toHaveLength(12);
  });

  it('resets the backoff once a connection succeeds', () => {
    const { socket, sockets } = build();
    socket.open();

    sockets[0].drop();
    jest.advanceTimersByTime(BASE_RECONNECT_DELAY_MS);
    sockets[1].drop();
    jest.advanceTimersByTime(BASE_RECONNECT_DELAY_MS * 2);

    sockets[2].finishHandshake();
    sockets[2].drop();

    // Back to the base delay, not the grown one: a server that came back and
    // blipped again should be retried promptly.
    jest.advanceTimersByTime(BASE_RECONNECT_DELAY_MS);
    expect(sockets).toHaveLength(4);
  });

  it('stops reconnecting once closed on purpose', () => {
    const { socket, sockets, statuses } = build();
    socket.open();
    sockets[0].finishHandshake();

    socket.close();
    jest.advanceTimersByTime(MAX_RECONNECT_DELAY_MS * 10);

    expect(sockets).toHaveLength(1);
    // Leaving the page must not look like a connection that dropped, or the
    // caller falls back to polling on its way out.
    expect(statuses).toEqual(['connecting', 'open']);
  });

  it('cancels a pending reconnect when closed during the backoff', () => {
    const { socket, sockets } = build();
    socket.open();
    sockets[0].drop();

    socket.close();
    jest.advanceTimersByTime(MAX_RECONNECT_DELAY_MS * 10);

    expect(sockets).toHaveLength(1);
  });

  describe('queue_event frames', () => {
    it('routes an event to onEvent, not onSnapshot', () => {
      const { socket, sockets, events, snapshots } = build();
      socket.open();
      sockets[0].finishHandshake();

      sockets[0].deliver({
        type: 'queue_event',
        data: { type: 'run_completed', data: { runId: 'run-1', status: 'succeeded' } },
      });

      expect(events).toEqual([
        { type: 'run_completed', data: { runId: 'run-1', status: 'succeeded' } },
      ]);
      expect(snapshots).toHaveLength(0);
    });

    it('keeps carrying snapshots alongside events', () => {
      const { socket, sockets, events, snapshots } = build();
      socket.open();
      sockets[0].finishHandshake();

      sockets[0].deliver({
        type: 'queue_event',
        data: { type: 'queue_promoted', data: { runId: 'run-1', slotNumber: 1 } },
      });
      sockets[0].deliver({ type: 'queue_status', data: SNAPSHOT });

      expect(events).toHaveLength(1);
      expect(snapshots).toEqual([SNAPSHOT]);
    });

    it('ignores an event frame with no payload', () => {
      const { socket, sockets, events } = build();
      socket.open();
      sockets[0].finishHandshake();

      sockets[0].deliver({ type: 'queue_event' });

      expect(events).toHaveLength(0);
    });

    it('survives a caller that does not handle events', () => {
      const sockets: FakeWebSocket[] = [];
      const snapshots: QueueStatusSnapshot[] = [];
      const socket = new QueueSocket(
        'ws://localhost:8000/ws/queue/ws-1',
        { onSnapshot: (s) => snapshots.push(s), onStatus: () => {} },
        (url) => {
          const fake = new FakeWebSocket(url);
          sockets.push(fake);
          return fake as unknown as WebSocket;
        },
      );
      socket.open();
      sockets[0].finishHandshake();

      expect(() =>
        sockets[0].deliver({ type: 'queue_event', data: { type: 'error' } }),
      ).not.toThrow();
    });
  });
});
