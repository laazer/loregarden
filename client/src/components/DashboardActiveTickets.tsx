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
import "./DashboardActiveTickets.css";

/** Statuses where an agent is still on the work, matching the queue's cards. */
const LIVE_RUN_STATUSES = new Set(["running", "awaiting_permission"]);

interface Props {
  selectedTicketId?: string;
  onSelect: (ticketId: string) => void;
}

export function DashboardActiveTickets({ selectedTicketId, onSelect }: Props) {
  const { activeRuns } = useQueueStatus();

  // One entry per ticket: a lane holds its slot across every stage, so the
  // same ticket can appear behind more than one run row.
  const byTicket = new Map<string, (typeof activeRuns)[number]>();
  for (const run of activeRuns) {
    if (!run.ticket_id) continue;
    const seen = byTicket.get(run.ticket_id);
    if (!seen || run.elapsed_seconds > seen.elapsed_seconds) byTicket.set(run.ticket_id, run);
  }
  const running = [...byTicket.values()].sort((a, b) => a.slot_number - b.slot_number);

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
