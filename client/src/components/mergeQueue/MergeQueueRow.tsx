import { useQuery } from "@tanstack/react-query";

import { mergeQueueApi, mergeQueueKeys, type MergeQueueEntry } from "../../api/mergeQueueApi";
import { usePolledReadFailure } from "../../hooks/usePolledReadFailure";
import { formatRelativeAge } from "../../lib/timestamps";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { StaleReadNotice } from "../ui/StaleReadNotice";
import type { QueueAction } from "./useMergeQueueActions";
import { QUEUE_REFETCH_MS, STATE_LABELS, WAIT_LABELS, entryName, isFinished, isStopped } from "./mergeQueueLabels";
import "./MergeQueue.css";

interface MergeQueueRowProps {
  entry: MergeQueueEntry;
  now: Date;
  busy: QueueAction | undefined;
  onAct: (entry: MergeQueueEntry, action: QueueAction) => void;
  /**
   * When given, a ticket's entry opens the ticket from its name and its PR from
   * the number beside it; without it (or for a PR with no ticket) the name
   * opens the PR.
   */
  onOpenTicket?: (ticketId: string) => void;
  /**
   * Read the head's checks from GitHub every poll (true), or show what the loop
   * recorded on its last cycle (false). Surfaces that are open all the time and
   * span every workspace (Home, Lanes) use the record, so they never ask GitHub.
   */
  liveChecks?: boolean;
}

/** One entry: its name opens it; the head can be withdrawn, a stopped entry retried. */
export function MergeQueueRow({ entry, now, busy, onAct, onOpenTicket, liveChecks = true }: MergeQueueRowProps) {
  const ticketId = entry.ticket_id;
  const opensTicket = onOpenTicket !== undefined && ticketId !== null;
  return (
    <li className="mq-row">
      {opensTicket ? (
        <span className="mq-row-link">
          <Button variant="plain" className="mq-row-title mq-row-title--button" onClick={() => onOpenTicket(ticketId)}>
            {entryName(entry)}
          </Button>
          <a className="mq-id" href={entry.pr_url} target="_blank" rel="noreferrer">
            #{entry.pr_number}
          </a>
          <span className="mq-id">{entry.ticket_external_id}</span>
        </span>
      ) : (
        <a href={entry.pr_url} target="_blank" rel="noreferrer" className="mq-row-link">
          <span className="mq-row-title">{entryName(entry)}</span>
          <span className="mq-id">
            #{entry.pr_number} {entry.ticket_external_id}
          </span>
        </a>
      )}
      <span className="mq-row-aside">
        <MergeQueueStateTag entry={entry} />
        {entry.wait && !isFinished(entry.state) ? <span className="mq-where">{WAIT_LABELS[entry.wait]}</span> : null}
        <span className="mq-where">{formatRelativeAge(entry.state_changed_at, now) || "just now"}</span>
        <MergeQueueEntryActions entry={entry} busy={busy} onAct={onAct} />
      </span>
      {entry.is_head && entry.state === "testing" ? <HeadChecks entryId={entry.id} live={liveChecks} /> : null}
      {isStopped(entry.state) ? <StoppedReason entry={entry} /> : null}
    </li>
  );
}

export function MergeQueueStateTag({ entry }: { entry: MergeQueueEntry }) {
  return <span className={`mq-tag mq-tag--${entry.state}`}>{STATE_LABELS[entry.state]}</span>;
}

/** Withdraw while it is still safe (the head, or anything queued); retry once it stopped. */
export function MergeQueueEntryActions({
  entry,
  busy,
  onAct,
}: {
  entry: MergeQueueEntry;
  busy: QueueAction | undefined;
  onAct: (entry: MergeQueueEntry, action: QueueAction) => void;
}) {
  return (
    <>
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
    </>
  );
}

/** The required checks the head is waiting on: from GitHub when `live`, else as the loop last recorded them. */
export function HeadChecks({ entryId, live = true }: { entryId: string; live?: boolean }) {
  const detail = useQuery({
    queryKey: mergeQueueKeys.entry(entryId, live),
    queryFn: () => mergeQueueApi.entry(entryId, live),
    refetchInterval: QUEUE_REFETCH_MS,
  });
  // This query sets no `staleTime`, `retry` or `placeholderData`, and
  // `createQueryClient()` sets no query `defaultOptions`, so a failed RE-READ
  // keeps `data` and sets `status: "error"`. Guarded on the read's own checks,
  // a failure over checks already on screen keeps them and says so below;
  // guarded on `isError` it threw away the one-shot content this row exists to
  // show, for a read that is about to be attempted again anyway.
  const failure = usePolledReadFailure(detail, entryId, (detail.data?.checks.length ?? 0) > 0);

  if (detail.isPending) return <p className="mq-note">Reading its checks from GitHub…</p>;
  if (failure.blocking) {
    return (
      <p className="mq-note mq-danger" role="alert">
        Could not read its checks: {describeError(failure.blocking.error)}
      </p>
    );
  }
  // `?? []` because the branch above no longer narrows `data` for TypeScript.
  // "Every required check passed" must not render over a failing read, and the
  // branch above is what stops it: zero checks read, then a failure, has
  // nothing to protect and takes the pane.
  const checks = detail.data?.checks ?? [];
  const waiting = checks.filter((check) => check.outcome !== "passing" && check.outcome !== "skipped");
  return (
    <>
      {waiting.length === 0 ? (
        <p className="mq-note">
          {checks.length === 0 && !live
            ? "Its checks have not been recorded yet; the next cycle reads them."
            : "Every required check passed; landing next cycle."}
        </p>
      ) : (
        <p className="mq-note">
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
      )}
      {failure.stale && (
        <StaleReadNotice
          lead="These checks may be out of date — the last re-read failed."
          error={failure.stale.error}
          fallback="GitHub did not answer."
          testId="head-checks-refresh-failed"
        />
      )}
    </>
  );
}

/** Why a stopped entry left the queue, and the failing log tail when there is one. */
export function StoppedReason({ entry }: { entry: MergeQueueEntry }) {
  return (
    <div className="mq-reason">
      <p className={entry.state === "withdrawn" ? "mq-muted" : "mq-danger"}>
        {entry.detail.split("\n")[0] || "No reason was recorded."}
        {entry.artifact_ref ? <> Resolve it through {entry.artifact_ref}; the entry retries once that is done.</> : null}
      </p>
      {entry.log_tail ? (
        <details>
          <summary>Failing log tail</summary>
          <pre className="mq-log scroll-focus-ring" role="group" aria-label="Failing log tail" tabIndex={0}>
            {entry.log_tail}
          </pre>
        </details>
      ) : null}
    </div>
  );
}
