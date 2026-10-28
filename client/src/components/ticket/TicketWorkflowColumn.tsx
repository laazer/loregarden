import type { TicketDetail, TicketTreeNode, WorkItemType } from "../../api/client";
import type { TicketWork } from "../../hooks/useTicketWork";
import { canHaveChildren } from "../../lib/workItemHierarchy";
import { ChildTicketsList } from "../dashboard/ChildTicketsList";
import { SubtreeAutopilotCard } from "../SubtreeAutopilotCard";
import { TicketLinksCard } from "./TicketLinksCard";
import { TicketWorkflowCard } from "./TicketWorkflowCard";

type SubTicketParent = { id: string; title: string; work_item_type: WorkItemType; workspace_slug?: string };

interface TicketWorkflowColumnProps {
  ticket: TicketDetail;
  work: TicketWork;
  expandedIds: Set<string>;
  onOpenTicket: (id: string) => void;
  onToggleChild: (id: string) => void;
  onAddChild: (parent: SubTicketParent | TicketTreeNode) => void;
}

/**
 * The ticket page's left column: a parent's children and their autopilot, or a
 * leaf ticket's Workflow card; then, for both, the tickets it is linked to.
 */
export function TicketWorkflowColumn({
  ticket,
  work,
  expandedIds,
  onOpenTicket,
  onToggleChild,
  onAddChild,
}: TicketWorkflowColumnProps) {
  if (ticket.child_count > 0 || work.children.length > 0) {
    return (
      <div>
        {canHaveChildren(ticket.work_item_type) && ticket.work_item_type !== "initiative" ? (
          // An initiative's autopilot lives on its plan page, beside the schedule.
          <SubtreeAutopilotCard ticketId={ticket.id} workItemType={ticket.work_item_type} onOpenTicket={onOpenTicket} />
        ) : null}
        <div className="state-label workflow-lifecycle-label ticket-workflow-heading">Child tickets</div>
        <ChildTicketsList
          nodes={work.children}
          selectedId={ticket.id}
          expandedIds={expandedIds}
          onSelect={onOpenTicket}
          onToggle={onToggleChild}
          onAddChild={onAddChild}
        />
        <div className="ticket-workflow-cards ticket-workflow-cards--below">
          <TicketLinksCard ticket={ticket} />
        </div>
      </div>
    );
  }

  return (
    <div className="ticket-workflow-cards">
      <TicketWorkflowCard ticket={ticket} work={work} />
      <TicketLinksCard ticket={ticket} />
    </div>
  );
}
