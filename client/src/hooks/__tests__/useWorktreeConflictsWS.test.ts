/**
 * Unit tests for useWorktreeConflictsWS hook.
 */

import { renderHook, waitFor, act } from '@testing-library/react';
import { useWorktreeConflictsWS } from '../useWorktreeConflictsWS';
import * as websocketService from '../../services/websocket';

// Mock the WebSocket service
jest.mock('../../services/websocket', () => ({
  getWebSocketClient: jest.fn(),
  resetWebSocketClient: jest.fn(),
}));

// Mock the polling hook
jest.mock('../useWorktreeConflicts', () => ({
  useWorktreeConflicts: jest.fn(() => ({
    conflicts: [],
    preview: null,
    details: null,
    hasConflicts: false,
    loading: false,
    error: null,
  })),
}));

describe('useWorktreeConflictsWS', () => {
  let mockWebSocketClient: any;

  beforeEach(() => {
    jest.clearAllMocks();

    // Create mock WebSocket client
    mockWebSocketClient = {
      connect: jest.fn().mockResolvedValue(undefined),
      disconnect: jest.fn(),
      joinWorktree: jest.fn(),
      leaveWorktree: jest.fn(),
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

  test('returns initial loading state when enabled', () => {
    const { result } = renderHook(() =>
      useWorktreeConflictsWS('wt-1', undefined, 30000, true)
    );

    expect(result.current.loading).toBe(true);
    expect(result.current.conflicts).toEqual([]);
    expect(result.current.hasConflicts).toBe(false);
    expect(result.current.isWebSocket).toBe(true);
  });

  test('skips loading when disabled', () => {
    const { result } = renderHook(() =>
      useWorktreeConflictsWS('wt-1', undefined, 30000, false)
    );

    expect(result.current.loading).toBe(false);
  });

  test('connects to WebSocket on mount when enabled', async () => {
    renderHook(() => useWorktreeConflictsWS('wt-1', undefined, 30000, true));

    await waitFor(() => {
      expect(mockWebSocketClient.connect).toHaveBeenCalled();
    });
  });

  test('joins worktree after connecting', async () => {
    renderHook(() =>
      useWorktreeConflictsWS('wt-1', 'run-123', 30000, true)
    );

    await waitFor(() => {
      expect(mockWebSocketClient.joinWorktree).toHaveBeenCalledWith('wt-1', 'run-123');
    });
  });

  test('registers event handlers for conflict events', async () => {
    renderHook(() => useWorktreeConflictsWS('wt-1', undefined, 30000, true));

    await waitFor(() => {
      const calls = mockWebSocketClient.on.mock.calls.map((c: any) => c[0]);
      expect(calls).toContain('conflict_detected');
      expect(calls).toContain('conflict_resolved');
    });
  });

  test('updates state when conflict_detected event received', async () => {
    const mockConflicts = [
      {
        path: 'src/app.ts',
        type: 'code' as const,
        conflictLines: 12,
        auto_mergeable: false,
        resolution_suggestion: 'Merge both',
      },
    ];

    let conflictDetectedHandler: ((data: any) => void) | undefined;
    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'conflict_detected') {
        conflictDetectedHandler = handler;
      }
    });

    const { result } = renderHook(() => useWorktreeConflictsWS('wt-1', undefined, 30000, true));

    // `wsClient.on(...)` is only called after the mocked `connect()` promise
    // resolves, which happens on a later microtask than this synchronous test
    // body. Wait for the handler to actually be registered before invoking it.
    await waitFor(() => {
      expect(conflictDetectedHandler).toBeDefined();
    });

    act(() => {
      conflictDetectedHandler!({
        worktreeId: 'wt-1',
        runId: 'run-123',
        timestamp: new Date().toISOString(),
        data: {
          conflicts: mockConflicts,
          preview: {
            conflicting_files: mockConflicts,
            total_conflicts: 1,
            auto_mergeable_count: 0,
            severity: 'medium',
          },
        },
      });
    });

    await waitFor(() => {
      expect(result.current.conflicts).toEqual(mockConflicts);
      expect(result.current.hasConflicts).toBe(true);
    });
  });

  test('clears conflicts when conflict_resolved event received', async () => {
    let detectedHandler: ((data: any) => void) | undefined;
    let resolvedHandler: ((data: any) => void) | undefined;

    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'conflict_detected') {
        detectedHandler = handler;
      } else if (event === 'conflict_resolved') {
        resolvedHandler = handler;
      }
    });

    const { result } = renderHook(() => useWorktreeConflictsWS('wt-1', undefined, 30000, true));

    await waitFor(() => {
      expect(detectedHandler).toBeDefined();
    });

    // First detect a conflict
    act(() => {
      detectedHandler!({
        worktreeId: 'wt-1',
        runId: 'run-123',
        timestamp: new Date().toISOString(),
        data: {
          conflicts: [
            {
              path: 'src/app.ts',
              type: 'code',
              conflictLines: 12,
              auto_mergeable: false,
            },
          ],
          preview: {
            conflicting_files: [],
            total_conflicts: 1,
            auto_mergeable_count: 0,
            severity: 'medium',
          },
        },
      });
    });

    await waitFor(() => {
      expect(result.current.hasConflicts).toBe(true);
    });

    await waitFor(() => {
      expect(resolvedHandler).toBeDefined();
    });

    // Then resolve it
    act(() => {
      resolvedHandler!({
        worktreeId: 'wt-1',
        runId: 'run-123',
        timestamp: new Date().toISOString(),
      });
    });

    await waitFor(() => {
      expect(result.current.conflicts).toEqual([]);
      expect(result.current.hasConflicts).toBe(false);
    });
  });

  test('handles WebSocket connection failures gracefully', async () => {
    mockWebSocketClient.connect.mockRejectedValueOnce(
      new Error('Connection refused')
    );

    const { result } = renderHook(() =>
      useWorktreeConflictsWS('wt-1', undefined, 30000, true)
    );

    await waitFor(() => {
      expect(result.current.isWebSocket).toBe(false);
    });
  });

  test('respects fallback timeout', async () => {
    jest.useFakeTimers();

    let stateChangeHandler: ((data: any) => void) | undefined;
    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'websocket:state_change') {
        stateChangeHandler = handler;
      }
    });

    const { result } = renderHook(() =>
      useWorktreeConflictsWS('wt-1', undefined, 1000, true, true)
    );

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
      useWorktreeConflictsWS('wt-1', undefined, 30000, true, false)
    );

    await waitFor(() => {
      expect(result.current.error).toContain('Failed to connect');
    });
  });

  test('cleans up on unmount', async () => {
    const { unmount } = renderHook(() =>
      useWorktreeConflictsWS('wt-1', undefined, 30000, true)
    );

    await waitFor(() => {
      expect(mockWebSocketClient.joinWorktree).toHaveBeenCalled();
    });

    unmount();

    await waitFor(() => {
      expect(mockWebSocketClient.off).toHaveBeenCalled();
      expect(mockWebSocketClient.leaveWorktree).toHaveBeenCalledWith('wt-1');
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

    const { result } = renderHook(() =>
      useWorktreeConflictsWS('wt-1', undefined, 30000, true)
    );

    expect(result.current.connectionState).toBe('disconnected');

    await act(async () => {
      resolveConnect();
    });

    await waitFor(() => {
      expect(result.current.connectionState).toBe('connected');
    });
  });

  test('updates details object from conflict_detected event', async () => {
    let conflictDetectedHandler: ((data: any) => void) | undefined;
    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'conflict_detected') {
        conflictDetectedHandler = handler;
      }
    });

    const { result } = renderHook(() => useWorktreeConflictsWS('wt-1', 'run-123', 30000, true));

    const timestamp = new Date().toISOString();

    await waitFor(() => {
      expect(conflictDetectedHandler).toBeDefined();
    });

    act(() => {
      conflictDetectedHandler!({
        worktreeId: 'wt-1',
        runId: 'run-123',
        timestamp,
        data: {
          conflicts: [],
          preview: {
            conflicting_files: [],
            total_conflicts: 0,
            auto_mergeable_count: 0,
            severity: 'low',
          },
        },
      });
    });

    await waitFor(() => {
      expect(result.current.details).not.toBeNull();
      expect(result.current.details?.worktree_id).toBe('wt-1');
      expect(result.current.details?.run_id).toBe('run-123');
    });
  });

  test('handles server errors', async () => {
    let errorHandler: ((data: any) => void) | undefined;
    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'websocket:server_error') {
        errorHandler = handler;
      }
    });

    const { result } = renderHook(() =>
      useWorktreeConflictsWS('wt-1', undefined, 30000, true)
    );

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

  test('respects enabled flag changes', () => {
    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) =>
        useWorktreeConflictsWS('wt-1', undefined, 30000, enabled),
      { initialProps: { enabled: false } }
    );

    expect(result.current.loading).toBe(false);

    rerender({ enabled: true });

    expect(result.current.loading).toBe(true);
  });
});
