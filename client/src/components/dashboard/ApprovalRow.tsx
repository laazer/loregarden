import type { ApprovalResolutionAction } from "../../api/chatTypes";
import type { Approval } from "../../api/client";
import { impactWithoutCriteria } from "../../utils/approvalCriteria";
import { ApprovalCard, type ApprovalResolvePayload } from "../ApprovalCard";

function approvalKindLabel(approval: Approval): string {
  if (approval.kind === "workflow_gate") return "Stage sign-off";
  if (approval.kind === "rework_pause") return "Rework paused";
  if (approval.kind === "cli_permission") return "Agent permission";
  if (approval.kind === "stage_park") return "Environment override";
  return "Agent question";
}

/**
 * The scope hint matters here: the approvals list covers the ticket's whole
 * subtree, so a card can belong to a child ticket rather than the open one.
 */
export function ApprovalRow({
  approval,
  ticketId,
  criteria,
  isSubmitting,
  onResolve,
}: {
  approval: Approval;
  ticketId: string;
  /** Shown beside the cards — what the brief and the checklist need not restate. */
  criteria: string[];
  isSubmitting: boolean;
  onResolve: (action: ApprovalResolutionAction, payload?: ApprovalResolvePayload) => void;
}) {
  const deduped = criteria.length > 0;
  return (
    <div>
      {approval.ticket_id && approval.ticket_id !== ticketId && approval.ticket_external_id && (
        <div className="approvals-view-scope-hint">
          {approvalKindLabel(approval)} · {approval.ticket_external_id}
        </div>
      )}
      <ApprovalCard
        approval={approval}
        impactText={deduped ? impactWithoutCriteria(approval.impact) : undefined}
        clampBrief
        isSubmitting={isSubmitting}
        onApprove={(payload) => onResolve("approve", payload)}
        onRecheck={(payload) => onResolve("recheck", payload)}
        onReject={(payload) => onResolve("reject", payload)}
        onIgnore={(reason) => onResolve("ignore", { response: reason })}
      />
    </div>
  );
}
