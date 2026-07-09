/**
 * React hook for worktree merge conflicts via WebSocket.
 * Provides real-time conflict detection with fallback to polling if WebSocket fails.
 * Maintains same interface as useWorktreeConflicts for seamless replacement.
 */

import { useEffect, useState, useRef } from 'react';
import { getWebSocketClient } from '../services/websocket';
import { useWorktreeConflicts } from './useWorktreeConflicts';
import type {
  ConflictFile,
  ConflictPreview,
  WorktreeConflictDetails,
} from './useWorktreeConflicts';

export interface WorktreeConflictsWSStatus {
  conflicts: ConflictFile[];
  preview: ConflictPreview | null;
  details: WorktreeConflictDetails | null;
  hasConflicts: boolean;
  loading: boolean;
  error: string | null;
  connectionState: 'disconnected' | 'connecting' | 'connected' | 'error';
  isWebSocket: boolean;
}

const DEFAULT_POLL_FALLBACK_TIMEOUT = 30000; // 30 seconds

export function useWorktreeConflictsWS(
  worktreeId: string,
  runId?: string,
  fallbackTimeout: number = DEFAULT_POLL_FALLBACK_TIMEOUT,
  enabled: boolean = true,
  useFallback: boolean = true
): WorktreeConflictsWSStatus {
  const [conflicts, setConflicts] = useState<ConflictFile[]>([]);
  const [preview, setPreview] = useState<ConflictPreview | null>(null);
  const [details, setDetails] = useState<WorktreeConflictDetails | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const [connectionState, setConnectionState] = useState<
    'disconnected' | 'connecting' | 'connected' | 'error'
  >('disconnected');
  const [isWebSocket, setIsWebSocket] = useState(true);

  const wsClient = getWebSocketClient();
  const fallbackHook = useWorktreeConflicts(worktreeId, 3000, enabled);
  const fallbackTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }

    let isMounted = true;

    const initializeWebSocket = async () => {
      try {
        // Connect to WebSocket
        await wsClient.connect();
        if (!isMounted) return;

        setConnectionState('connected');
        setIsWebSocket(true);
        setError(null);
        setLoading(false);

        // Join worktree room
        wsClient.joinWorktree(worktreeId, runId);

        // Set up event handlers for conflict updates
        const handleConflictDetected = (data: any) => {
          if (!isMounted) return;

          const { data: conflictData } = data;
          if (conflictData) {
            setConflicts(conflictData.conflicts || []);
            setPreview(conflictData.preview || null);
            setDetails({
              worktree_id: data.worktreeId,
              run_id: data.runId,
              conflicts: conflictData.conflicts || [],
              merge_preview: conflictData.preview || {
                conflicting_files: [],
                total_conflicts: 0,
                auto_mergeable_count: 0,
                severity: 'low',
              },
              timestamp: data.timestamp,
            });
            setError(null);
          }
        };

        const handleConflictResolved = () => {
          if (!isMounted) return;

          // Clear conflicts
          setConflicts([]);
          setPreview(null);
          setDetails(null);
          setError(null);
        };

        wsClient.on('conflict_detected', handleConflictDetected);
        wsClient.on('conflict_resolved', handleConflictResolved);

        // Set up connection state listeners
        const handleStateChange = (data: any) => {
          if (!isMounted) return;
          const newState = data.state as typeof connectionState;
          setConnectionState(newState);

          if (newState === 'error' || newState === 'disconnected') {
            // Start fallback timeout
            if (useFallback) {
              startFallbackTimer();
            }
          }
        };

        wsClient.on('websocket:state_change', handleStateChange);

        const handleServerError = (data: any) => {
          if (!isMounted) return;
          const errorMsg = data.message || 'WebSocket error';
          setError(errorMsg);
        };

        wsClient.on('websocket:server_error', handleServerError);

        // Cleanup on unmount
        return () => {
          wsClient.off('conflict_detected', handleConflictDetected);
          wsClient.off('conflict_resolved', handleConflictResolved);
          wsClient.off('websocket:state_change', handleStateChange);
          wsClient.off('websocket:server_error', handleServerError);
          wsClient.leaveWorktree(worktreeId);
        };
      } catch (err) {
        if (!isMounted) return;

        // WebSocket connection failed, fall back to polling
        setIsWebSocket(false);
        setConnectionState('error');

        if (useFallback) {
          setError('WebSocket unavailable, using polling');
          setLoading(false);
          // Use fallback polling hook
          return;
        } else {
          const errorMsg = err instanceof Error ? err.message : 'Failed to connect';
          setError(errorMsg);
          setLoading(false);
        }
      }
    };

    const startFallbackTimer = () => {
      if (fallbackTimeoutRef.current) {
        clearTimeout(fallbackTimeoutRef.current);
      }

      if (!useFallback) return;

      fallbackTimeoutRef.current = setTimeout(() => {
        if (!isMounted) return;

        // Switch to polling fallback
        setIsWebSocket(false);
        setError('Using polling due to WebSocket timeout');
      }, fallbackTimeout);
    };

    initializeWebSocket();

    return () => {
      if (fallbackTimeoutRef.current) {
        clearTimeout(fallbackTimeoutRef.current);
      }
    };
  }, [worktreeId, runId, fallbackTimeout, enabled, useFallback]);

  // Use fallback polling hook data if WebSocket fails
  if (!isWebSocket) {
    return {
      conflicts: fallbackHook.conflicts,
      preview: fallbackHook.preview,
      details: fallbackHook.details,
      hasConflicts: fallbackHook.hasConflicts,
      loading: fallbackHook.loading,
      error: fallbackHook.error,
      connectionState: 'disconnected',
      isWebSocket: false,
    };
  }

  return {
    conflicts,
    preview,
    details,
    hasConflicts: conflicts.length > 0,
    loading,
    error,
    connectionState,
    isWebSocket: true,
  };
}
