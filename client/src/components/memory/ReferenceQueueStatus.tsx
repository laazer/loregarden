import { Link } from "react-router-dom";

import type { DiscoveryView, QueueSchedule } from "../../api/referenceLibraryApi";

/** A run is over once its ticket is settled. */
const SETTLED = new Set(["done", "wont_do"]);
import { memoryPath, ticketPath } from "../../lib/appNavigation";
import { nextCheckText } from "../../lib/referenceLibrary";
import { ReviewNowButton } from "./ReferenceReviewNow";

/**
 * "What gets reviewed next, and when?" Over the whole queue, not this view:
 * what is being read now, the next source in line, and either when the next
 * check will start it or what is holding it.
 */
export function ReferenceQueueStatus({
  schedule,
  discoveries,
  search,
}: {
  schedule: QueueSchedule;
  discoveries: DiscoveryView[];
  search: string;
}) {
  const { next, running } = schedule;
  const queued = Object.keys(schedule.positions).length;
  const searching = discoveries.filter((run) => !SETTLED.has(run.carrier_state ?? ""));
  return (
    <div className="rl-queue-status" role="status" aria-label="Review queue">
      {searching.map((run) => (
        <p key={run.id}>
          <span className="rl-queue-label">Finding</span> {run.label}
          {run.focus ? ` (${run.focus})` : ""}: {run.found} of up to {run.wanted} found so far
          {run.carrier ? (
            <>
              {" "}
              by <Link to={ticketPath(run.carrier)}>{run.carrier}</Link>
            </>
          ) : null}
          . Keep or drop each in Queue.
        </p>
      ))}
      {running.map((pass) => (
        <p key={`${pass.source_id}-${pass.ordinal}`}>
          <span className="rl-queue-label">Reading now</span>{" "}
          <Link to={{ pathname: memoryPath("library", pass.source_id), search }}>{pass.name}</Link>, pass {pass.ordinal}
          {pass.carrier ? (
            <>
              {" "}
              as <Link to={ticketPath(pass.carrier)}>{pass.carrier}</Link>
            </>
          ) : null}
        </p>
      ))}
      {next ? (
        <div className="rl-queue-next">
          <p>
            <span className="rl-queue-label">Next review</span>{" "}
            <Link to={{ pathname: memoryPath("library", next.source_id), search }}>{next.name}</Link>, pass {next.ordinal}
            {queued > 1 ? ` (${queued - 1} more after it)` : ""}
            {next.waiting ? (
              <span className="rl-queue-held"> — held: {next.waiting.replace(/`/g, "")}.</span>
            ) : (
              <span> — starts at the next check, {nextCheckText(schedule)}.</span>
            )}
          </p>
          {next.can_start_now ? <ReviewNowButton sourceId={next.source_id} name={next.name} variant="secondary" /> : null}
        </div>
      ) : (
        <p>
          <span className="rl-queue-label">Next review</span>{" "}
          Nothing is queued. Add a reference, or open a surveyed source to review it again.
          {schedule.enabled ? null : <span className="rl-queue-held"> Note: {schedule.disabled_reason}.</span>}
        </p>
      )}
    </div>
  );
}
