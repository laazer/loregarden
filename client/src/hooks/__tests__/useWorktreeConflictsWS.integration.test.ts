/**
 * Integration tests for useWorktreeConflictsWS hook with WebSocket and fallback.
 */

import { renderHook, waitFor, act } from '@testing-library/react';
import { useWorktreeConflictsWS } from '../useWorktreeConflictsWS';
import * as websocketService from '../../services/websocket';

jest.mock('../../services/websocket');
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

describe('useWorktreeConflictsWS Integration', () => {
  let mockWebSocketClient: any;

  beforeEach(() => {
    jest.clearAllMocks();
    jest.useFakeTimers();

    mockWebSocketClient = {
      connect: jest.fn().mockResolvedValue(undefined),
      disconnect: jest.fn(),
      joinWorktree: jest.fn(),
      leaveWorktree: jest.fn(),
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
    test('joins worktree when enabled', async () => {
      renderHook(() =>
        useWorktreeConflictsWS('wt-1', undefined, undefined, true)
      );

      // `wsClient.joinWorktree(worktreeId, runId)` always passes runId
      // (undefined here since none was provided) — see useWorktreeConflictsWS.ts.
      await waitFor(() => {
        expect(mockWebSocketClient.joinWorktree).toHaveBeenCalledWith('wt-1', undefined);
      });
    });

    test('does not join worktree when disabled', async () => {
      renderHook(() =>
        useWorktreeConflictsWS('wt-1', undefined, undefined, false)
      );

      await waitFor(() => {
        expect(mockWebSocketClient.joinWorktree).not.toHaveBeenCalled();
      });
    });

    test('leaves worktree on unmount', async () => {
      const { unmount } = renderHook(() =>
        useWorktreeConflictsWS('wt-1', undefined, undefined, true)
      );

      await waitFor(() => {
        expect(mockWebSocketClient.joinWorktree).toHaveBeenCalled();
      });

      unmount();

      expect(mockWebSocketClient.leaveWorktree).toHaveBeenCalledWith('wt-1');
    });

    test('re-joins when enabled changes from false to true', async () => {
      const { rerender } = renderHook(
        ({ enabled }) => useWorktreeConflictsWS('wt-1', undefined, undefined, enabled),
        { initialProps: { enabled: false } }
      );

      expect(mockWebSocketClient.joinWorktree).not.toHaveBeenCalled();

      rerender({ enabled: true });

      await waitFor(() => {
        expect(mockWebSocketClient.joinWorktree).toHaveBeenCalledWith('wt-1', undefined);
      });
    });
  });

  describe('Conflict Detection Events', () => {
    test('registers conflict_detected event listener', async () => {
      renderHook(() =>
        useWorktreeConflictsWS('wt-1', undefined, undefined, true)
      );

      await waitFor(() => {
        expect(mockWebSocketClient.on).toHaveBeenCalledWith(
          'conflict_detected',
          expect.any(Function)
        );
      });
    });

    test('updates state when conflict_detected event received', async () => {
      let eventHandler: Function | null = null;

      mockWebSocketClient.on.mockImplementation((event: string, handler: Function) => {
        if (event === 'conflict_detected') {
          eventHandler = handler;
        }
      });

      const { result } = renderHook(() =>
        useWorktreeConflictsWS('wt-1', undefined, undefined, true)
      );

      // ConflictFile's field is `path` (see src/hooks/useWorktreeConflicts.ts),
      // and the hook destructures the event's nested `data` field (see
      // useWorktreeConflictsWS.ts's handleConflictDetected), matching the
      // server's WebSocketEvent envelope shape.
      const mockConflictData = {
        conflicts: [
          {
            path: 'src/main.py',
            status: 'conflicted',
            ours_lines: 2,
            theirs_lines: 1,
          },
        ],
        preview: {
          has_conflicts: true,
          conflicting_files: ['src/main.py'],
          severity: 'medium',
        },
        severity: 'medium',
      };

      await waitFor(() => {
        expect(eventHandler).toBeDefined();
      });

      if (eventHandler) {
        act(() => {
          eventHandler!({ data: mockConflictData });
        });
      }

      await waitFor(() => {
        expect(result.current.hasConflicts).toBe(true);
        expect(result.current.conflicts).toHaveLength(1);
      });
    });
  });

  describe('Conflict Resolution Events', () => {
    test('registers conflict_resolved event listener', async () => {
      renderHook(() =>
        useWorktreeConflictsWS('wt-1', undefined, undefined, true)
      );

      await waitFor(() => {
        expect(mockWebSocketClient.on).toHaveBeenCalledWith(
          'conflict_resolved',
          expect.any(Function)
        );
      });
    });

    test('clears conflicts when conflict_resolved event received', async () => {
      let conflictDetectedHandler: Function | null = null;
      let conflictResolvedHandler: Function | null = null;

      mockWebSocketClient.on.mockImplementation((event: string, handler: Function) => {
        if (event === 'conflict_detected') {
          conflictDetectedHandler = handler;
        } else if (event === 'conflict_resolved') {
          conflictResolvedHandler = handler;
        }
      });

      const { result } = renderHook(() =>
        useWorktreeConflictsWS('wt-1', undefined, undefined, true)
      );

      await waitFor(() => {
        expect(conflictDetectedHandler).toBeDefined();
      });

      // First, detect conflicts
      if (conflictDetectedHandler) {
        act(() => {
          conflictDetectedHandler!({
            data: {
              conflicts: [{ path: 'src/main.py', status: 'conflicted' }],
              preview: { has_conflicts: true, conflicting_files: ['src/main.py'] },
            },
          });
        });
      }

      await waitFor(() => {
        expect(result.current.hasConflicts).toBe(true);
      });

      // Then, resolve conflicts
      if (conflictResolvedHandler) {
        act(() => {
          conflictResolvedHandler!({});
        });
      }

      await waitFor(() => {
        expect(result.current.hasConflicts).toBe(false);
      });
    });
  });

  describe('Fallback to Polling', () => {
    test('indicates WebSocket mode when connected', async () => {
      mockWebSocketClient.getState.mockReturnValue('connected');

      const { result } = renderHook(() =>
        useWorktreeConflictsWS('wt-1', undefined, undefined, true)
      );

      await waitFor(() => {
        expect(result.current.isWebSocket).toBe(true);
      });
    });

    test('falls back to polling after connection timeout', async () => {
      mockWebSocketClient.getState.mockReturnValue('connecting');

      const { result } = renderHook(() =>
        useWorktreeConflictsWS('wt-1', undefined, undefined, true)
      );

      // Simulate timeout (30 seconds)
      act(() => {
        jest.advanceTimersByTime(31000);
      });

      await waitFor(() => {
        expect(result.current.isWebSocket).toBe(false);
      });
    });
  });

  describe('Connection State', () => {
    test('reports connection state', async () => {
      mockWebSocketClient.getState.mockReturnValue('connected');

      const { result } = renderHook(() =>
        useWorktreeConflictsWS('wt-1', undefined, undefined, true)
      );

      await waitFor(() => {
        expect(result.current.connectionState).toBe('connected');
      });
    });
  });

  describe('Data Structure', () => {
    test('maintains conflict data structure', async () => {
      let eventHandler: Function | null = null;

      mockWebSocketClient.on.mockImplementation((event: string, handler: Function) => {
        if (event === 'conflict_detected') {
          eventHandler = handler;
        }
      });

      const { result } = renderHook(() =>
        useWorktreeConflictsWS('wt-1', undefined, undefined, true)
      );

      // ConflictFile's field is `path` (see src/hooks/useWorktreeConflicts.ts).
      const mockData = {
        conflicts: [
          {
            path: 'src/auth.ts',
            status: 'conflicted',
            ours_lines: 5,
            theirs_lines: 3,
            preview: '<<<<<<< HEAD\n...',
          },
          {
            path: 'src/config.ts',
            status: 'conflicted',
            ours_lines: 2,
            theirs_lines: 1,
          },
        ],
        preview: {
          has_conflicts: true,
          conflicting_files: ['src/auth.ts', 'src/config.ts'],
          auto_mergeable: false,
          severity: 'high',
        },
        severity: 'high',
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
        expect(result.current.conflicts).toHaveLength(2);
        expect(result.current.conflicts[0].path).toBe('src/auth.ts');
        expect(result.current.preview?.severity).toBe('high');
      });
    });
  });

  describe('Error Handling', () => {
    test('handles missing conflict data gracefully', async () => {
      let eventHandler: Function | null = null;

      mockWebSocketClient.on.mockImplementation((event: string, handler: Function) => {
        if (event === 'conflict_detected') {
          eventHandler = handler;
        }
      });

      renderHook(() =>
        useWorktreeConflictsWS('wt-1', undefined, undefined, true)
      );

      await waitFor(() => {
        expect(eventHandler).toBeDefined();
      });

      // Should not throw with partial data
      if (eventHandler) {
        expect(() => {
          act(() => {
            eventHandler!({ conflicts: [] });
          });
        }).not.toThrow();
      }
    });
  });
});
