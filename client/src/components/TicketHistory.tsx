import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";

interface TicketHistoryProps {
  ticketId: string;
}

/** Transition types, in the vocabulary the log stores them under. */
const LABELS: Record<string, string> = {
  TicketStateChanged: "State",
  StageStarted: "Stage started",
  StageCompleted: "Stage completed",
  StageSkipped: "Stage skipped",
};

function describe(event: { type: string; payload: Record<string, unknown> }): string {
  const stage = event.payload.stage_key ?? event.payload.stage;
  const to = event.payload.to ?? event.payload.state ?? event.payload.status;
  const parts = [LABELS[event.type] ?? event.type];
  if (stage) parts.push(String(stage));
  if (to) parts.push(`→ ${String(to)}`);
  return parts.join(" ");
}

/**
 * How this ticket got to where it is.
 *
 * Collapsed by default: the history is worth having and worth finding, but it is
 * not what someone opening a ticket is usually looking at. Renders nothing at
 * all when the ticket predates the transitions being recorded.
 */
export function TicketHistory({ ticketId }: TicketHistoryProps) {
  const [open, setOpen] = useState(false);
  const { data: events } = useQuery({
    queryKey: ["ticket-history", ticketId],
    queryFn: () => api.ticketHistory(ticketId),
    enabled: Boolean(ticketId),
  });

  if (!events?.length) return null;

  return (
    <div className="ticket-history">
      <button type="button" className="btn-secondary btn-compact" onClick={() => setOpen(!open)}>
        {open ? "Hide history" : `History (${events.length})`}
      </button>
      {open && (
        <ol className="ticket-history-list">
          {events.map((event) => (
            <li key={event.id}>
              <span className="ticket-history-when">
                {new Date(event.created_at).toLocaleString()}
              </span>{" "}
              {describe(event)}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
