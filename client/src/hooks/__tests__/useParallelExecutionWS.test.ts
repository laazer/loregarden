/**
 * Unit tests for useParallelExecutionWS hook.
 */

import { renderHook, waitFor, act } from '@testing-library/react';
import { useParallelExecutionWS } from '../useParallelExecutionWS';
import * as websocketService from '../../services/websocket';

// Mock the WebSocket service
jest.mock('../../services/websocket', () => ({
  getWebSocketClient: jest.fn(),
  resetWebSocketClient: jest.fn(),
}));

// Mock the polling hook
jest.mock('../useParallelExecution', () => ({
  useParallelExecution: jest.fn(() => ({
    activeRuns: [],
    queuedRuns: [],
    stats: {
      max_concurrent: 3,
      active_count: 0,
      available_slots: 3,
      queued_count: 0,
      total_slots_occupied: 0,
      queue_wait_time_minutes: 0,
    },
    loading: false,
    error: null,
  })),
}));

describe('useParallelExecutionWS', () => {
  let mockWebSocketClient: any;

  beforeEach(() => {
    jest.clearAllMocks();

    // Create mock WebSocket client
    mockWebSocketClient = {
      connect: jest.fn().mockResolvedValue(undefined),
      disconnect: jest.fn(),
      joinWorkspace: jest.fn(),
      leaveWorkspace: jest.fn(),
      on: jest.fn(),
      off: jest.fn(),
      getState: jest.fn().mockReturnValue('connected'),
    };

    (websocketService.getWebSocketClient as jest.Mock).mockReturnValue(mockWebSocketClient);
  });

  afterEach(() => {
    // 'respects fallback timeout' below switches to fake timers and only
    // restores real ones on its last line — if that test fails before
    // reaching it (e.g. the assertion throws), fake timers would otherwise
    // leak into every later test in this file. Restoring unconditionally
    // here makes that failure mode inert instead of destabilizing sibling
    // tests. Calling this when timers are already real is a harmless no-op.
    jest.useRealTimers();
  });

  test('returns initial loading state', () => {
    const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

    expect(result.current.loading).toBe(true);
    expect(result.current.activeRuns).toEqual([]);
    expect(result.current.isWebSocket).toBe(true);
  });

  test('connects to WebSocket on mount', async () => {
    renderHook(() => useParallelExecutionWS('ws-1'));

    await waitFor(() => {
      expect(mockWebSocketClient.connect).toHaveBeenCalled();
    });
  });

  test('joins workspace after connecting', async () => {
    renderHook(() => useParallelExecutionWS('ws-1'));

    await waitFor(() => {
      expect(mockWebSocketClient.joinWorkspace).toHaveBeenCalledWith('ws-1');
    });
  });

  test('registers event handler for execution updates', async () => {
    renderHook(() => useParallelExecutionWS('ws-1'));

    await waitFor(() => {
      expect(mockWebSocketClient.on).toHaveBeenCalledWith(
        'execution_update',
        expect.any(Function)
      );
    });
  });

  test('updates state when execution_update event received', async () => {
    const mockRuns = [
      {
        run_id: 'run-1',
        ticket_id: 'feature-123',
        slot_number: 1,
        elapsed_seconds: 120,
        status: 'running',
        agent_id: 'planner',
      },
    ];

    // Set up event handler to be called
    let executionUpdateHandler: ((data: any) => void) | undefined;
    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'execution_update') {
        executionUpdateHandler = handler;
      }
    });

    const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

    // `wsClient.on(...)` is only called after the mocked `connect()` promise
    // resolves, which happens on a later microtask than this synchronous test
    // body. Wait for the handler to actually be registered before invoking it,
    // otherwise `executionUpdateHandler` is still undefined and the event is
    // silently never fired.
    await waitFor(() => {
      expect(executionUpdateHandler).toBeDefined();
    });

    // Trigger the event
    act(() => {
      executionUpdateHandler!({
        data: {
          activeRuns: mockRuns,
          queuedRuns: [],
          stats: {
            max_concurrent: 3,
            active_count: 1,
            available_slots: 2,
            queued_count: 0,
            total_slots_occupied: 1,
            queue_wait_time_minutes: 0,
          },
        },
      });
    });

    await waitFor(() => {
      expect(result.current.activeRuns).toEqual(mockRuns);
    });
  });

  test('handles WebSocket connection state changes', async () => {
    let stateChangeHandler: ((data: any) => void) | undefined;
    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'websocket:state_change') {
        stateChangeHandler = handler;
      }
    });

    const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

    if (stateChangeHandler) {
      stateChangeHandler({ state: 'connected' });
    }

    await waitFor(() => {
      expect(result.current.connectionState).toBe('connected');
    });
  });

  test('handles WebSocket errors', async () => {
    let errorHandler: ((data: any) => void) | undefined;
    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'websocket:server_error') {
        errorHandler = handler;
      }
    });

    const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

    // Wait for the handler to be registered (happens after the mocked
    // `connect()` promise resolves on a later microtask).
    await waitFor(() => {
      expect(errorHandler).toBeDefined();
    });

    act(() => {
      errorHandler!({ message: 'Server error' });
    });

    await waitFor(() => {
      expect(result.current.error).toBe('Server error');
    });
  });

  test('falls back to polling on WebSocket connection failure', async () => {
    mockWebSocketClient.connect.mockRejectedValueOnce(
      new Error('Connection refused')
    );

    const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

    await waitFor(() => {
      expect(result.current.isWebSocket).toBe(false);
    });
  });

  test('respects fallback timeout', async () => {
    jest.useFakeTimers();

    mockWebSocketClient.connect.mockResolvedValueOnce(undefined);

    let stateChangeHandler: ((data: any) => void) | undefined;
    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'websocket:state_change') {
        stateChangeHandler = handler;
      }
    });

    const { result } = renderHook(() =>
      useParallelExecutionWS('ws-1', undefined, 1000, true)
    );

    // Wait for the handler to be registered (happens after the mocked
    // `connect()` promise resolves on a later microtask).
    await waitFor(() => {
      expect(stateChangeHandler).toBeDefined();
    });

    // Simulate connection error. The real WebSocketClient always updates its
    // internal state before emitting 'websocket:state_change' (see
    // updateState() in services/websocket.ts), so getState() must agree with
    // the event here too — the hook now polls getState() directly (to catch
    // a connection stuck in 'connecting' with no event at all), and a mock
    // left inconsistent with the event it just fired would incorrectly look
    // like a recovered connection to that poll.
    mockWebSocketClient.getState.mockReturnValue('error');
    act(() => {
      stateChangeHandler!({ state: 'error' });
    });

    // Fast-forward time past fallback timeout
    act(() => {
      jest.advanceTimersByTime(1100);
    });

    await waitFor(() => {
      expect(result.current.isWebSocket).toBe(false);
    });

    jest.useRealTimers();
  });

  test('disables fallback when useFallback is false', async () => {
    mockWebSocketClient.connect.mockRejectedValueOnce(
      new Error('Connection refused')
    );

    const { result } = renderHook(() =>
      useParallelExecutionWS('ws-1', undefined, 30000, false)
    );

    await waitFor(() => {
      expect(result.current.error).toContain('Failed to connect');
    });
  });

  test('passes userId to WebSocket connection', async () => {
    renderHook(() => useParallelExecutionWS('ws-1', 'user-123'));

    await waitFor(() => {
      expect(mockWebSocketClient.connect).toHaveBeenCalledWith('user-123');
    });
  });

  test('cleans up on unmount', async () => {
    const { unmount } = renderHook(() => useParallelExecutionWS('ws-1'));

    await waitFor(() => {
      expect(mockWebSocketClient.joinWorkspace).toHaveBeenCalled();
    });

    unmount();

    await waitFor(() => {
      expect(mockWebSocketClient.off).toHaveBeenCalled();
      expect(mockWebSocketClient.leaveWorkspace).toHaveBeenCalledWith('ws-1');
    });
  });

  test('returns correct connection state', async () => {
    // `connect()` normally auto-resolves (see beforeEach's
    // `mockResolvedValue`), which races its continuation's microtask against
    // this test's synchronous assertion below — whether that microtask has
    // run yet by the time `expect` executes depends on exactly how many
    // ticks act()'s effect-flushing consumes, which isn't guaranteed. Hold
    // `connect()` open so 'disconnected' is asserted before any connection
    // work can possibly complete, then resolve it and await 'connected'.
    let resolveConnect: () => void;
    mockWebSocketClient.connect.mockReturnValue(
      new Promise<void>((resolve) => {
        resolveConnect = resolve;
      })
    );

    const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

    expect(result.current.connectionState).toBe('disconnected');

    await act(async () => {
      resolveConnect();
    });

    await waitFor(() => {
      expect(result.current.connectionState).toBe('connected');
    });
  });

  test('maintains stats from WebSocket updates', async () => {
    let executionUpdateHandler: ((data: any) => void) | undefined;
    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'execution_update') {
        executionUpdateHandler = handler;
      }
    });

    const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

    const mockStats = {
      max_concurrent: 5,
      active_count: 3,
      available_slots: 2,
      queued_count: 2,
      total_slots_occupied: 3,
      queue_wait_time_minutes: 10,
    };

    await waitFor(() => {
      expect(executionUpdateHandler).toBeDefined();
    });

    act(() => {
      executionUpdateHandler!({
        data: {
          activeRuns: [],
          queuedRuns: [],
          stats: mockStats,
        },
      });
    });

    await waitFor(() => {
      expect(result.current.stats).toEqual(mockStats);
    });
  });

  test('handles empty execution_update data gracefully', async () => {
    let executionUpdateHandler: ((data: any) => void) | undefined;
    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'execution_update') {
        executionUpdateHandler = handler;
      }
    });

    const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

    if (executionUpdateHandler) {
      executionUpdateHandler({ data: null });
    }

    // Should not crash
    expect(result.current.activeRuns).toBeDefined();
  });
});
