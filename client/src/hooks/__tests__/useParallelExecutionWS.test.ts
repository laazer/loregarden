/**
 * What the queue dashboard actually gets from `useParallelExecutionWS`, and
 * whether the "Real-time" badge it drives is telling the truth.
 *
 * The socket is faked at the `WebSocket` global rather than by mocking the
 * hook's own module, so the connection lifecycle under test is the real one —
 * including starting in CONNECTING, which is where the client this replaced
 * spent its entire life.
 */

import { act, renderHook, waitFor } from '@testing-library/react';

import { useParallelExecutionWS } from '../useParallelExecutionWS';
import type { QueueStatusSnapshot } from '../../lib/queueSocket';

const SNAPSHOT: QueueStatusSnapshot = {
  active_runs: [
    {
      run_id: 'run-1',
      ticket_id: 'ticket-1',
      slot_number: 1,
      elapsed_seconds: 12,
      status: 'running',
      agent_id: 'dev',
    },
  ],
  queued_runs: [],
  lanes: [],
  available_slots: 2,
  total_slots: 3,
  queue_length: 0,
  stats: {
    max_concurrent: 3,
    active_count: 1,
    available_slots: 2,
    queued_count: 0,
    total_slots_occupied: 1,
    longest_wait_seconds: 0,
  },
};

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;

  readyState = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  readonly url: string;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  finishHandshake(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  push(snapshot: QueueStatusSnapshot): void {
    this.onmessage?.({
      data: JSON.stringify({ type: 'queue_status', data: snapshot }),
    } as MessageEvent);
  }

  drop(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
  }
}

const originalWebSocket = global.WebSocket;

beforeEach(() => {
  FakeWebSocket.instances = [];
  (global as unknown as { WebSocket: unknown }).WebSocket = FakeWebSocket;
  // Never resolves. These tests care about *whether* the fallback polls, not
  // what it returns, and a resolving fetch would settle after the assertions
  // and update state outside act() — noise that hides real warnings.
  global.fetch = jest.fn(() => new Promise(() => {})) as unknown as typeof fetch;
});

afterEach(() => {
  (global as unknown as { WebSocket: unknown }).WebSocket = originalWebSocket;
  jest.restoreAllMocks();
});

const latest = () => FakeWebSocket.instances[FakeWebSocket.instances.length - 1];

it('opens the shared queue socket', () => {
  renderHook(() => useParallelExecutionWS());

  expect(FakeWebSocket.instances).toHaveLength(1);
  expect(latest().url).toContain('/ws/queue');
});

it('does not claim real-time before the handshake completes', () => {
  const { result } = renderHook(() => useParallelExecutionWS());

  expect(result.current.isWebSocket).toBe(false);
  expect(result.current.connectionState).toBe('connecting');
});

it('does not claim real-time while connected but before the first snapshot', () => {
  const { result } = renderHook(() => useParallelExecutionWS());

  act(() => latest().finishHandshake());

  // Connected is not the same as having data; the badge must not promise a
  // live view of a queue it has not received yet.
  expect(result.current.isWebSocket).toBe(false);
});

it('reports real-time and the pushed data once a snapshot arrives', () => {
  const { result } = renderHook(() => useParallelExecutionWS());

  act(() => {
    latest().finishHandshake();
    latest().push(SNAPSHOT);
  });

  expect(result.current.isWebSocket).toBe(true);
  expect(result.current.connectionState).toBe('open');
  expect(result.current.activeRuns).toEqual(SNAPSHOT.active_runs);
  expect(result.current.stats).toEqual(SNAPSHOT.stats);
  expect(result.current.loading).toBe(false);
});

it('stops polling once the socket is carrying the data', async () => {
  renderHook(() => useParallelExecutionWS());
  await waitFor(() => expect(global.fetch).toHaveBeenCalled());

  const pollsBefore = (global.fetch as jest.Mock).mock.calls.length;
  act(() => {
    latest().finishHandshake();
    latest().push(SNAPSHOT);
  });

  // The poll interval is torn down with the effect, so no further requests.
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect((global.fetch as jest.Mock).mock.calls.length).toBe(pollsBefore);
});

it('falls back to polling the moment the connection drops', async () => {
  const { result } = renderHook(() => useParallelExecutionWS());
  act(() => {
    latest().finishHandshake();
    latest().push(SNAPSHOT);
  });
  expect(result.current.isWebSocket).toBe(true);

  act(() => latest().drop());

  // No 30-second timeout: the drop itself is the signal.
  expect(result.current.isWebSocket).toBe(false);
  expect(result.current.connectionState).toBe('closed');
  await waitFor(() => expect(global.fetch).toHaveBeenCalled());
});

it('opens no socket when disabled', () => {
  const { result } = renderHook(() => useParallelExecutionWS(false));

  expect(FakeWebSocket.instances).toHaveLength(0);
  expect(result.current.isWebSocket).toBe(false);
});

it('does not poll when disabled, even with a workspace in hand', async () => {
  // The fallback runs while the socket is not live, and a disabled hook is not
  // live either — so gating it on `!live` alone made a caller that switched off
  // poll forever. The provider does exactly that when you leave the queue page,
  // with the workspace still resolved from cache, so this polled the queue on
  // every other page in the app.
  renderHook(() => useParallelExecutionWS(false));

  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(global.fetch).not.toHaveBeenCalled();
});

it('resumes polling when re-enabled without a socket', async () => {
  const { rerender } = renderHook(
    ({ on }) => useParallelExecutionWS(on),
    { initialProps: { on: false } },
  );
  expect(global.fetch).not.toHaveBeenCalled();

  rerender({ on: true });

  await waitFor(() => expect(global.fetch).toHaveBeenCalled());
});

it('opens no socket while disabled', () => {
  renderHook(() => useParallelExecutionWS(false));

  expect(FakeWebSocket.instances).toHaveLength(0);
});

it('drops its snapshot when the socket goes away', () => {
  const { result, rerender } = renderHook(
    ({ on }) => useParallelExecutionWS(on),
    { initialProps: { on: true } },
  );
  act(() => {
    latest().finishHandshake();
    latest().push(SNAPSHOT);
  });
  expect(result.current.activeRuns).toEqual(SNAPSHOT.active_runs);

  rerender({ on: false });

  // A snapshot outlives the socket it came from only as a stale answer, which
  // is worse than a slow one.
  expect(result.current.activeRuns).toEqual([]);
  expect(result.current.isWebSocket).toBe(false);
});

it('closes the socket on unmount', () => {
  const { unmount } = renderHook(() => useParallelExecutionWS());
  act(() => latest().finishHandshake());

  const socket = latest();
  unmount();

  expect(socket.readyState).toBe(FakeWebSocket.CLOSED);
});
