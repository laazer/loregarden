/**
 * React hook for parallel execution status via WebSocket.
 * Provides real-time updates with fallback to polling if WebSocket fails.
 * Maintains same interface as useParallelExecution for seamless replacement.
 */

import { useEffect, useState, useRef } from 'react';
import { getWebSocketClient } from '../services/websocket';
import { useParallelExecution } from './useParallelExecution';
import type { ActiveRun, QueuedRun, ParallelStats } from './useParallelExecution';

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
// How often to re-check wsClient.getState() directly. Socket.IO doesn't emit
// a 'websocket:state_change' event while stuck in 'connecting' (no error, no
// success), so relying on events alone can never notice — and can never
// notice a later recovery once we've fallen back to polling. This interval
// is the only thing that catches both.
const STATE_POLL_INTERVAL = 1000;

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
    let cleanupListeners: (() => void) | undefined;
    let pollIntervalId: NodeJS.Timeout | null = null;

    // Schedule the fallback-to-polling timeout at most once per "not
    // connected" episode. Calling this repeatedly (e.g. from every poll
    // tick) must NOT keep pushing the deadline out, or a connection stuck
    // in 'connecting' would postpone fallback forever.
    const scheduleFallbackIfNeeded = () => {
      if (!useFallback) return;
      if (fallbackTimeoutRef.current) return;

      fallbackTimeoutRef.current = setTimeout(() => {
        fallbackTimeoutRef.current = null;
        if (!isMounted) return;

        setIsWebSocket(false);
        setError('Using polling due to WebSocket timeout');
      }, fallbackTimeout);
    };

    const clearFallbackTimer = () => {
      if (fallbackTimeoutRef.current) {
        clearTimeout(fallbackTimeoutRef.current);
        fallbackTimeoutRef.current = null;
      }
    };

    // Single source of truth for reacting to a connection state, whether it
    // came from an explicit 'websocket:state_change' event or from directly
    // polling wsClient.getState().
    const syncConnectionState = (state: typeof connectionState) => {
      if (!isMounted) return;
      setConnectionState(state);

      if (state === 'connected') {
        clearFallbackTimer();
        setIsWebSocket(true);
      } else {
        scheduleFallbackIfNeeded();
      }
    };

    // Start polling immediately, synchronously, rather than waiting for the
    // connect() promise to settle: the fallback countdown needs to start the
    // moment we begin trying to connect, not whenever connect()'s resolution
    // happens to be scheduled, or a connection stuck in 'connecting' could
    // silently postpone the deadline.
    pollIntervalId = setInterval(() => {
      if (!isMounted) return;
      syncConnectionState(wsClient.getState());
    }, STATE_POLL_INTERVAL);

    const initializeWebSocket = async () => {
      try {
        // Connect to WebSocket
        await wsClient.connect(userId);
        if (!isMounted) return;

        // Trust the client's actual reported state rather than assuming
        // "connect() resolved" always means 'connected' — defensive against
        // any race between the resolved promise and the client's internal
        // state.
        syncConnectionState(wsClient.getState());
        setError(null);
        setLoading(false);

        // Join workspace room
        wsClient.joinWorkspace(workspaceId);

        // Set up event handler for execution updates
        const handleExecutionUpdate = (data: any) => {
          if (!isMounted) return;
          // Defend against a malformed/missing event payload (e.g. a null
          // event) so a bad server message can't crash the hook.
          if (!data) return;

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
          syncConnectionState(data.state as typeof connectionState);
        };

        wsClient.on('websocket:state_change', handleStateChange);

        const handleServerError = (data: any) => {
          if (!isMounted) return;
          const errorMsg = data.message || 'WebSocket error';
          setError(errorMsg);
        };

        wsClient.on('websocket:server_error', handleServerError);

        // Register cleanup to run when the effect tears down (unmount or
        // dependency change). Previously this was returned from inside this
        // async function, whose resolved value is discarded by the caller
        // below, so listeners/room membership were never actually released.
        cleanupListeners = () => {
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
          const errorMsg = err instanceof Error ? `Failed to connect: ${err.message}` : 'Failed to connect';
          setError(errorMsg);
          setLoading(false);
        }
      }
    };

    initializeWebSocket();

    return () => {
      isMounted = false;
      if (fallbackTimeoutRef.current) {
        clearTimeout(fallbackTimeoutRef.current);
        fallbackTimeoutRef.current = null;
      }
      if (pollIntervalId) {
        clearInterval(pollIntervalId);
        pollIntervalId = null;
      }
      if (cleanupListeners) {
        cleanupListeners();
      }
    };
  }, [workspaceId, userId, fallbackTimeout, useFallback]);

  // Use fallback polling hook data if WebSocket fails, but only when the
  // caller actually opted into fallback polling (`useFallback`). Otherwise
  // `isWebSocket: false` just reports connection failure and the hook's own
  // error/connection state (set in the catch block above) must still surface
  // — previously this branch ignored `useFallback` entirely and always
  // substituted the (unrelated) polling hook's data.
  if (!isWebSocket && useFallback) {
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
    isWebSocket,
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
