import { Link } from "react-router-dom";

import type { TicketSummary } from "../../api/types";
import { duration } from "../../lib/duration";
import type { ActiveRun } from "../../lib/queueSocket";
import { Button } from "../ui/Button";
import { StatusChip } from "../ui/StatusChip";

/**
 * Home's second question: what is moving right now, and how far along is it?
 *
 * One card per ticket holding a lane (a handful at most: the lane count caps
 * it), with its stages as a strip, so "5 of 12" is visible at a glance. The card
 * opens the ticket. The stages come from the ticket list Home already reads, so
 * a run whose ticket is not in it (another workspace's) shows without a strip.
 */
export function HomeRunningNow({
  runs,
  ticketsById,
  lanes,
  error,
  onOpenTicket,
}: {
  runs: readonly ActiveRun[];
  ticketsById: ReadonlyMap<string, TicketSummary>;
  /** Busy and total lanes, or null before the queue's first snapshot. */
  lanes: { busy: number; max: number } | null;
  error: string | null;
  onOpenTicket: (ticketId: string) => void;
}) {
  return (
    <section className="home-section" aria-labelledby="home-running-now">
      <div className="home-section-head">
        <h2 id="home-running-now">Running now</h2>
        <span className="home-section-count">
          {lanes ? `${lanes.busy} of ${lanes.max} lanes` : "…"}
        </span>
        <Link className="home-section-link" to="/machine/lanes">
          Lanes →
        </Link>
      </div>
      {error ? (
        <p className="home-empty home-empty--error" role="status">
          Couldn&apos;t reach the queue: {error}. Retrying.
        </p>
      ) : runs.length === 0 && lanes ? (
        <p className="home-empty">
          Nothing running; {lanes.max - lanes.busy} lanes free. Start a ticket from the Queue.
        </p>
      ) : (
        <ul className="home-run-cards">
          {runs.map((run) => {
            const ticket = ticketsById.get(run.ticket_id);
            const stages = ticket?.stages ?? [];
            const current = stages.find((stage) => stage.status === "running");
            const done = stages.filter((stage) => stage.status === "done").length;
            const name = run.ticket_title || ticket?.title || run.ticket_code || "A ticket";
            return (
              <li key={run.ticket_id}>
                <Button
                  variant="plain"
                  className="home-run-card"
                  onClick={() => onOpenTicket(run.ticket_id)}
                >
                  <span className="home-run-card-top">
                    <span className="home-run-card-id">
                      {ticket?.external_id || run.ticket_code}
                    </span>
                    <StatusChip tone="run" label={current?.name || run.status} />
                  </span>
                  <span className="home-run-card-title">{name}</span>
                  {stages.length ? (
                    <span className="home-run-stages" aria-hidden="true">
                      {stages.map((stage) => (
                        <i key={stage.key} data-status={stage.status} />
                      ))}
                    </span>
                  ) : null}
                  <span className="home-run-card-meta">
                    {stages.length ? `${done} of ${stages.length} stages · ` : ""}
                    {duration(run.elapsed_seconds)}
                  </span>
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
