import type { TicketDependencyRef } from "../api/client";

/** One ticket at the far end of an edge — shared by the dependency and relation
 * cards so the two never drift into rendering the same reference differently. */
export function TicketRefLabel({ ticket }: { ticket: TicketDependencyRef }) {
  return (
    <span
      style={{ fontSize: 13, color: "var(--tx)" }}
      title={`${ticket.external_id} — ${ticket.state}`}
    >
      <span className="count-pill">{ticket.external_id}</span> {ticket.title}
      {ticket.is_integration_review && (
        <span className="count-pill" style={{ marginLeft: 6 }}>
          review
        </span>
      )}
    </span>
  );
}
