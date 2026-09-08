import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import { historyLines } from "../utils/ticketHistory";

interface TicketHistoryProps {
  ticketId: string;
}

/**
 * How this ticket got to where it is.
 *
 * Collapsed by default: the history is worth having and worth finding, but it is
 * not what someone opening a ticket is usually looking at. Renders nothing at
 * all when the ticket predates the transitions being recorded.
 *
 * Gate evaluations land here too (lg-workflow-integrity-684). They were recorded
 * all along — 203 of them, 43 failures — and filtered out of every reader, which
 * made the one thing worth watching on an unattended run the one thing invisible.
 */
export function TicketHistory({ ticketId }: TicketHistoryProps) {
  const [open, setOpen] = useState(false);
  const { data: events } = useQuery({
    queryKey: ["ticket-history", ticketId],
    queryFn: () => api.ticketHistory(ticketId),
    enabled: Boolean(ticketId),
  });

  if (!events?.length) return null;
  const lines = historyLines(events);

  return (
    <div className="ticket-history">
      <button type="button" className="btn-secondary btn-compact" onClick={() => setOpen(!open)}>
        {open ? "Hide history" : `History (${lines.length})`}
      </button>
      {open && (
        <ol className="ticket-history-list">
          {lines.map((line) => (
            <li key={line.id} className={`ticket-history-${line.tone}`}>
              <span className="ticket-history-when">
                {new Date(line.at).toLocaleString()}
              </span>{" "}
              {line.text}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
