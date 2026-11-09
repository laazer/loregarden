import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { mergeQueueApi, mergeQueueKeys, type MergeQueueEntry } from "../../api/mergeQueueApi";
import { NotThisTarget } from "../../lib/agentActions/registry";
import { useAgentAction } from "../../lib/agentActions/useAgentAction";
import { toastActionFailed } from "../../state/toastStore";

export type QueueAction = "retry" | "withdraw";

export interface MergeQueueActions {
  /** The action in flight for each entry; an entry in here takes no second click. */
  busy: ReadonlyMap<string, QueueAction>;
  /** Run an action from a button: a failure is a toast naming the PR. */
  run: (entry: MergeQueueEntry, action: QueueAction) => void;
}

/**
 * Retry and withdraw for the entries one surface shows, offered to agents too.
 *
 * Several surfaces can be mounted at once (a ticket's strip, Home, the Lanes
 * page), each registering the same agent actions; one that does not show the
 * entry asked for answers `NotThisTarget`, so the request reaches the one that
 * does. Every merge-queue read is invalidated afterwards, so all of them agree.
 */
export function useMergeQueueActions(entries: readonly MergeQueueEntry[], where: string): MergeQueueActions {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState<ReadonlyMap<string, QueueAction>>(new Map());

  const act = async (entry: MergeQueueEntry, action: QueueAction) => {
    if (busy.has(entry.id)) return undefined;
    setBusy((prev) => new Map(prev).set(entry.id, action));
    try {
      return action === "retry"
        ? await mergeQueueApi.retry(entry.id)
        : await mergeQueueApi.withdraw(entry.id, `withdrawn from ${where}`);
    } finally {
      setBusy((prev) => {
        const next = new Map(prev);
        next.delete(entry.id);
        return next;
      });
      void queryClient.invalidateQueries({ queryKey: mergeQueueKeys.all });
    }
  };
  const run = (entry: MergeQueueEntry, action: QueueAction) =>
    void act(entry, action).catch((error: unknown) =>
      toastActionFailed(`${action === "retry" ? "Retry" : "Withdraw"} PR #${entry.pr_number}`, error),
    );

  const shown = (entryId: string) => {
    const entry = entries.find((candidate) => candidate.id === entryId);
    if (!entry) throw new NotThisTarget(`merge queue entry ${entryId} is not shown on ${where}`);
    return entry;
  };
  useAgentAction("merge_queue.retry", async ({ entry_id }) => act(shown(entry_id), "retry"));
  useAgentAction("merge_queue.withdraw", async ({ entry_id }) => act(shown(entry_id), "withdraw"));

  return { busy, run };
}
