import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  mergeQueueApi,
  mergeQueueKeys,
  type MergeQueueEntry,
  type MergeQueueState,
  type MergeQueueWait,
} from "../../api/mergeQueueApi";
import { useAgentAction } from "../../lib/agentActions/useAgentAction";
import { formatRelativeAge } from "../../lib/timestamps";
import { describeError, toastActionFailed } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { DashboardSection } from "./DashboardSection";

/** The loop advances on the CI poll tick (120s); reading faster shows nothing new. */
const QUEUE_REFETCH_MS = 30_000;

const STATE_LABELS: Record<MergeQueueState, string> = {
  queued: "Queued",
  staging: "Staging",
  testing: "Testing",
  merging: "Merging",
  merged: "Merged",
  failed: "Failed",
  timed_out: "Timed out",
  withdrawn: "Withdrawn",
};

/** Why a live entry is not moving, in the operator's words. */
const WAIT_LABELS: Record<MergeQueueWait, string> = {
  "": "",
  behind: "behind the head",
  checks_unreported: "waiting for required checks to start",
  checks_running: "waiting for required checks to finish",
  github_unreachable: "GitHub did not answer; retrying",
  base_moved: "the base moved; staging again",
  conflict_artifact_open: "conflicts with the base; resolve the bug ticket",
};

type Busy = Map<string, "retry" | "withdraw">;

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
  const queryClient = useQueryClient();
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

  const [busy, setBusy] = useState<Busy>(new Map());
  const act = async (entry: MergeQueueEntry, action: "retry" | "withdraw") => {
    if (busy.has(entry.id)) return undefined;
    setBusy((prev) => new Map(prev).set(entry.id, action));
    try {
      return action === "retry"
        ? await mergeQueueApi.retry(entry.id)
        : await mergeQueueApi.withdraw(entry.id, "withdrawn from the workspace dashboard");
    } finally {
      setBusy((prev) => {
        const next = new Map(prev);
        next.delete(entry.id);
        return next;
      });
      void queryClient.invalidateQueries({ queryKey: mergeQueueKeys.queue(workspaceSlug) });
    }
  };
  const run = (entry: MergeQueueEntry, action: "retry" | "withdraw") =>
    void act(entry, action).catch((error: unknown) =>
      toastActionFailed(`${action === "retry" ? "Retry" : "Withdraw"} PR #${entry.pr_number}`, error),
    );

  const listed = (entryId: string) => {
    const entry = entries.find((candidate) => candidate.id === entryId);
    if (!entry) throw new Error(`merge queue entry ${entryId} is not on ${workspaceSlug}'s dashboard`);
    return entry;
  };
  useAgentAction("merge_queue.retry", async ({ entry_id }) => act(listed(entry_id), "retry"));
  useAgentAction("merge_queue.withdraw", async ({ entry_id }) => act(listed(entry_id), "withdraw"));

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
          Nothing is landing on main. A tree joins the queue when its pull request is published with auto-merge on, or
          when an agent calls <code>loregarden_enqueue_merge</code>; the queue then tests it on main&rsquo;s tip and
          lands exactly what passed.
        </>
      }
      skeletonRows={2}
    >
      {current.length > 0 ? (
        <ul className="wsd-rows">
          {current.map((entry) => (
            <QueueRow key={entry.id} entry={entry} now={now} busy={busy.get(entry.id)} onAct={run} />
          ))}
        </ul>
      ) : (
        <p className="wsd-section-empty">Nothing is in line; the last landings are below.</p>
      )}
      {landed.length > 0 ? (
        <details className="wsd-mq-landed">
          <summary>Landed recently ({landed.length})</summary>
          <ul className="wsd-rows">
            {landed.map((entry) => (
              <QueueRow key={entry.id} entry={entry} now={now} busy={undefined} onAct={run} />
            ))}
          </ul>
        </details>
      ) : null}
    </DashboardSection>
  );
}

interface QueueRowProps {
  entry: MergeQueueEntry;
  now: Date;
  busy: "retry" | "withdraw" | undefined;
  onAct: (entry: MergeQueueEntry, action: "retry" | "withdraw") => void;
}

