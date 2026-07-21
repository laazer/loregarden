/**
 * Parallel execution status, pushed rather than polled.
 *
 * Same shape as `useParallelExecution`, so the components that render the
 * queue did not have to change. The difference is where the data comes from:
 * a websocket while one is up, the polling hook while it is not — and the
 * caller is told honestly which, because the dashboard badge says so.
 *
 * The previous version dialled a Socket.IO server that was never mounted, so
 * it sat in 'connecting' indefinitely, needed a 30-second timer and a
 * once-a-second state poll to notice, and reported "Polling" forever. There is
 * no timeout here: the socket says when it is open and when it is not, and
 * both are acted on immediately.
 */

import { useEffect, useMemo, useState } from 'react';

import { API_BASE } from '../api/client';
import {
  DEFAULT_PARALLEL_STATS,
  QueueSocket,
  queueSocketUrl,
} from '../lib/queueSocket';
import type {
  ActiveRun,
  ParallelStats,
  QueuedRun,
  QueueSocketStatus,
  QueueStatusSnapshot,
} from '../lib/queueSocket';
import { useParallelExecution } from './useParallelExecution';

export type { ActiveRun, ParallelStats, QueuedRun };

export interface ParallelExecutionWSStatus {
  activeRuns: ActiveRun[];
  queuedRuns: QueuedRun[];
  stats: ParallelStats;
  loading: boolean;
  error: string | null;
  connectionState: QueueSocketStatus;
  isWebSocket: boolean;
}

/** How often the fallback polls while the socket is down. */
const FALLBACK_POLL_INTERVAL = 5000;

export function useParallelExecutionWS(
  workspaceId: string,
  enabled: boolean = true
): ParallelExecutionWSStatus {
  const [snapshot, setSnapshot] = useState<QueueStatusSnapshot | null>(null);
  const [status, setStatus] = useState<QueueSocketStatus>('connecting');

  const live = enabled && Boolean(workspaceId) && status === 'open';

  // Polls only while the socket is not carrying the data. This is the whole
  // saving: a healthy dashboard makes no status requests at all.
  const fallback = useParallelExecution(workspaceId, FALLBACK_POLL_INTERVAL, !live);

  useEffect(() => {
    if (!enabled || !workspaceId) {
      setStatus('closed');
      return;
    }

    setStatus('connecting');
    const socket = new QueueSocket(queueSocketUrl(workspaceId, API_BASE), {
      onSnapshot: setSnapshot,
      onStatus: setStatus,
    });
    socket.open();

    return () => {
      socket.close();
      // Drop the snapshot with the socket it came from: keeping it would show
      // one workspace's queue under another workspace's name for as long as
      // the new socket takes to deliver its first push.
      setSnapshot(null);
    };
  }, [workspaceId, enabled]);

  return useMemo(() => {
    if (live && snapshot) {
      return {
        activeRuns: snapshot.active_runs,
        queuedRuns: snapshot.queued_runs,
        stats: snapshot.stats ?? DEFAULT_PARALLEL_STATS,
        loading: false,
        error: null,
        connectionState: status,
        isWebSocket: true,
      };
    }

    return {
      activeRuns: fallback.activeRuns,
      queuedRuns: fallback.queuedRuns,
      stats: fallback.stats,
      // Connected but still waiting on the first frame is loading too, or the
      // dashboard would flash an empty queue before its first snapshot.
      loading: live ? true : fallback.loading,
      error: fallback.error,
      connectionState: status,
      isWebSocket: false,
    };
  }, [live, snapshot, status, fallback]);
}
