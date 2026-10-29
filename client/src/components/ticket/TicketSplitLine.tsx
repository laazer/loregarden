import type { TicketDetail, TicketTreeNode } from "../../api/client";
import { partStanding, partsProgress } from "../../lib/ticketParts";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";

interface TicketSplitLineProps {
  ticket: TicketDetail;
  parts: readonly TicketTreeNode[];
  loading: boolean;
  error: Error | null;
  onRetry: () => void;
}

const DATE = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

/**
 * One line under the meta row of a ticket that became several: who split it,
 * when and why, and how far along its parts are. Without it a split ticket
 * reads like any other, its split one output among the spec stage's.
 */
export function TicketSplitLine({ ticket, parts, loading, error, onRetry }: TicketSplitLineProps) {
  const split = ticket.split ?? null;
  const count = parts.length || ticket.child_count;
  const noun = split ? (count === 1 ? "part" : "parts") : count === 1 ? "sub-item" : "sub-items";
  const heading = `${split ? "Split into" : "Broken into"} ${count} ${noun}`;
  const stageName = split ? (ticket.stages.find((s) => s.key === split.stage_key)?.name ?? split.stage_key) : "";
  const progress = partsProgress(parts);
  const ready = !loading && !error && parts.length > 0;

  return (
    <div className="ticket-split-line" role="group" aria-label={heading}>
      <svg className="ticket-split-icon" width="18" height="18" viewBox="0 0 18 18" aria-hidden>
        <path
          d="M3 3v4a3 3 0 003 3h9M3 7v6a3 3 0 003 3h9M12 7l3 3-3 3"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <div className="ticket-split-what">
        <b>{heading}</b>
        {split ? ` by the orchestrator at ${stageName}, ${DATE.format(new Date(split.split_at))}` : null}
        <div className="ticket-split-why">
          {error ? (
            <>
              Couldn&apos;t load the parts: {describeError(error, "the ticket tree did not load")}.{" "}
              <Button variant="plain" className="ticket-split-retry" onClick={onRetry}>
                Retry
              </Button>
            </>
          ) : (
            <>
              {split && split.over_limits.length > 0 ? `${sentence(split.over_limits.join("; "))} ` : null}
              Each part runs on its own; this ticket finishes when they all do.
            </>
          )}
        </div>
      </div>
      <div className="ticket-split-bar" aria-hidden>
        {ready
          ? parts.map((part) => <i key={part.id} className={`is-${partStanding(part)}`} />)
          : Array.from({ length: count }, (_, i) => <i key={i} />)}
      </div>
      {ready ? (
        <span className="ticket-split-count">
          {progress.done} of {progress.total} done
        </span>
      ) : null}
    </div>
  );
}

function sentence(text: string): string {
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
}
