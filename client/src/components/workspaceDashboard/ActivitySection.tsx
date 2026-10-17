import type { UseQueryResult } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import {
  ACTIVITY_KINDS,
  type ActivityHighlight,
  type ActivityHighlightKind,
  type WorkspaceActivity,
} from "../../api/workspaceDashboardApi";
import { ticketPath } from "../../lib/appNavigation";
import { CATCH_UP_LABELS, CATCH_UP_SPANS, type CatchUpSpan, type CatchUpWindow } from "../../lib/catchUpWindow";
import { formatLocalTimestamp, formatRelativeAge } from "../../lib/timestamps";
import { highlightText, KIND_LABELS } from "../../lib/workspaceDashboard";
import { Button } from "../ui/Button";
import { Select } from "../ui/Select";
import { DashboardSection } from "./DashboardSection";

const FAILURE_KINDS: ReadonlySet<ActivityHighlightKind> = new Set([
  "landing_failed",
  "stage_rejected",
  "run_failed",
  "gate_failed",
]);

/**
 * A row's outcomes as at most two runs of text, failures first: what went
 * wrong is what a person catching up looks for, and a run per outcome made
 * every row a five-field record (the usability check's "tabular list").
 */
function outcomeParts(highlights: ActivityHighlight[], state: string): { failure: boolean; text: string }[] {
  const failures = highlights.filter((h) => FAILURE_KINDS.has(h.kind));
  const others = highlights.filter((h) => !FAILURE_KINDS.has(h.kind)).map(highlightText);
  const now = `now ${state.replaceAll("_", " ")}`;
  return [
    { failure: true, text: failures.map(highlightText).join(", ") },
    {
      failure: false,
      text: others.length ? `${others.join(", ")}; ${now}` : now,
    },
  ].filter((part) => part.text);
}

interface ActivitySectionProps {
  query: UseQueryResult<WorkspaceActivity>;
  span: CatchUpSpan;
  window: CatchUpWindow;
  /** False when this browser could not store the visit; the span then falls back. */
  remembered: boolean;
  kinds: ActivityHighlightKind[];
  onSpanChange: (span: CatchUpSpan) => void;
  onKindsChange: (kinds: ActivityHighlightKind[]) => void;
  now: Date;
}

/**
 * "What happened while I was away?"
 *
 * One row per ticket that something happened to, newest first, with its
 * highlights folded into counts. The kind chips filter the list and show
 * the whole window's totals, so "10 landing failures" is visible before
 * anyone filters for it.
 */
export function ActivitySection({
  query,
  span,
  window: catchUp,
  remembered,
  kinds,
  onSpanChange,
  onKindsChange,
  now,
}: ActivitySectionProps) {
  const data = query.data;
  const totals = data?.totals ?? {};
  const fellBack = span === "last_visit" && catchUp.span !== "last_visit";
  const toggle = (kind: ActivityHighlightKind) =>
    onKindsChange(kinds.includes(kind) ? kinds.filter((k) => k !== kind) : [...kinds, kind]);

  return (
    <DashboardSection
      id="wsd-activity"
      title="Since you were away"
      count={data ? `${data.tickets.length + data.more_tickets} tickets` : undefined}
      queries={[query]}
      errorTitle="Could not load recent activity"
      empty={(data?.tickets.length ?? 0) === 0}
      emptyMessage={
        kinds.length > 0
          ? "Nothing of those kinds in this window. Clear the filter to see everything."
          : "Nothing happened to any ticket in this window. Pick a longer one above."
      }
      actions={
        <label>
          <span className="visually-hidden">Time window</span>
          <Select value={span} onChange={(event) => onSpanChange(event.target.value as CatchUpSpan)}>
            {CATCH_UP_SPANS.map((option) => (
              <option key={option} value={option}>
                {CATCH_UP_LABELS[option]}
              </option>
            ))}
          </Select>
        </label>
      }
    >
      <p className="wsd-note">
        Since {formatLocalTimestamp(catchUp.since.toISOString())}
        {fellBack
          ? remembered
            ? " (no earlier visit recorded here, so the last 7 days)"
            : " (this browser cannot remember visits, so the last 7 days)"
          : ""}
      </p>
      <div className="wsd-chips" role="group" aria-label="Filter by what happened">
        {ACTIVITY_KINDS.filter((kind) => (totals[kind] ?? 0) > 0).map((kind) => (
          <Button
            key={kind}
            variant="plain"
            className={`wsd-chip${FAILURE_KINDS.has(kind) ? " wsd-chip--failure" : ""}`}
            aria-pressed={kinds.includes(kind)}
            onClick={() => toggle(kind)}
          >
            {KIND_LABELS[kind]} {totals[kind]}
          </Button>
        ))}
      </div>
      <ol className="wsd-timeline">
        {(data?.tickets ?? []).map((ticket) => (
          <li key={ticket.ticket_id} className="wsd-event">
            <span className="wsd-event-when">{formatRelativeAge(ticket.last_event_at, now)}</span>
            <div className="wsd-event-body">
              <Link to={ticketPath(ticket.ticket_id)} className="wsd-row-link">
                <span className="wsd-row-title">{ticket.title}</span>
              </Link>
              <span className="wsd-outcomes">
                {outcomeParts(ticket.highlights, ticket.state).map((part) => (
                  <span
                    key={part.failure ? "failure" : "other"}
                    className={part.failure ? "wsd-outcome wsd-outcome--failure" : "wsd-outcome"}
                  >
                    {part.text}
                  </span>
                ))}
              </span>
            </div>
          </li>
        ))}
      </ol>
      {data && data.more_tickets > 0 ? (
        <p className="wsd-note">
          {data.more_tickets} more tickets had activity. Narrow the window or filter by kind to see them.
        </p>
      ) : null}
      {data?.events_truncated ? (
        <p className="wsd-note">This window holds more events than one read covers; counts are from the newest.</p>
      ) : null}
    </DashboardSection>
  );
}
