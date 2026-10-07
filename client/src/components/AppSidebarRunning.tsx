/**
 * The sidebar's Running section: every ticket holding an execution slot, from
 * any page, one click from its ticket view.
 *
 * It answers "what is running right now, and how long has it been going?"
 * without a trip to the Queue screen. The data is `useQueueStatus`'s — the one
 * app-wide subscription — so this adds no poller of its own.
 *
 * The section is always drawn, so runs starting and finishing never make the
 * sections above it appear or vanish. An idle queue says so in words, and a queue the app could not
 * ask says *that*, rather than both reading as an empty list.
 */

import { Link, useLocation } from "react-router-dom";

import { duration } from "../lib/duration";
import { ticketIdFromPath, ticketPath } from "../lib/appNavigation";
import { LIVE_RUN_STATUSES, runningTickets } from "../lib/runningTickets";
import { runStatusLabel, ticketStateColor } from "../lib/ticketStates";
import { useQueueStatus } from "../state/QueueStatusContext";
import { blurOnClick } from "./appSidebarBlur";

export function SidebarRunningSection({ headingId }: { headingId: string }) {
  const { pathname } = useLocation();
  const { activeRuns, error, loading } = useQueueStatus();
  const running = runningTickets(activeRuns);
  const currentTicketId = ticketIdFromPath(pathname);

  let body;
  if (running.length > 0) {
    body = running.map((run) => {
      const live = LIVE_RUN_STATUSES.has(run.status);
      const name = run.ticket_title || run.ticket_code || run.ticket_id.slice(0, 8);
      const active = currentTicketId === run.ticket_id;
      return (
        <li key={run.ticket_id} className="app-sidebar-row">
          <Link
            to={ticketPath(run.ticket_id)}
            aria-current={active ? "page" : undefined}
            className={`app-sidebar-link${active ? " app-sidebar-link--active" : ""}`}
            onClick={blurOnClick}
          >
            {active ? <span className="app-sidebar-bar" aria-hidden /> : null}
            <span className="app-sidebar-icon">
              <span
                className={`app-sidebar-run-dot${live ? " is-live" : ""}`}
                style={{ background: ticketStateColor(run.ticket_state ?? "in_progress") }}
                aria-hidden
              />
            </span>
            <span className="app-sidebar-name app-sidebar-reveal">{name}</span>
            <span className="app-sidebar-kind app-sidebar-reveal">
              {live ? duration(run.elapsed_seconds) : runStatusLabel(run.status)}
            </span>
          </Link>
        </li>
      );
    });
  } else if (error) {
    body = (
      <li className="app-sidebar-note app-sidebar-reveal" role="status">
        Couldn&apos;t reach the queue — retrying
      </li>
    );
  } else if (loading) {
    // The first snapshot lands in well under a second; a placeholder would
    // only flash.
    body = null;
  } else {
    body = (
      <li className="app-sidebar-note app-sidebar-reveal">
        Nothing running — start a ticket from the Queue
      </li>
    );
  }

  return (
    <div className="app-sidebar-section">
      <div className="app-sidebar-section-head">
        <span className="app-sidebar-section-title app-sidebar-reveal" id={headingId}>
          Running
        </span>
        {running.length > 0 ? (
          <span className="app-sidebar-count app-sidebar-reveal">{running.length}</span>
        ) : null}
      </div>
      <ul className="app-sidebar-list" aria-labelledby={headingId}>
        {body}
      </ul>
    </div>
  );
}
