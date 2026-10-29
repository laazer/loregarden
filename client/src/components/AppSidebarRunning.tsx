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

import { Link } from "react-router-dom";

import { duration } from "../lib/duration";
import { ticketPath } from "../lib/appNavigation";
import { useTicketIdFromPath } from "../lib/useAppNavigation";
import { LIVE_RUN_STATUSES, runningTickets } from "../lib/runningTickets";
import { runStatusLabel, ticketStateColor } from "../lib/ticketStates";
import { useQueueStatus } from "../state/QueueStatusContext";
import { blurOnClick } from "./appSidebarBlur";

const RING_R = 7;
const RING_C = 2 * Math.PI * RING_R;

/**
 * How far through its stages a running ticket is: the ring fills with the
 * stages settled, and a short arc turns around it while the run is live. A
 * ticket with no workflow yet shows the empty track, not an invented fraction.
 */
function StageRing({
  done,
  total,
  live,
  color,
}: {
  done: number | null;
  total: number | null;
  live: boolean;
  color: string;
}) {
  const fraction = total ? Math.min(done ?? 0, total) / total : 0;
  return (
    <svg className={`app-sidebar-ring${live ? " is-live" : ""}`} width="18" height="18" viewBox="0 0 18 18" aria-hidden>
      <circle className="app-sidebar-ring-track" cx="9" cy="9" r={RING_R} fill="none" strokeWidth="2.2" />
      {fraction > 0 ? (
        <circle
          cx="9"
          cy="9"
          r={RING_R}
          fill="none"
          stroke={color}
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeDasharray={`${fraction * RING_C} ${RING_C}`}
          transform="rotate(-90 9 9)"
        />
      ) : null}
      {live ? (
        <circle
          className="app-sidebar-ring-spin"
          cx="9"
          cy="9"
          r={RING_R}
          fill="none"
          stroke={color}
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeDasharray={`${RING_C * 0.12} ${RING_C}`}
          opacity="0.55"
        />
      ) : null}
    </svg>
  );
}

export function SidebarRunningSection({ headingId }: { headingId: string }) {
  const { activeRuns, error, loading } = useQueueStatus();
  const running = runningTickets(activeRuns);
  const currentTicketId = useTicketIdFromPath();

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
              <StageRing
                done={run.stages_done ?? null}
                total={run.stages_total ?? null}
                live={live}
                color={ticketStateColor(run.ticket_state ?? "in_progress")}
              />
            </span>
            <span className="app-sidebar-name app-sidebar-reveal">
              {name}
              {run.stages_total ? (
                <span className="app-sidebar-sr">
                  , {run.stages_done ?? 0} of {run.stages_total} stages done
                </span>
              ) : null}
            </span>
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
