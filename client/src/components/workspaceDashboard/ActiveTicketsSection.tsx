import type { UseQueryResult } from "@tanstack/react-query";
import { useState } from "react";

import type { TicketActivity, TicketSummary } from "../../api/types";
import { Button } from "../ui/Button";
import { DashboardSection } from "./DashboardSection";
import { TicketLink } from "../TicketLink";

const ACTIVITY_ORDER: TicketActivity[] = ["running", "awaiting", "queued", "idle"];
const ACTIVITY_LABELS: Record<TicketActivity, string> = {
  running: "Running",
  awaiting: "Awaiting answer",
  queued: "Queued",
  idle: "Idle",
};

const activityOf = (ticket: TicketSummary): TicketActivity => ticket.activity ?? "idle";

interface ActiveTicketsSectionProps {
  query: UseQueryResult<TicketSummary[]>;
}

/**
 * "What is in flight, and is anything actually moving?"
 *
 * In-progress means started and unfinished, and most of that pile is idle
 * between stages. So rows sort by what is executing (running first), and the
 * activity filter lets the operator see only the ones that are moving, or
 * only the ones that are not.
 */
export function ActiveTicketsSection({ query }: ActiveTicketsSectionProps) {
  const [filter, setFilter] = useState<TicketActivity | null>(null);
  const all = query.data ?? [];
  const counts = new Map(ACTIVITY_ORDER.map((a) => [a, all.filter((t) => activityOf(t) === a).length]));
  const rows = all
    .filter((ticket) => filter === null || activityOf(ticket) === filter)
    .sort(
      (a, b) =>
        ACTIVITY_ORDER.indexOf(activityOf(a)) - ACTIVITY_ORDER.indexOf(activityOf(b)) || b.priority - a.priority,
    );

  return (
    <DashboardSection
      id="wsd-active"
      title="Active tickets"
      count={query.data ? `${all.length} in progress` : undefined}
      queries={[query]}
      errorTitle="Could not load in-progress tickets"
      empty={all.length === 0}
      emptyMessage="Nothing is in progress. Start a ticket from the Console's backlog."
      actions={
        all.length > 0 ? (
          <div className="wsd-chips" role="group" aria-label="Filter by activity">
            {ACTIVITY_ORDER.filter((a) => (counts.get(a) ?? 0) > 0).map((activity) => (
              <Button
                key={activity}
                variant="plain"
                className="wsd-chip"
                aria-pressed={filter === activity}
                onClick={() => setFilter(filter === activity ? null : activity)}
              >
                {ACTIVITY_LABELS[activity]} {counts.get(activity)}
              </Button>
            ))}
          </div>
        ) : null
      }
    >
      <div className="wsd-table-wrap">
        <table className="wsd-table">
          <caption className="visually-hidden">In-progress tickets, running first</caption>
          <thead>
            <tr>
              <th scope="col">Ticket</th>
              <th scope="col">Type</th>
              <th scope="col">Stage</th>
              <th scope="col" aria-sort="ascending">
                Activity
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((ticket) => (
              <tr key={ticket.id}>
                <td data-label="Ticket">
                  <TicketLink ticketId={ticket.id} className="wsd-row-link">
                    <span className="wsd-row-title">{ticket.title}</span>
                  </TicketLink>
                </td>
                <td data-label="Type" className="wsd-muted">
                  {ticket.work_item_type}
                </td>
                <td data-label="Stage" className="wsd-muted">
                  {ticket.workflow_stage_name || "—"}
                </td>
                <td data-label="Activity">
                  <span className={`wsd-tag wsd-tag--activity-${activityOf(ticket)}`}>
                    {ACTIVITY_LABELS[activityOf(ticket)]}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </DashboardSection>
  );
}
