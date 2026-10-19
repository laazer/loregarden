import type { PlanNode } from "../../../api/initiativeApi";
import { navigateToTicket } from "../../../lib/useAppNavigation";
import { Button } from "../../ui/Button";

/** A plan ticket by its id and title, opening the ticket. */
export function PlanTicketLink({ node }: { node: PlanNode }) {
  return (
    <Button
      variant="plain"
      className="plan-link"
      title={`Open ${node.external_id}`}
      onClick={() => navigateToTicket(node.id)}
    >
      <span className="plan-mono">{node.external_id}</span> {node.title}
    </Button>
  );
}
