import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import type { RefusedAttempt, TicketLandingStatus } from "../../api/ticketLandingApi";
import { ticketLandingKey } from "../../api/ticketLandingApi";
import { machinePath, ticketPath } from "../../lib/appNavigation";
import { refusalReason } from "../../lib/landingStatus";
import { formatDuration } from "../../lib/queueRunFormat";
import { formatRelativeAge, parseTimestamp } from "../../lib/timestamps";
import { describeError } from "../../state/toastStore";

/** The ledger changes every couple of seconds while tests run; the elapsed clock every second. */
const POLL_MS = 3000;
const TICK_MS = 1000;

function useNow(intervalMs: number): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

function elapsedSince(since: string | null, now: Date): string {
  const start = parseTimestamp(since);
  if (!start) return "";
  return formatDuration(Math.max(0, (now.getTime() - start.getTime()) / 1000));
}

function headline(status: TicketLandingStatus): string {
  switch (status.phase) {
    case "verifying":
      return "Running the landing tests";
    case "waiting_for_capacity":
      return "Waiting for machine capacity to run the landing tests";
    case "merging":
      return `Merging onto ${status.target}`;
    case "not_landing":
      return "Finishing the landing";
  }
}

/**
 * "What is this ticket waiting on before it is marked done?" — the landing's
 * phase as the capacity ledger records it, how long it has been in it, who it
 * waits behind, and how many attempts were refused before this one.
 */
export function TicketLandingPanel({ ticketId }: { ticketId: string }) {
  const landing = useQuery({
    queryKey: ticketLandingKey(ticketId),
    queryFn: () => api.ticketLanding(ticketId),
    refetchInterval: POLL_MS,
  });
  const now = useNow(TICK_MS);

  // Loading renders nothing: the "landing on …" line above already says what
  // is happening, and the first answer arrives well inside a poll.
  if (landing.isPending) return null;
  if (landing.isError) {
    return (
      <p className="twf-landing-error" role="status">
        Could not read what this landing is waiting on: {describeError(landing.error, "the request failed")}. Trying
        again every few seconds.
      </p>
    );
  }

  const status = landing.data;
  return (
    <section className="twf-landing" aria-label="What this landing is waiting on">
      {/* Only the phase is live: the progress below changes every few seconds
          and would talk over everything else. */}
      <p className="twf-landing-head" aria-live="polite">
        {headline(status)}
      </p>
      {status.phase === "verifying" || status.phase === "waiting_for_capacity" ? (
        <p className="twf-landing-line">
          Marked done once these tests pass and the merge onto {status.target} is published.
        </p>
      ) : null}
      {status.phase === "verifying" ? <Verifying status={status} now={now} /> : null}
      {status.phase === "waiting_for_capacity" ? <Waiting status={status} now={now} /> : null}
      {status.phase === "merging" ? (
        <p className="twf-landing-line">Merging and publishing take seconds; the tests have already passed.</p>
      ) : null}
      {status.last_refusal ? (
        <Refusals ticketId={ticketId} count={status.refused_attempts} last={status.last_refusal} now={now} />
      ) : null}
    </section>
  );
}

function Verifying({ status, now }: { status: TicketLandingStatus; now: Date }) {
  const elapsed = elapsedSince(status.since, now);
  const limitMinutes = status.timeout_seconds ? Math.round(status.timeout_seconds / 60) : null;
  const total = status.progress_total;
  return (
    <>
      {elapsed ? (
        <p className="twf-landing-line twf-landing-num">
          {elapsed}
          {limitMinutes ? ` of a ${limitMinutes}m limit` : ""}
        </p>
      ) : null}
      {status.progress ? <p className="twf-landing-line twf-mono">{status.progress}</p> : null}
      {total ? (
        <progress
          className="twf-landing-bar"
          value={status.progress_done ?? 0}
          max={total}
          aria-label="Landing tests completed"
        />
      ) : null}
    </>
  );
}

function Waiting({ status, now }: { status: TicketLandingStatus; now: Date }) {
  const elapsed = elapsedSince(status.since, now);
  const hidden = status.ahead_count - status.ahead.length;
  return (
    <>
      <p className="twf-landing-line">
        {elapsed ? `In line for ${elapsed}. ` : ""}
        {status.ahead_count === 0
          ? "Nothing is ahead of it now; it starts on the next check."
          : `Behind ${status.ahead_count} ${status.ahead_count === 1 ? "job" : "jobs"}:`}
      </p>
      {status.ahead.length > 0 ? (
        <ul className="twf-landing-ahead">
          {status.ahead.map((row, index) => (
            <li key={`${row.label}-${index}`}>
              <span className="twf-mono">{row.label}</span>
              <span className="twf-set-note">
                {" "}
                · {row.held ? "running" : "waiting"}
                {row.since ? ` ${elapsedSince(row.since, now)}` : ""}
                {row.progress ? ` · ${row.progress}` : ""}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {hidden > 0 ? <p className="twf-landing-line">and {hidden} more.</p> : null}
      <Link className="twf-landing-link" to={machinePath("capacity")}>
        Open the capacity board
      </Link>
    </>
  );
}

function Refusals({
  ticketId,
  count,
  last,
  now,
}: {
  ticketId: string;
  count: number;
  last: RefusedAttempt;
  now: Date;
}) {
  const age = formatRelativeAge(last.at, now);
  return (
    <p className="twf-landing-refused">
      {count === 1 ? "The last attempt was refused" : `The last ${count} attempts were refused`}: {refusalReason(last)}
      {age ? `, ${age}` : ""}.{" "}
      <Link className="twf-landing-link" to={ticketPath(ticketId, "outputs")}>
        See the verification output
      </Link>
    </p>
  );
}
