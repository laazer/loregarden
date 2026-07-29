import type { TicketSummary, TicketTreeNode } from "../../../api/types";

/** Child listings arrive flat; the v6 tree row needs a node. Grandchildren stay
 *  unexpanded — the count pill already reports them. */
export function toTicketTreeNode(ticket: TicketSummary): TicketTreeNode {
  return {
    id: ticket.id,
    external_id: ticket.external_id,
    title: ticket.title,
    state: ticket.state,
    priority: ticket.priority,
    work_item_type: ticket.work_item_type,
    workspace_slug: ticket.workspace_slug,
    workflow_stage_name: ticket.workflow_stage_name,
    workflow_stage_status: ticket.workflow_stage_status,
    child_count: ticket.child_count,
    children: [],
  };
}
