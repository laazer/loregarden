import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { mergeQueueApi, mergeQueueKeys, type MergeQueueEntry } from "../../api/mergeQueueApi";
import { usePolledReadFailure } from "../../hooks/usePolledReadFailure";
import { ticketPath } from "../../lib/appNavigation";
import { formatRelativeAge } from "../../lib/timestamps";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { StaleReadNotice } from "../ui/StaleReadNotice";
import { HeadChecks, MergeQueueEntryActions, MergeQueueStateTag, StoppedReason } from "./MergeQueueRow";
import { QUEUE_REFETCH_MS, WAIT_LABELS, isStopped } from "./mergeQueueLabels";
import { useMergeQueueActions } from "./useMergeQueueActions";
import "./MergeQueue.css";

/** What the strip leads with, by where the entry is. */
function lead(entry: MergeQueueEntry): string {
  if (entry.state === "merged") return "Landed on main";
  if (isStopped(entry.state)) return "Left the merge queue";
  return "Landing on main";
}

/** Where a live entry stands: its place in line, or what holds it. */
function standing(entry: MergeQueueEntry, ahead: number, now: Date): string {
  if (entry.state === "merged") return formatRelativeAge(entry.state_changed_at, now) || "just now";
  if (isStopped(entry.state)) return formatRelativeAge(entry.state_changed_at, now) || "just now";
  if (entry.state === "queued") return ahead === 0 ? "next in line" : `${ahead} ahead in line`;
  return WAIT_LABELS[entry.wait] || "in progress";
}

/**
 * "Is this ticket's work landing, and what is it waiting for?"
 *
 * A tree lands as one pull request queued under its root, so every ticket in
 * the tree shows the same entry, and a child names the root it lands with.
 * Nothing renders for a tree that was never queued (most tickets, most of the
 * time) or while the first read is in flight: the strip is news, and absence
 * is the normal answer, not an empty state to explain. A failed read says so.
 */
export function TicketMergeQueueStrip({ ticketId }: { ticketId: string }) {
  const read = useQuery({
    queryKey: mergeQueueKeys.ticket(ticketId),
    queryFn: () => mergeQueueApi.forTicket(ticketId),
    refetchInterval: QUEUE_REFETCH_MS,
  });
  const entry = read.data?.entry ?? null;
  const failure = usePolledReadFailure(read, ticketId, entry !== null);
  const { busy, run } = useMergeQueueActions(entry ? [entry] : [], "the ticket page");

  if (failure.blocking) {
    return (
      <div className="mq-strip mq-strip--error" role="alert">
        <span>
          Could not read where this ticket stands in the merge queue: {describeError(failure.blocking.error)}
        </span>
        <Button variant="secondary" compact disabled={read.isFetching} onClick={() => void read.refetch()}>
          {read.isFetching ? "Retrying…" : "Retry"}
        </Button>
      </div>
    );
  }
  if (!read.data || !entry) return null;
  // Re-read every poll, which is as fresh as an age on this strip needs to be.
  const now = new Date();
  const { root_ticket_id: rootId, root_external_id: rootExternalId, ahead } = read.data;

  return (
    <section className={`mq-strip mq-strip--${entry.state}`} aria-label="Merge queue">
      <div className="mq-strip-line">
        <span className="mq-strip-lead">{lead(entry)}</span>
        <MergeQueueStateTag entry={entry} />
        <span className="mq-where">{standing(entry, ahead, now)}</span>
        <a className="mq-id" href={entry.pr_url} target="_blank" rel="noreferrer">
          PR #{entry.pr_number}
        </a>
        {rootId !== ticketId ? (
          <span className="mq-where">
            with its tree <Link to={ticketPath(rootId)}>{rootExternalId}</Link>
          </span>
        ) : null}
        <span className="mq-strip-actions">
          <MergeQueueEntryActions entry={entry} busy={busy.get(entry.id)} onAct={run} />
        </span>
      </div>
      {entry.is_head && entry.state === "testing" ? <HeadChecks entryId={entry.id} /> : null}
      {isStopped(entry.state) ? <StoppedReason entry={entry} /> : null}
      {failure.stale ? (
        <StaleReadNotice
          lead="This may be out of date — the last re-read of the merge queue failed."
          error={failure.stale.error}
          fallback="The server did not answer."
          testId="ticket-merge-queue-refresh-failed"
        />
      ) : null}
    </section>
  );
}
