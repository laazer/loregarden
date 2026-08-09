import type { TicketDetail } from "../api/client";
import { addChildActionLabel, canHaveChildren } from "../lib/workItemHierarchy";

interface WorkflowPaneTicketMetaProps {
  ticket: TicketDetail;
  onOpenParent: (parentId: string) => void;
  onAddChild: (ticket: TicketDetail) => void;
}

/** The pill row under the workflow pane's title: where the ticket sits
 * (workspace, type, parent) and how it is labelled (tags). */
export function WorkflowPaneTicketMeta({
  ticket,
  onOpenParent,
  onAddChild,
}: WorkflowPaneTicketMetaProps) {
  const parentId = ticket.parent_ticket_id;
  const tags = ticket.tags ?? [];

  return (
    <>
      <div
        style={{
          display: "flex",
          gap: 8,
          flexWrap: "wrap",
          marginBottom: 8,
          alignItems: "center",
        }}
      >
        <span className="count-pill">{ticket.workspace_slug}</span>
        <span className="count-pill">{ticket.work_item_type}</span>
        {parentId && (
          <button
            type="button"
            className="btn-secondary btn-compact"
            title="Open this item's parent"
            onClick={() => onOpenParent(parentId)}
          >
            ↑ Parent
          </button>
        )}
        {canHaveChildren(ticket.work_item_type) && (
          <button
            type="button"
            className="btn-secondary btn-compact"
            title={addChildActionLabel(ticket.work_item_type)}
            onClick={() => onAddChild(ticket)}
          >
            + Sub-item
          </button>
        )}
      </div>
      {tags.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
          {tags.map((tag) => (
            <span key={tag.toLowerCase()} className="count-pill" title="Tag">
              {tag}
            </span>
          ))}
        </div>
      )}
    </>
  );
}
