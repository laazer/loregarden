/**
 * Unit tests for useWorktreeConflictsWS hook.
 */

import { renderHook, waitFor } from '@testing-library/react';
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

    let conflictDetectedHandler: ((data: any) => void) | null = null;
    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'conflict_detected') {
        conflictDetectedHandler = handler;
      }
    });

    const { result } = renderHook(() => useWorktreeConflictsWS('wt-1', undefined, 30000, true));

    if (conflictDetectedHandler) {
      conflictDetectedHandler({
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
    }

    await waitFor(() => {
      expect(result.current.conflicts).toEqual(mockConflicts);
      expect(result.current.hasConflicts).toBe(true);
    });
  });

  test('clears conflicts when conflict_resolved event received', async () => {
    let detectedHandler: ((data: any) => void) | null = null;
    let resolvedHandler: ((data: any) => void) | null = null;

    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'conflict_detected') {
        detectedHandler = handler;
      } else if (event === 'conflict_resolved') {
        resolvedHandler = handler;
      }
    });

    const { result } = renderHook(() => useWorktreeConflictsWS('wt-1', undefined, 30000, true));

    // First detect a conflict
    if (detectedHandler) {
      detectedHandler({
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
    }

    await waitFor(() => {
      expect(result.current.hasConflicts).toBe(true);
    });

    // Then resolve it
    if (resolvedHandler) {
      resolvedHandler({
        worktreeId: 'wt-1',
        runId: 'run-123',
        timestamp: new Date().toISOString(),
      });
    }

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

    let stateChangeHandler: ((data: any) => void) | null = null;
    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'websocket:state_change') {
        stateChangeHandler = handler;
      }
    });

    const { result } = renderHook(() =>
      useWorktreeConflictsWS('wt-1', undefined, 1000, true, true)
    );

    // Simulate connection error
    if (stateChangeHandler) {
      stateChangeHandler({ state: 'error' });
    }

    // Fast-forward time past fallback timeout
    jest.advanceTimersByTime(1100);

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
    const { result } = renderHook(() =>
      useWorktreeConflictsWS('wt-1', undefined, 30000, true)
    );

    expect(result.current.connectionState).toBe('disconnected');

    await waitFor(() => {
      expect(result.current.connectionState).toBe('connected');
    });
  });

  test('updates details object from conflict_detected event', async () => {
    let conflictDetectedHandler: ((data: any) => void) | null = null;
    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'conflict_detected') {
        conflictDetectedHandler = handler;
      }
    });

    const { result } = renderHook(() => useWorktreeConflictsWS('wt-1', 'run-123', 30000, true));

    const timestamp = new Date().toISOString();

    if (conflictDetectedHandler) {
      conflictDetectedHandler({
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
    }

    await waitFor(() => {
      expect(result.current.details).not.toBeNull();
      expect(result.current.details?.worktree_id).toBe('wt-1');
      expect(result.current.details?.run_id).toBe('run-123');
    });
  });

  test('handles server errors', async () => {
    let errorHandler: ((data: any) => void) | null = null;
    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'websocket:server_error') {
        errorHandler = handler;
      }
    });

    const { result } = renderHook(() =>
      useWorktreeConflictsWS('wt-1', undefined, 30000, true)
    );

    if (errorHandler) {
      errorHandler({ message: 'Server error' });
    }

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
