/**
 * The execution lanes, fetched once however many things are watching.
 *
 * Three callers now want the same list — the lane picker in the run dialogs,
 * the `lane` settings source, and any number of Queue Lane panes in a view —
 * and each of the alternatives is wrong for one of them:
 *
 * - `useParallelExecution` keeps its own `setInterval` per hook call, so a grid
 *   with one pane per lane would poll the server three times for one answer.
 * - `useParallelExecutionWS` opens a websocket per call, which is worse.
 * - `useQueueStatus` holds the app-wide socket's snapshot and would be free,
 *   but it is page-level state: a container primitive may not import from
 *   `state/`, because reading a zustand store or a context outside its provider
 *   returns an empty value instead of throwing, and a pane silently showing
 *   nothing is the failure that rule exists to prevent.
 *
 * A react-query key is the one shape that answers all three: the cache dedupes
 * concurrent callers to a single request, the interval belongs to the query
 * rather than to each caller, and a pane that mounts anywhere still gets data.
 * The queue socket remains the live path for the dashboard; this is the polled
 * one, and 5s matches what the picker already used.
 */

import { useQuery } from "@tanstack/react-query";

import { API_BASE } from "../api/client";
import type { QueueLane } from "../lib/queueSocket";

/** Spelled once: two keys for one fetch is two caches that drift apart. */
export const QUEUE_LANES_KEY = ["queue-lanes"] as const;

const POLL_INTERVAL = 5000;

export async function fetchQueueLanes(): Promise<QueueLane[]> {
  const response = await fetch(`${API_BASE}/api/parallel/status`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json();
  return (data.lanes ?? []) as QueueLane[];
}

export interface QueueLanesResult {
  lanes: QueueLane[];
  isLoading: boolean;
  isError: boolean;
}

export function useQueueLanes(enabled = true): QueueLanesResult {
  const query = useQuery({
    queryKey: QUEUE_LANES_KEY,
    queryFn: fetchQueueLanes,
    enabled,
    refetchInterval: POLL_INTERVAL,
  });

  return {
    lanes: query.data ?? [],
    // A disabled query is pending forever, which is not the same as waiting for
    // one in flight — a caller told to render nothing would spin instead.
    isLoading: enabled && query.isLoading,
    isError: query.isError,
  };
}
