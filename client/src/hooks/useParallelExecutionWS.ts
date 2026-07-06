/**
 * React hook for parallel execution status via WebSocket.
 * Provides real-time updates with fallback to polling if WebSocket fails.
 * Maintains same interface as useParallelExecution for seamless replacement.
 */

import { useEffect, useState, useRef } from 'react';
import { getWebSocketClient } from '../services/websocket';
import { useParallelExecution, ActiveRun, QueuedRun, ParallelStats } from './useParallelExecution';

export interface ParallelExecutionWSStatus {
  activeRuns: ActiveRun[];
  queuedRuns: QueuedRun[];
  stats: ParallelStats;
  loading: boolean;
  error: string | null;
  connectionState: 'disconnected' | 'connecting' | 'connected' | 'error';
  isWebSocket: boolean;
}

const DEFAULT_POLL_FALLBACK_TIMEOUT = 30000; // 30 seconds

export function useParallelExecutionWS(
  workspaceId: string,
  userId?: string,
  fallbackTimeout: number = DEFAULT_POLL_FALLBACK_TIMEOUT,
  useFallback: boolean = true
): ParallelExecutionWSStatus {
  const [activeRuns, setActiveRuns] = useState<ActiveRun[]>([]);
  const [queuedRuns, setQueuedRuns] = useState<QueuedRun[]>([]);
  const [stats, setStats] = useState<ParallelStats>({
    max_concurrent: 3,
    active_count: 0,
    available_slots: 3,
    queued_count: 0,
    total_slots_occupied: 0,
    queue_wait_time_minutes: 0,
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [connectionState, setConnectionState] = useState<
    'disconnected' | 'connecting' | 'connected' | 'error'
  >('disconnected');
  const [isWebSocket, setIsWebSocket] = useState(true);

  const wsClient = getWebSocketClient();
  const fallbackHook = useParallelExecution(workspaceId, 5000);
  const fallbackTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    let isMounted = true;

    const initializeWebSocket = async () => {
      try {
        // Connect to WebSocket
        await wsClient.connect(userId);
        if (!isMounted) return;

        setConnectionState('connected');
        setIsWebSocket(true);
        setError(null);
        setLoading(false);

        // Join workspace room
        wsClient.joinWorkspace(workspaceId);

        // Set up event handler for execution updates
        const handleExecutionUpdate = (data: any) => {
          if (!isMounted) return;

          const { data: updateData } = data;
          if (updateData) {
            setActiveRuns(updateData.activeRuns || []);
            setQueuedRuns(updateData.queuedRuns || []);
            setStats(updateData.stats || stats);
            setError(null);
          }
        };

        wsClient.on('execution_update', handleExecutionUpdate);

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
          wsClient.off('execution_update', handleExecutionUpdate);
          wsClient.off('websocket:state_change', handleStateChange);
          wsClient.off('websocket:server_error', handleServerError);
          wsClient.leaveWorkspace(workspaceId);
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
  }, [workspaceId, userId, fallbackTimeout, useFallback]);

  // Use fallback polling hook data if WebSocket fails
  if (!isWebSocket) {
    return {
      activeRuns: fallbackHook.activeRuns,
      queuedRuns: fallbackHook.queuedRuns,
      stats: fallbackHook.stats,
      loading: fallbackHook.loading,
      error: fallbackHook.error,
      connectionState: 'disconnected',
      isWebSocket: false,
    };
  }

  return {
    activeRuns,
    queuedRuns,
    stats,
    loading,
    error,
    connectionState,
    isWebSocket: true,
  };
}

/**
 * Hook for getting WebSocket connection state only.
 * Useful for displaying connection indicator.
 */
export function useWebSocketConnectionState() {
  const [state, setState] = useState<
    'disconnected' | 'connecting' | 'connected' | 'error'
  >('disconnected');

  const wsClient = getWebSocketClient();

  useEffect(() => {
    // Get initial state
    setState(wsClient.getState());

    // Listen for state changes
    const handleStateChange = (data: any) => {
      setState(data.state);
    };

    wsClient.on('websocket:state_change', handleStateChange);

    return () => {
      wsClient.off('websocket:state_change', handleStateChange);
    };
  }, []);

  return state;
}
