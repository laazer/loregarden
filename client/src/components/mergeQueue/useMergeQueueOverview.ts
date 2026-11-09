import { useQuery } from "@tanstack/react-query";

import { mergeQueueApi, mergeQueueKeys, type MergeQueueEntry, type MergeQueueView } from "../../api/mergeQueueApi";
import { parseTimestamp } from "../../lib/timestamps";
import { QUEUE_REFETCH_MS, isFinished } from "./mergeQueueLabels";

/** Every workspace's queue, read once and shared by Home and the Lanes page through the cache. */
export function useMergeQueueOverview() {
  return useQuery({
    queryKey: mergeQueueKeys.overview,
    queryFn: () => mergeQueueApi.overview(),
    refetchInterval: QUEUE_REFETCH_MS,
  });
}

/** Entries still in line, across workspaces, in each queue's own order. */
export function liveEntries(queues: readonly MergeQueueView[]): MergeQueueEntry[] {
  return queues.flatMap((queue) => queue.entries.filter((entry) => !isFinished(entry.state)));
}

/** Failed or timed-out entries: stopped and waiting on someone. A withdrawal was someone's choice. */
export function stoppedEntries(queues: readonly MergeQueueView[]): MergeQueueEntry[] {
  return queues.flatMap((queue) =>
    queue.entries.filter((entry) => entry.state === "failed" || entry.state === "timed_out"),
  );
}

/** Entries that landed since ``since``. */
export function landedSince(queues: readonly MergeQueueView[], since: Date): MergeQueueEntry[] {
  return queues.flatMap((queue) =>
    queue.entries.filter((entry) => {
      // The server sends naive UTC; `parseTimestamp` reads it as UTC, `new Date` would not.
      const at = parseTimestamp(entry.state_changed_at);
      return entry.state === "merged" && at !== null && at >= since;
    }),
  );
}
