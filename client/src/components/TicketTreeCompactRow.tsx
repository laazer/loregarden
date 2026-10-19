import type { ReactNode } from "react";

import type { TicketTreeNode } from "../api/client";
import { TICKET_STATE_COLORS } from "../lib/ticketStates";
import { blockedOnStateLabel } from "../utils/blockKinds";
import { TreeExpandChevron } from "./icons/TicketTreeIcons";
import { PrioBars } from "./PrioBars";
import { Button } from "./ui/Button";
import "./TicketTreeCompactRow.css";

/**
 * One line per ticket: chevron, priority, title, state.
 *
 * The card presentation spends three lines on each ticket, which at the
 * Console's sidebar width left room for about four of over a thousand. The
 * stage and workspace stay one hover away in the row's tooltip, and one click
 * away in the Workflow pane.
 */
export function TicketTreeCompactRowBody({
  node,
  expanded,
  onToggle,
  trail,
}: {
  node: TicketTreeNode;
  expanded: boolean;
  onToggle: (id: string) => void;
  trail: ReactNode;
}) {
  const hasChildren = node.children.length > 0;
  const stateLabel = blockedOnStateLabel(node.state, node.blocked_on);
  return (
    <div className="tree-compact">
      {hasChildren ? (
        <Button
          variant="plain"
          className="tree-chevron-btn"
          aria-label={expanded ? `Collapse ${node.title}` : `Expand ${node.title}`}
          onClick={(e) => {
            e.stopPropagation();
            onToggle(node.id);
          }}
        >
          <TreeExpandChevron expanded={expanded} />
        </Button>
      ) : (
        <span className="tree-compact-spacer" aria-hidden />
      )}
      <PrioBars priority={node.priority} />
      <span className="tree-compact-title">{node.title}</span>
      <span
        className="tree-state-dot"
        style={{ background: TICKET_STATE_COLORS[node.state] }}
        aria-hidden
      />
      <span className="tree-compact-sr">
        {`P${node.priority}, ${stateLabel}`}
        {node.workflow_stage_name ? `, ${node.workflow_stage_name} ${node.workflow_stage_status}` : ""}
      </span>
      {trail}
    </div>
  );
}
