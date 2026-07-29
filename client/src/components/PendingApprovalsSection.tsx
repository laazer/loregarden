import type { Approval } from "../api/client";
import { ApprovalCard, type ApprovalResolvePayload } from "./ApprovalCard";

function approvalKindLabel(kind: Approval["kind"]) {
  switch (kind) {
    case "workflow_gate":
      return "Stage sign-off";
    case "cli_permission":
      return "Agent permission";
    case "cli_question":
      return "Agent question";
    default:
      return kind;
  }
}

export function PendingApprovalsSection({
  approvals,
  ticketExternalId,
  submittingApprovalId,
  submitError,
  onApprove,
  onReject,
}: {
  approvals: Approval[];
  ticketExternalId?: string;
  submittingApprovalId?: string | null;
  submitError?: string | null;
  onApprove: (approval: Approval, payload?: ApprovalResolvePayload) => void;
  onReject: (approval: Approval, payload?: ApprovalResolvePayload) => void;
}) {
  if (approvals.length === 0) return null;

  return (
    <section className="pending-approvals">
      <div className="state-label pending-approvals-label">Needs attention</div>
      {submitError ? <div className="pending-approvals-error">{submitError}</div> : null}
      {approvals.map((approval) => (
        <div key={approval.id}>
          {approval.ticket_external_id &&
            ticketExternalId &&
            approval.ticket_external_id !== ticketExternalId && (
              <div className="pending-approvals-ticket-hint">
                {approvalKindLabel(approval.kind)} · {approval.ticket_external_id}
              </div>
            )}
          <ApprovalCard
            approval={approval}
            compact
            isSubmitting={submittingApprovalId === approval.id}
            onApprove={(payload) => onApprove(approval, payload)}
            onReject={(payload) => onReject(approval, payload)}
          />
        </div>
      ))}
    </section>
  );
}
