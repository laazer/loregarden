import { useEffect, useRef } from "react";
import { Link, useLocation } from "react-router-dom";

import type { MergeQueueView } from "../../api/mergeQueueApi";
import { workspaceDashboardPath } from "../../lib/appNavigation";
import { usePolledReadFailure } from "../../hooks/usePolledReadFailure";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import { StaleReadNotice } from "../ui/StaleReadNotice";
import { MergeQueueRow } from "./MergeQueueRow";
import { isFinished } from "./mergeQueueLabels";
import { liveEntries, useMergeQueueOverview } from "./useMergeQueueOverview";
import { useMergeQueueActions } from "./useMergeQueueActions";
import "./MergeQueue.css";

/**
 * The Lanes page's second question: "what is waiting to land on main, in every
 * workspace, and what is each one waiting for?"
 *
 * Lanes are what waits for the machine; this is what waits for main. One group
 * per workspace whose queue has held anything, its live and stopped entries
 * first and its recent landings folded. A ticket's entry opens the ticket; any
 * entry's number opens its pull request.
 */
export function MergeQueueOverviewSection({ onOpenTicket }: { onOpenTicket: (ticketId: string) => void }) {
  const overview = useMergeQueueOverview();
  const queues = overview.data?.queues ?? [];
  const entries = queues.flatMap((queue) => queue.entries);
  const live = liveEntries(queues);
  const actions = useMergeQueueActions(entries, "the Lanes page");
  const failure = usePolledReadFailure(overview, "merge-queue-overview", entries.length > 0);
  const now = new Date();

  // Home links here as `#merge-queue`; the router does not scroll to a hash.
  const { hash } = useLocation();
  const sectionRef = useRef<HTMLElement>(null);
  const loaded = overview.data !== undefined;
  useEffect(() => {
    if (hash === "#merge-queue" && loaded) sectionRef.current?.scrollIntoView({ block: "start" });
  }, [hash, loaded]);

  let body;
  if (failure.blocking) {
    body = (
      <div className="mq-section-error" role="alert">
        <p>Could not read the merge queue: {describeError(failure.blocking.error)}</p>
        <Button variant="secondary" compact disabled={overview.isFetching} onClick={() => void overview.refetch()}>
          {overview.isFetching ? "Retrying…" : "Retry"}
        </Button>
      </div>
    );
  } else if (overview.isPending) {
    body = <PaneSkeleton variant="list" rows={2} label="Loading the merge queue…" />;
  } else if (entries.length === 0) {
    body = (
      <p className="mq-section-empty">
        Nothing has been through the merge queue yet. A tree joins it when its pull request is published with
        auto-merge on, and any other pull request when an agent calls <code>loregarden_enqueue_merge</code> with its
        number.
      </p>
    );
  } else {
    body = queues.map((queue) => (
      <WorkspaceQueue key={queue.workspace_slug} queue={queue} now={now} actions={actions} onOpenTicket={onOpenTicket} />
    ));
  }

  return (
    <section id="merge-queue" ref={sectionRef} className="mq-section" aria-labelledby="mq-section-heading">
      <header className="mq-section-header">
        <h2 id="mq-section-heading">
          Merge queue
          {overview.data ? <span className="mq-section-count"> {live.length} in line</span> : null}
        </h2>
      </header>
      {failure.stale ? (
        <StaleReadNotice
          lead="This may be out of date — the last re-read of the merge queue failed."
          error={failure.stale.error}
          fallback="The server did not answer."
          testId="merge-queue-overview-refresh-failed"
        />
      ) : null}
      {body}
    </section>
  );
}

function WorkspaceQueue({
  queue,
  now,
  actions,
  onOpenTicket,
}: {
  queue: MergeQueueView;
  now: Date;
  actions: ReturnType<typeof useMergeQueueActions>;
  onOpenTicket: (ticketId: string) => void;
}) {
  const current = queue.entries.filter((entry) => entry.state !== "merged");
  const landed = queue.entries.filter((entry) => entry.state === "merged");
  const inLine = queue.entries.filter((entry) => !isFinished(entry.state)).length;
  const headingId = `mq-ws-${queue.workspace_slug}`;
  return (
    <div className="mq-workspace" role="group" aria-labelledby={headingId}>
      <h3 id={headingId} className="mq-workspace-name">
        <Link to={workspaceDashboardPath(queue.workspace_slug)}>{queue.workspace_slug}</Link>
        <span className="mq-section-count"> {inLine} in line</span>
      </h3>
      {current.length > 0 ? (
        <ul className="mq-rows">
          {current.map((entry) => (
            <MergeQueueRow
              key={entry.id}
              entry={entry}
              now={now}
              busy={actions.busy.get(entry.id)}
              onAct={actions.run}
              onOpenTicket={onOpenTicket}
              liveChecks={false}
            />
          ))}
        </ul>
      ) : (
        <p className="mq-section-empty">Nothing in line.</p>
      )}
      {landed.length > 0 ? (
        <details className="mq-landed">
          <summary>Landed recently ({landed.length})</summary>
          <ul className="mq-rows">
            {landed.map((entry) => (
              <MergeQueueRow
                key={entry.id}
                entry={entry}
                now={now}
                busy={undefined}
                onAct={actions.run}
                onOpenTicket={onOpenTicket}
              />
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