/** One entry: its name opens the PR; the head can be withdrawn, a stopped entry retried. */
function QueueRow({ entry, now, busy, onAct }: QueueRowProps) {
  return (
    <li className="wsd-row wsd-mq-row">
      <a href={entry.pr_url} target="_blank" rel="noreferrer" className="wsd-row-link">
        <span className="wsd-row-title">{entry.ticket_title || entry.source_branch}</span>
        <span className="wsd-id">
          #{entry.pr_number} {entry.ticket_external_id}
        </span>
      </a>
      <span className="wsd-row-aside">
        <span className={`wsd-tag wsd-tag--mq-${entry.state}`}>{STATE_LABELS[entry.state]}</span>
        {entry.wait && !isFinished(entry.state) ? (
          <span className="wsd-row-where">{WAIT_LABELS[entry.wait]}</span>
        ) : null}
        <span className="wsd-row-where">{formatRelativeAge(entry.state_changed_at, now) || "just now"}</span>
        {entry.can_withdraw && entry.is_head ? (
          <Button
            variant="secondary"
            compact
            disabled={busy !== undefined}
            aria-busy={busy === "withdraw"}
            onClick={() => onAct(entry, "withdraw")}
            aria-label={`Withdraw PR #${entry.pr_number} from the merge queue`}
          >
            {busy === "withdraw" ? "Withdrawing…" : "Withdraw"}
          </Button>
        ) : null}
        {entry.can_retry ? (
          <Button
            variant="secondary"
            compact
            disabled={busy !== undefined}
            aria-busy={busy === "retry"}
            onClick={() => onAct(entry, "retry")}
            aria-label={`Retry PR #${entry.pr_number} in the merge queue`}
          >
            {busy === "retry" ? "Retrying…" : "Retry"}
          </Button>
        ) : null}
      </span>
      {entry.is_head && entry.state === "testing" ? <HeadChecks entryId={entry.id} /> : null}
      {isStopped(entry.state) ? <StoppedReason entry={entry} /> : null}
    </li>
  );
}

function isFinished(state: MergeQueueState): boolean {
  return state === "merged" || isStopped(state);
}

function isStopped(state: MergeQueueState): boolean {
  return state === "failed" || state === "timed_out" || state === "withdrawn";
}

/** The required checks the head is waiting on, read from GitHub. */
function HeadChecks({ entryId }: { entryId: string }) {
  const detail = useQuery({
    queryKey: mergeQueueKeys.entry(entryId),
    queryFn: () => mergeQueueApi.entry(entryId),
    refetchInterval: QUEUE_REFETCH_MS,
  });
  if (detail.isPending) return <p className="wsd-row-note">Reading its checks from GitHub…</p>;
  if (detail.isError) {
    return (
      <p className="wsd-row-note wsd-danger" role="alert">
        Could not read its checks: {describeError(detail.error)}
      </p>
    );
  }
  const waiting = detail.data.checks.filter((check) => check.outcome !== "passing" && check.outcome !== "skipped");
  if (waiting.length === 0) return <p className="wsd-row-note">Every required check passed; landing next cycle.</p>;
  return (
    <p className="wsd-row-note">
      Waiting on{" "}
      {waiting.map((check, index) => (
        <span key={check.name}>
          {index > 0 ? ", " : null}
          {check.url ? (
            <a href={check.url} target="_blank" rel="noreferrer">
              {check.name}
            </a>
          ) : (
            check.name
          )}{" "}
          ({check.outcome})
        </span>
      ))}
    </p>
  );
}

/** Why a stopped entry left the queue, and the failing log tail when there is one. */
function StoppedReason({ entry }: { entry: MergeQueueEntry }) {
  return (
    <div className="wsd-mq-reason">
      <p className={entry.state === "withdrawn" ? "wsd-muted" : "wsd-danger"}>
        {entry.detail.split("\n")[0] || "No reason was recorded."}
        {entry.artifact_ref ? <> Resolve it through {entry.artifact_ref}; the entry retries once that is done.</> : null}
      </p>
      {entry.log_tail ? (
        <details>
          <summary>Failing log tail</summary>
          <pre className="wsd-mq-log">{entry.log_tail}</pre>
        </details>
      ) : null}
    </div>
  );
}
