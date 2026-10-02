/**
 * The tab's half of the agent action protocol: it offers what is registered,
 * re-offers after every reconnect (a restarted backend remembers nothing), and
 * answers every invocation — including the ones that fail — so an agent is
 * never left waiting on a request the tab dropped.
 */

import { UiActionRegistry } from '../registry';
import { UiActionSocket, uiActionSocketUrl } from '../uiActionSocket';

class FakeWebSocket {
  readyState = 0;
  sent: Array<Record<string, unknown>> = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  finishHandshake(): void {
    this.readyState = 1;
    this.onopen?.();
  }

  deliver(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
  }

  drop(): void {
    this.readyState = 3;
    this.onclose?.();
  }

  send(data: string): void {
    this.sent.push(JSON.parse(data));
  }

  close(): void {
    this.readyState = 3;
  }
}

function setup() {
  const sockets: FakeWebSocket[] = [];
  const registry = new UiActionRegistry();
  const onInvoke = jest.fn();
  const socket = new UiActionSocket(
    'ws://x/ws/ui-actions',
    { onStatus: () => {}, onInvoke },
    () => {
      const fake = new FakeWebSocket();
      sockets.push(fake);
      return fake as unknown as WebSocket;
    },
  );
  socket.attach(registry);
  socket.open();
  return { socket, sockets, registry, onInvoke };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => jest.useRealTimers());

it('builds a ws url from the api base', () => {
  expect(uiActionSocketUrl('http://127.0.0.1:8000/')).toBe('ws://127.0.0.1:8000/ws/ui-actions');
});

it('offers what is registered once connected, and again after a reconnect', () => {
  jest.useFakeTimers();
  const { sockets, registry, socket } = setup();
  registry.register('navigate.page', async () => null);
  expect(sockets[0].sent).toEqual([]); // nothing to send on before the handshake

  sockets[0].finishHandshake();
  expect(sockets[0].sent[0]).toEqual({ type: 'register', actions: ['navigate.page'] });

  sockets[0].drop();
  jest.advanceTimersByTime(1000);
  sockets[1].finishHandshake();
  expect(sockets[1].sent[0]).toEqual({ type: 'register', actions: ['navigate.page'] });
  socket.close();
});

it('re-offers when a surface mounts or unmounts', () => {
  const { sockets, registry, socket } = setup();
  sockets[0].finishHandshake();
  const unregister = registry.register('ticket.set_state', async () => null);
  expect(sockets[0].sent.at(-1)).toEqual({ type: 'register', actions: ['ticket.set_state'] });
  unregister();
  expect(sockets[0].sent.at(-1)).toEqual({ type: 'register', actions: [] });
  socket.close();
});

it('runs an invocation and answers with its result', async () => {
  const { sockets, registry, onInvoke, socket } = setup();
  const handler = jest.fn(async () => ({ path: '/queue' }));
  registry.register('navigate.page', handler);
  sockets[0].finishHandshake();

  sockets[0].deliver({ type: 'invoke', id: '7', action: 'navigate.page', arguments: { page: 'queue' } });
  await flush();

  expect(handler).toHaveBeenCalledWith({ page: 'queue' });
  expect(sockets[0].sent.at(-1)).toEqual({ type: 'result', id: '7', ok: true, result: { path: '/queue' } });
  expect(onInvoke).toHaveBeenCalledWith('navigate.page', { ok: true });
  socket.close();
});

it('answers a failing handler with its reason, and tells the operator', async () => {
  const { sockets, registry, onInvoke, socket } = setup();
  registry.register('ticket.set_state', async () => {
    throw new Error('lg-1 is not the open ticket');
  });
  sockets[0].finishHandshake();

  sockets[0].deliver({ type: 'invoke', id: '8', action: 'ticket.set_state', arguments: {} });
  await flush();

  expect(sockets[0].sent.at(-1)).toMatchObject({ type: 'result', id: '8', ok: false });
  expect(String(sockets[0].sent.at(-1)?.error)).toContain('lg-1 is not the open ticket');
  expect(onInvoke).toHaveBeenCalledWith('ticket.set_state', {
    ok: false,
    error: expect.stringContaining('lg-1 is not the open ticket'),
  });
  socket.close();
});

it('answers an action this tab no longer offers instead of dropping it', async () => {
  const { sockets, socket } = setup();
  sockets[0].finishHandshake();
  sockets[0].deliver({ type: 'invoke', id: '9', action: 'ticket.update', arguments: {} });
  await flush();
  expect(sockets[0].sent.at(-1)).toMatchObject({ type: 'result', id: '9', ok: false });
  socket.close();
});
