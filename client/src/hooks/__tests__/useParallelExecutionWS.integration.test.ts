/**
 * Integration tests for useParallelExecutionWS hook with WebSocket and fallback.
 */

import { renderHook, waitFor, act } from '@testing-library/react';
import { useParallelExecutionWS } from '../useParallelExecutionWS';
import * as websocketService from '../../services/websocket';

jest.mock('../../services/websocket');
jest.mock('../useParallelExecution', () => ({
  useParallelExecution: jest.fn(() => ({
    activeRuns: [],
    queuedRuns: [],
    stats: {
      max_concurrent: 3,
      active_count: 0,
      available_slots: 3,
      queued_count: 0,
    },
    loading: false,
    error: null,
  })),
}));

describe('useParallelExecutionWS Integration', () => {
  let mockWebSocketClient: any;

  beforeEach(() => {
    jest.clearAllMocks();
    jest.useFakeTimers();

    mockWebSocketClient = {
      connect: jest.fn().mockResolvedValue(undefined),
      disconnect: jest.fn(),
      joinWorkspace: jest.fn(),
      leaveWorkspace: jest.fn(),
      on: jest.fn(),
      off: jest.fn(),
      getState: jest.fn().mockReturnValue('connected'),
    };

    (websocketService.getWebSocketClient as jest.Mock).mockReturnValue(
      mockWebSocketClient
    );
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  describe('WebSocket Connection', () => {
    test('establishes WebSocket connection on mount', async () => {
      renderHook(() => useParallelExecutionWS('ws-1'));

      await waitFor(() => {
        expect(mockWebSocketClient.connect).toHaveBeenCalled();
      });
    });

    test('joins workspace with correct ID', async () => {
      renderHook(() => useParallelExecutionWS('ws-123'));

      await waitFor(() => {
        expect(mockWebSocketClient.joinWorkspace).toHaveBeenCalledWith('ws-123');
      });
    });

    test('leaves workspace on unmount', async () => {
      const { unmount } = renderHook(() => useParallelExecutionWS('ws-1'));

      await waitFor(() => {
        expect(mockWebSocketClient.joinWorkspace).toHaveBeenCalled();
      });

      unmount();

      expect(mockWebSocketClient.leaveWorkspace).toHaveBeenCalledWith('ws-1');
    });

    test('passes user ID to join workspace if provided', async () => {
      renderHook(() => useParallelExecutionWS('ws-1', 'user-123'));

      // `userId` is forwarded to `wsClient.connect(userId)` for the socket
      // handshake (see the "passes userId to WebSocket connection" unit test);
      // `joinWorkspace(workspaceId: string)` itself takes a single argument
      // (see src/services/websocket.ts) and has no userId parameter.
      await waitFor(() => {
        expect(mockWebSocketClient.connect).toHaveBeenCalledWith('user-123');
        expect(mockWebSocketClient.joinWorkspace).toHaveBeenCalledWith('ws-1');
      });
    });
  });

  describe('Event Handling', () => {
    test('registers execution_update event listener', async () => {
      renderHook(() => useParallelExecutionWS('ws-1'));

      await waitFor(() => {
        expect(mockWebSocketClient.on).toHaveBeenCalledWith(
          'execution_update',
          expect.any(Function)
        );
      });
    });

    test('updates state when execution_update event received', async () => {
      let eventHandler: Function | null = null;

      mockWebSocketClient.on.mockImplementation((event: string, handler: Function) => {
        if (event === 'execution_update') {
          eventHandler = handler;
        }
      });

      const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

      const mockData = {
        activeRuns: [
          {
            run_id: 'run-1',
            ticket_id: 'ticket-1',
            slot_number: 1,
            elapsed_seconds: 60,
            status: 'running',
          },
        ],
        queuedRuns: [],
        stats: { active_count: 1, queued_count: 0 },
      };

      await waitFor(() => {
        expect(eventHandler).toBeDefined();
      });

      if (eventHandler) {
        // The hook destructures `data.data` (see useParallelExecutionWS.ts's
        // handleExecutionUpdate), matching the server's WebSocketEvent
        // envelope shape (`{ type, timestamp, data }`).
        act(() => {
          eventHandler!({ data: mockData });
        });
      }

      await waitFor(() => {
        expect(result.current.activeRuns).toHaveLength(1);
        expect(result.current.activeRuns[0].run_id).toBe('run-1');
      });
    });

    test('deregisters event handler on unmount', async () => {
      const { unmount } = renderHook(() => useParallelExecutionWS('ws-1'));

      await waitFor(() => {
        expect(mockWebSocketClient.on).toHaveBeenCalled();
      });

      unmount();

      expect(mockWebSocketClient.off).toHaveBeenCalledWith(
        'execution_update',
        expect.any(Function)
      );
    });
  });

  describe('Fallback to Polling', () => {
    test('indicates WebSocket mode when connected', async () => {
      mockWebSocketClient.getState.mockReturnValue('connected');

      const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

      await waitFor(() => {
        expect(result.current.isWebSocket).toBe(true);
      });
    });

    test('falls back to polling after connection timeout', async () => {
      mockWebSocketClient.getState.mockReturnValue('connecting');

      const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

      // Simulate timeout (30 seconds)
      act(() => {
        jest.advanceTimersByTime(31000);
      });

      await waitFor(() => {
        expect(result.current.isWebSocket).toBe(false);
      });
    });

    test('restores WebSocket mode if connection succeeds after timeout', async () => {
      let currentState = 'connecting';
      mockWebSocketClient.getState.mockImplementation(() => currentState);

      const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

      // Timeout to fallback
      act(() => {
        jest.advanceTimersByTime(31000);
      });

      await waitFor(() => {
        expect(result.current.isWebSocket).toBe(false);
      });

      // Connection succeeds
      currentState = 'connected';

      act(() => {
        jest.advanceTimersByTime(1000);
      });

      await waitFor(() => {
        expect(result.current.isWebSocket).toBe(true);
      });
    });
  });

  describe('Connection State', () => {
    test('reports connection state', async () => {
      mockWebSocketClient.getState.mockReturnValue('connected');

      const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

      await waitFor(() => {
        expect(result.current.connectionState).toBe('connected');
      });
    });

    test('handles error state', async () => {
      mockWebSocketClient.getState.mockReturnValue('error');

      const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

      await waitFor(() => {
        expect(result.current.connectionState).toBe('error');
      });
    });
  });

  describe('Data Updates', () => {
    test('maintains separate active and queued runs', async () => {
      let eventHandler: Function | null = null;

      mockWebSocketClient.on.mockImplementation((event: string, handler: Function) => {
        if (event === 'execution_update') {
          eventHandler = handler;
        }
      });

      const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

      const mockData = {
        activeRuns: [{ run_id: 'run-1', slot_number: 1 }],
        queuedRuns: [
          { run_id: 'run-2', position: 1 },
          { run_id: 'run-3', position: 2 },
        ],
        stats: { active_count: 1, queued_count: 2 },
      };

      await waitFor(() => {
        expect(eventHandler).toBeDefined();
      });

      if (eventHandler) {
        act(() => {
          eventHandler!({ data: mockData });
        });
      }

      await waitFor(() => {
        expect(result.current.activeRuns).toHaveLength(1);
        expect(result.current.queuedRuns).toHaveLength(2);
      });
    });

    test('updates stats correctly', async () => {
      let eventHandler: Function | null = null;

      mockWebSocketClient.on.mockImplementation((event: string, handler: Function) => {
        if (event === 'execution_update') {
          eventHandler = handler;
        }
      });

      const { result } = renderHook(() => useParallelExecutionWS('ws-1'));

      const mockData = {
        activeRuns: [],
        queuedRuns: [],
        stats: {
          max_concurrent: 3,
          active_count: 2,
          available_slots: 1,
          queued_count: 5,
        },
      };

      await waitFor(() => {
        expect(eventHandler).toBeDefined();
      });

      if (eventHandler) {
        act(() => {
          eventHandler!({ data: mockData });
        });
      }

      await waitFor(() => {
        expect(result.current.stats.active_count).toBe(2);
        expect(result.current.stats.available_slots).toBe(1);
      });
    });
  });

  describe('Error Handling', () => {
    test('continues operation even if event handler errors', async () => {
      let eventHandler: Function | null = null;

      mockWebSocketClient.on.mockImplementation((event: string, handler: Function) => {
        if (event === 'execution_update') {
          eventHandler = handler;
        }
      });

      renderHook(() => useParallelExecutionWS('ws-1'));

      await waitFor(() => {
        expect(eventHandler).toBeDefined();
      });

      if (eventHandler) {
        const invalidData = null;
        // Should not throw even with invalid data
        expect(() => {
          act(() => {
            eventHandler!(invalidData);
          });
        }).not.toThrow();
      }
    });
  });
});
