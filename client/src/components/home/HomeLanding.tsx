import { Link } from "react-router-dom";

import { usePolledReadFailure } from "../../hooks/usePolledReadFailure";
import { machinePath } from "../../lib/appNavigation";
import { describeError } from "../../state/toastStore";
import { MergeQueueRow } from "../mergeQueue/MergeQueueRow";
import { landedSince, liveEntries, stoppedEntries, useMergeQueueOverview } from "../mergeQueue/useMergeQueueOverview";
import { useMergeQueueActions } from "../mergeQueue/useMergeQueueActions";
import { PaneSkeleton } from "../ui/PaneSkeleton";

/** Home shows the first few; the Lanes page shows them all. */
const SHOWN = 5;
const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Home's landing question: "what is about to reach main, and is anything stuck?"
 *
 * Every workspace's entries still in line, then the ones that failed or timed
 * out (each of those also files an inbox card). A ticket's entry opens the
 * ticket; its number opens the PR. Usually empty or one deep, so the empty
 * state says what did land, which is the reassurance the operator came for.
 */
export function HomeLanding({ now, onOpenTicket }: { now: Date; onOpenTicket: (ticketId: string) => void }) {
  const overview = useMergeQueueOverview();
  const queues = overview.data?.queues ?? [];
  const rows = [...liveEntries(queues), ...stoppedEntries(queues)];
  const inLine = liveEntries(queues).length;
  const landed = landedSince(queues, new Date(now.getTime() - DAY_MS)).length;
  const actions = useMergeQueueActions(rows, "Home");
  const failure = usePolledReadFailure(overview, "merge-queue-overview", rows.length > 0);

  let body;
  if (failure.blocking) {
    body = (
      <p className="home-empty home-empty--error" role="status">
        Couldn&apos;t read the merge queue: {describeError(failure.blocking.error)}. Retrying.
      </p>
    );
  } else if (overview.isPending) {
    body = <PaneSkeleton variant="list" rows={1} label="Loading the merge queue…" />;
  } else if (rows.length === 0) {
    body = (
      <p className="home-empty">
        Nothing is landing on main right now; {landed === 1 ? "1 pull request" : `${landed} pull requests`} landed
        through the queue in the last 24 hours.
      </p>
    );
  } else {
    body = (
      <>
        <ul className="mq-rows">
          {rows.slice(0, SHOWN).map((entry) => (
            <MergeQueueRow
              key={entry.id}
              entry={entry}
              now={now}
              busy={actions.busy.get(entry.id)}
              onAct={actions.run}
              onOpenTicket={onOpenTicket}
            />
          ))}
        </ul>
        {rows.length > SHOWN ? (
          <Link className="home-section-more" to={`${machinePath("lanes")}#merge-queue`}>
            {rows.length - SHOWN} more in the merge queue
          </Link>
        ) : null}
      </>
    );
  }

  return (
    <section className="home-section" aria-labelledby="home-landing">
      <div className="home-section-head">
        <h2 id="home-landing">Landing on main</h2>
        <span className="home-section-count">{overview.data ? `${inLine} in line` : "…"}</span>
        <Link className="home-section-link" to={`${machinePath("lanes")}#merge-queue`}>
          Merge queue →
        </Link>
      </div>
      {failure.stale ? (
        <p className="home-empty home-empty--error" role="status">
          This may be out of date: the last re-read failed ({describeError(failure.stale.error)}). Retrying.
        </p>
      ) : null}
      {body}
    </section>
  );
}
