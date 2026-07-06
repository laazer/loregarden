/**
 * Unit tests for useParallelExecutionWS hook.
 */

import { renderHook, waitFor } from '@testing-library/react';
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
    let executionUpdateHandler: ((data: any) => void) | null = null;
    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'execution_update') {
        executionUpdateHandler = handler;
      }
    });

    const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

    // Trigger the event
    if (executionUpdateHandler) {
      executionUpdateHandler({
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
    }

    await waitFor(() => {
      expect(result.current.activeRuns).toEqual(mockRuns);
    });
  });

  test('handles WebSocket connection state changes', async () => {
    let stateChangeHandler: ((data: any) => void) | null = null;
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
    let errorHandler: ((data: any) => void) | null = null;
    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'websocket:server_error') {
        errorHandler = handler;
      }
    });

    const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

    if (errorHandler) {
      errorHandler({ message: 'Server error' });
    }

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

    let stateChangeHandler: ((data: any) => void) | null = null;
    mockWebSocketClient.on.mockImplementation((event: string, handler: any) => {
      if (event === 'websocket:state_change') {
        stateChangeHandler = handler;
      }
    });

    const { result } = renderHook(() =>
      useParallelExecutionWS('ws-1', undefined, 1000, true)
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
    const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

    expect(result.current.connectionState).toBe('disconnected');

    await waitFor(() => {
      expect(result.current.connectionState).toBe('connected');
    });
  });

  test('maintains stats from WebSocket updates', async () => {
    let executionUpdateHandler: ((data: any) => void) | null = null;
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

    if (executionUpdateHandler) {
      executionUpdateHandler({
        data: {
          activeRuns: [],
          queuedRuns: [],
          stats: mockStats,
        },
      });
    }

    await waitFor(() => {
      expect(result.current.stats).toEqual(mockStats);
    });
  });

  test('handles empty execution_update data gracefully', async () => {
    let executionUpdateHandler: ((data: any) => void) | null = null;
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
