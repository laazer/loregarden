/**
 * React hook for parallel execution status and management.
 * Polls API for active/queued runs and queue statistics.
 */

import { useEffect, useState } from 'react';

import { DEFAULT_PARALLEL_STATS } from '../lib/queueSocket';
import type { ActiveRun, ParallelStats, QueuedRun } from '../lib/queueSocket';

// The shapes moved down to lib/queueSocket, where the socket that carries them
// lives; re-exported so components keep importing them from the hook they use.
export type { ActiveRun, ParallelStats, QueuedRun };

export interface ParallelExecutionStatus {
  activeRuns: ActiveRun[];
  queuedRuns: QueuedRun[];
  stats: ParallelStats;
  loading: boolean;
  error: string | null;
}

const DEFAULT_POLL_INTERVAL = 5000; // 5 seconds

export function useParallelExecution(
  workspaceId: string,
  pollInterval: number = DEFAULT_POLL_INTERVAL,
  /**
   * Off while the queue socket is carrying this data. React forbids calling a
   * hook conditionally, so the only way for `useParallelExecutionWS` to stop
   * polling once its socket is up is to say so here — otherwise every
   * dashboard would poll *and* hold a socket, which is worse than either.
   */
  enabled: boolean = true
): ParallelExecutionStatus {
  const [activeRuns, setActiveRuns] = useState<ActiveRun[]>([]);
  const [queuedRuns, setQueuedRuns] = useState<QueuedRun[]>([]);
  const [stats, setStats] = useState<ParallelStats>(DEFAULT_PARALLEL_STATS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;
    let intervalId: ReturnType<typeof setInterval>;

    // No workspace, nothing to ask about. Callers resolve the workspace from a
    // query, so the first render always has an empty id. Without this guard it
    // polled `/api/parallel/status/` with no id every few seconds, 404ing each
    // time while the page looked fine.
    if (!workspaceId || !enabled) {
      setLoading(false);
      return;
    }

    const fetchStatus = async () => {
      try {
        const response = await fetch(
          `/api/parallel/status/${workspaceId}`
        );

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();

        if (isMounted) {
          setActiveRuns(data.active_runs || []);
          setQueuedRuns(data.queued_runs || []);
          setStats(data.stats || {
            max_concurrent: 3,
            active_count: data.active_runs?.length || 0,
            available_slots: data.available_slots || 3,
            queued_count: data.queued_runs?.length || 0,
            total_slots_occupied: data.active_runs?.length || 0,
            queue_wait_time_minutes: 0,
          });
          setError(null);
        }
      } catch (err) {
        if (isMounted) {
          setError(err instanceof Error ? err.message : 'Failed to fetch status');
          // Keep previous data on error
        }
      } finally {
        if (isMounted) {
          setLoading(false);
        }
      }
    };

    // Initial fetch
    fetchStatus();

    // Poll for updates
    intervalId = setInterval(fetchStatus, pollInterval);

    return () => {
      isMounted = false;
      clearInterval(intervalId);
    };
  }, [workspaceId, pollInterval, enabled]);

  return {
    activeRuns,
    queuedRuns,
    stats,
    loading,
    error,
  };
}
