/**
 * React hook for parallel execution status and management.
 * Polls API for active/queued runs and queue statistics.
 */

import { useEffect, useState } from 'react';

export interface ActiveRun {
  run_id: string;
  ticket_id: string;
  slot_number: number;
  elapsed_seconds: number;
  status: string;
  agent_id: string;
}

export interface QueuedRun {
  run_id: string;
  ticket_id: string;
  position: number;
  estimated_start_at: string;
  wait_seconds: number;
  agent_id: string;
}

export interface ParallelStats {
  max_concurrent: number;
  active_count: number;
  available_slots: number;
  queued_count: number;
  total_slots_occupied: number;
  queue_wait_time_minutes: number;
}

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
  pollInterval: number = DEFAULT_POLL_INTERVAL
): ParallelExecutionStatus {
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

  useEffect(() => {
    let isMounted = true;
    let intervalId: ReturnType<typeof setInterval>;

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
  }, [workspaceId, pollInterval]);

  return {
    activeRuns,
    queuedRuns,
    stats,
    loading,
    error,
  };
}
