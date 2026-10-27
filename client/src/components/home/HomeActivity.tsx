import { Link } from "react-router-dom";

import { runTime, type RecentRun } from "../../lib/homeNeedsYou";
import { runStatusChip } from "../../lib/ticketStates";
import { Button } from "../ui/Button";
import { StatusChip } from "../ui/StatusChip";

/** A feed is read at a glance; the rest is on the ticket. */
const FEED_ROWS = 10;

function timeLabel(at: Date | null, now: Date): string {
  if (!at) return "";
  const sameDay = at.toDateString() === now.toDateString();
  const time = at.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return sameDay ? time : `${at.toLocaleDateString(undefined, { weekday: "short" })} ${time}`;
}

/**
 * Home's third question: what just happened? Newest first, one line each, the
 * ticket one click away.
 */
export function HomeActivity({
  runs,
  now,
  loading,
  error,
  onOpenTicket,
}: {
  runs: readonly RecentRun[];
  now: Date;
  loading: boolean;
  error: string | null;
  onOpenTicket: (ticketId: string) => void;
}) {
  const shown = runs.slice(0, FEED_ROWS);
  return (
    <section className="home-section" aria-labelledby="home-recent-activity">
      <div className="home-section-head">
        <h2 id="home-recent-activity">Recent activity</h2>
        <Link className="home-section-link" to="/console">
          Console →
        </Link>
      </div>
      {error ? (
        <p className="home-empty home-empty--error" role="status">
          Couldn&apos;t load recent runs: {error}
        </p>
      ) : !loading && shown.length === 0 ? (
        <p className="home-empty">
          No runs yet. A ticket's agent runs show up here as they finish.
        </p>
      ) : (
        <ul className="home-feed">
          {shown.map((run) => {
            const at = runTime(run);
            const ticket = run.ticket_title || run.ticket_external_id || "the workspace";
            const line = (
              <>
                <time className="home-feed-time" dateTime={at?.toISOString()}>
                  {timeLabel(at, now)}
                </time>
                <StatusChip {...runStatusChip(run.status)} />
                <span className="home-feed-text">
                  <b>{run.agent_id || "agent"}</b>
                  {run.stage_key ? (
                    <span className="home-feed-stage"> · {run.stage_key}</span>
                  ) : null}
                  {" on "}
                  {ticket}
                </span>
              </>
            );
            return (
              <li key={run.id}>
                {run.ticket_id ? (
                  <Button
                    variant="plain"
                    className="home-feed-row"
                    onClick={() => onOpenTicket(run.ticket_id as string)}
                  >
                    {line}
                  </Button>
                ) : (
                  <span className="home-feed-row home-feed-row--static">{line}</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
