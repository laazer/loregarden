import type { TicketDetail } from "../api/client";
import { TicketHistory } from "./TicketHistory";
import { TicketAttention } from "./TicketAttention";
import { Button } from "./ui/Button";
import { hasParts } from "../lib/ticketParts";
import { addChildActionLabel, canHaveChildren } from "../lib/workItemHierarchy";

interface WorkflowPaneTicketMetaProps {
  ticket: TicketDetail;
  /** A failed run with stderr, or an error artifact — the Dashboard already derives it. */
  hasRunErrors: boolean;
  onAddChild: (ticket: TicketDetail) => void;
  /** The page shows the ticket's failure in full, so the attention row leaves it out. */
  failureShown?: boolean;
}

/**
 * One row under the ticket's title: where it sits and how it is labelled
 * (workspace, type, workflow, tags), then its two quiet controls, adding a
 * sub-item and its history. The parent is the breadcrumb's and the Linked
 * tickets card's, so it is not a third control here. What needs attention
 * follows on its own line.
 */
export function WorkflowPaneTicketMeta({
  ticket,
  hasRunErrors,
  onAddChild,
  failureShown = false,
}: WorkflowPaneTicketMetaProps) {
  const tags = ticket.tags ?? [];

  return (
    <>
      <div className="ticket-page-meta">
        <span className="count-pill" title="Workspace">
          {ticket.workspace_slug}
        </span>
        <span className="count-pill" title="Type">
          {ticket.work_item_type}
        </span>
        {hasParts(ticket) && ticket.child_count > 0 ? (
          <span className="count-pill ticket-page-parts-pill" title={ticket.split ? "Split into parts" : "Sub-items"}>
            {ticket.child_count} {ticket.split ? "parts" : "sub-items"}
          </span>
        ) : null}
        {ticket.workflow_template_name ? (
          <span className="count-pill" title="Workflow">
            {ticket.workflow_template_name}
          </span>
        ) : null}
        {tags.map((tag) => (
          <span key={tag.toLowerCase()} className="count-pill" title="Tag">
            {tag}
          </span>
        ))}
        <span className="ticket-page-meta-actions">
          {canHaveChildren(ticket.work_item_type) ? (
            <Button
              variant="plain"
              className="ticket-page-meta-btn"
              title={addChildActionLabel(ticket.work_item_type)}
              onClick={() => onAddChild(ticket)}
            >
              + Sub-item
            </Button>
          ) : null}
          <TicketHistory ticketId={ticket.id} />
        </span>
      </div>
      <TicketAttention ticket={ticket} hasRunErrors={hasRunErrors} failureShown={failureShown} />
    </>
  );
}
