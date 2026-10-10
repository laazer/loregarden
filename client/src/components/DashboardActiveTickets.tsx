/**
 * Every ticket with work on it right now, across the whole machine.
 *
 * The Dashboard is built around one selected ticket, which was honest while
 * only one could run. Tickets now execute in their own worktrees and three
 * hold slots at once, so a page that shows one of them hides the other two.
 *
 * The data is the queue's own — `useQueueStatus` already subscribes once for
 * the whole app, so this is a second view of that subscription rather than a
 * second poller. The lane cards on the Queue screen stay the detailed view;
 * this is the one line the Dashboard needs to stop lying about how much is
 * running.
 */

import { useQueueStatus } from "../state/QueueStatusContext";
import { runStatusLabel, ticketStateColor } from "../lib/ticketStates";
import { duration } from "../lib/duration";
import { LIVE_RUN_STATUSES, runningTickets } from "../lib/runningTickets";
import "./DashboardActiveTickets.css";

interface Props {
  selectedTicketId?: string;
  onSelect: (ticketId: string) => void;
}

export function DashboardActiveTickets({ selectedTicketId, onSelect }: Props) {
  const { activeRuns } = useQueueStatus();

  const running = runningTickets(activeRuns);

  if (running.length === 0) return null;

  return (
    <section className="dash-active-tickets" aria-label="Tickets running now">
      <span className="dash-active-tickets__label">
        Running now
        <span className="count-pill" data-testid="active-ticket-count">
          {running.length}
        </span>
      </span>
      <ul className="dash-active-tickets__list">
        {running.map((run) => {
          const live = LIVE_RUN_STATUSES.has(run.status);
          return (
            <li key={run.ticket_id}>
              <button
                type="button"
                className={`dash-active-ticket ${
                  run.ticket_id === selectedTicketId ? "is-selected" : ""
                }`.trim()}
                onClick={() => onSelect(run.ticket_id)}
                title={run.ticket_title ?? run.ticket_code ?? run.ticket_id}
              >
                <span
                  className={`dash-active-ticket__dot ${live ? "is-live" : ""}`.trim()}
                  style={{ background: ticketStateColor(run.ticket_state ?? "in_progress") }}
                  aria-hidden
                />
                <span className="dash-active-ticket__code">
                  {run.ticket_code || run.ticket_id.slice(0, 8)}
                </span>
                <span className="dash-active-ticket__title">{run.ticket_title ?? ""}</span>
                <span className="dash-active-ticket__meta">
                  slot {run.slot_number} ·{" "}
                  {live ? duration(run.elapsed_seconds) : runStatusLabel(run.status)}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
