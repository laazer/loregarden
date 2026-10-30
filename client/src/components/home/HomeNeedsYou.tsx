import { useState } from "react";

import type { ArtifactTab } from "../../lib/appNavigation";
import type { NeedsYouRow } from "../../lib/homeNeedsYou";
import { Button } from "../ui/Button";
import { StatusChip } from "../ui/StatusChip";

/** Past this, the rest wait behind "Show all"; the first rows are the biggest piles. */
const VISIBLE_ROWS = 10;

function countLine(rows: readonly NeedsYouRow[]): string {
  const approvals = rows.reduce((n, row) => n + (row.kind === "approval" ? row.count : 0), 0);
  const ships = rows.reduce((n, row) => n + (row.kind === "ship" ? row.count : 0), 0);
  const failed = rows.reduce((n, row) => n + (row.kind === "failed" ? row.count : 0), 0);
  const people = rows.filter((row) => row.kind === "person").length;
  const parts = [
    approvals ? `${approvals} approval${approvals === 1 ? "" : "s"}` : "",
    ships ? `${ships} ship${ships === 1 ? "" : "s"}` : "",
    failed ? `${failed} failed` : "",
    people ? `${people} ticket${people === 1 ? "" : "s"}` : "",
  ].filter(Boolean);
  return parts.join(" · ");
}

/**
 * Home's first question: what is waiting on me, and where do I start?
 *
 * One row per thing to do, and the row is the action: an approval group opens
 * the inbox (on its ticket when the group has one), a failed run or a ticket
 * waiting on a person opens the ticket.
 */
export function HomeNeedsYou({
  rows,
  loading,
  error,
  status = [],
  onOpenApproval,
  onOpenTicket,
  onOpenInbox,
}: {
  rows: readonly NeedsYouRow[];
  loading: boolean;
  /** Why a source could not be read, already in words; one line each. */
  error: string[];
  /** Incomplete-but-usable notes (not the unavailable error path). */
  status?: string[];
  onOpenApproval: (ticketId: string | null) => void;
  onOpenTicket: (ticketId: string, tab?: ArtifactTab) => void;
  onOpenInbox: () => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? rows : rows.slice(0, VISIBLE_ROWS);

  return (
    <section className="home-section" aria-labelledby="home-needs-you">
      <div className="home-section-head">
        <h2 id="home-needs-you">Needs you</h2>
        <span className="home-section-count">{loading ? "…" : countLine(rows)}</span>
        <Button variant="plain" className="home-section-link" onClick={onOpenInbox}>
          Open inbox →
        </Button>
      </div>
      {error.map((line, index) => (
        // The lines are one per source, in a fixed order, so the index is the
        // stable identity; two sources that fail the same way share a message.
        <p key={`error-${index}`} className="home-empty home-empty--error" role="status">
          {line}
        </p>
      ))}
      {status.map((line, index) => (
        <p key={`status-${index}`} className="home-empty" role="status" aria-label={line}>
          {line}
        </p>
      ))}
      {!loading && rows.length === 0 && error.length === 0 && status.length === 0 ? (
        <p className="home-empty">
          Nothing needs you. Approvals, runs that failed today, tickets waiting on a person, and
          finished tickets whose ship is not green show up here.
        </p>
      ) : null}
      {shown.length > 0 ? (
        <ul className="home-rows">
          {shown.map((row) => (
            <li key={row.key}>
              <Button
                variant="plain"
                className="home-needs-row"
                onClick={() =>
                  row.kind === "approval"
                    ? onOpenApproval(row.ticketId)
                    : onOpenTicket(row.ticketId, row.kind === "ship" ? "pr" : undefined)
                }
              >
                <StatusChip {...row.chip} />
                <span className="home-needs-title">
                  {row.title}
                  {row.kind !== "person" && row.count > 1 ? (
                    <span className="home-needs-count">×{row.count}</span>
                  ) : null}
                </span>
                <span className="home-needs-where">{row.where}</span>
                <span className="home-needs-act" aria-hidden="true">
                  {row.kind === "approval"
                    ? row.count > 1
                      ? `Review ${row.count}`
                      : "Review"
                    : "Open"}
                </span>
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
      {rows.length > VISIBLE_ROWS ? (
        <Button
          variant="plain"
          className="home-section-more"
          aria-expanded={showAll}
          onClick={() => setShowAll(!showAll)}
        >
          {showAll ? "Show fewer" : `Show all ${rows.length}`}
        </Button>
      ) : null}
    </section>
  );
}
