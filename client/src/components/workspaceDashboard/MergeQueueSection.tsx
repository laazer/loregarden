import { useQuery } from "@tanstack/react-query";

import { mergeQueueApi, mergeQueueKeys } from "../../api/mergeQueueApi";
import { MergeQueueRow } from "../mergeQueue/MergeQueueRow";
import { QUEUE_REFETCH_MS, isFinished } from "../mergeQueue/mergeQueueLabels";
import { useMergeQueueActions } from "../mergeQueue/useMergeQueueActions";
import { DashboardSection } from "./DashboardSection";

interface MergeQueueSectionProps {
  workspaceSlug: string;
  now: Date;
}

/**
 * "What is landing on main, and what is the head entry waiting for?"
 *
 * A list, not a board: the queue is usually empty or one deep, and a board of
 * empty columns answers nothing. Each entry's name opens its pull request; the
 * head can be withdrawn while that is still safe, and a failed entry says why
 * and can be sent back in line.
 */
export function MergeQueueSection({ workspaceSlug, now }: MergeQueueSectionProps) {
  const queue = useQuery({
    queryKey: mergeQueueKeys.queue(workspaceSlug),
    queryFn: () => mergeQueueApi.queue(workspaceSlug),
    refetchInterval: QUEUE_REFETCH_MS,
  });
  const entries = queue.data?.entries ?? [];
  const live = entries.filter((entry) => !isFinished(entry.state));
  // What landed is history; what is in line or stopped is what the operator acts on.
  const landed = entries.filter((entry) => entry.state === "merged");
  const current = entries.filter((entry) => entry.state !== "merged");
  const { busy, run } = useMergeQueueActions(entries, "the workspace dashboard");

  return (
    <DashboardSection
      id="wsd-merge-queue"
      title="Merge queue"
      count={queue.data ? `${live.length} in line` : undefined}
      queries={[queue]}
      errorTitle="Could not read the merge queue"
      empty={entries.length === 0}
      emptyMessage={
        <>
          Nothing is landing on main. A tree joins the queue when its pull request is published with auto-merge on, and
          any other pull request when an agent calls <code>loregarden_enqueue_merge</code> with its number; the queue
          then tests it on main&rsquo;s tip and lands exactly what passed.
        </>
      }
      skeletonRows={2}
    >
      {current.length > 0 ? (
        <ul className="mq-rows">
          {current.map((entry) => (
            <MergeQueueRow key={entry.id} entry={entry} now={now} busy={busy.get(entry.id)} onAct={run} />
          ))}
        </ul>
      ) : (
        <p className="wsd-section-empty">Nothing is in line; the last landings are below.</p>
      )}
      {landed.length > 0 ? (
        <details className="mq-landed">
          <summary>Landed recently ({landed.length})</summary>
          <ul className="mq-rows">
            {landed.map((entry) => (
              <MergeQueueRow key={entry.id} entry={entry} now={now} busy={undefined} onAct={run} />
            ))}
          </ul>
        </details>
      ) : null}
    </DashboardSection>
  );
}
