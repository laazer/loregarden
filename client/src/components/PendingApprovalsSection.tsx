import type { Approval } from "../api/client";
import { ApprovalCard } from "./ApprovalCard";

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
  isSubmitting,
  onApprove,
  onReject,
}: {
  approvals: Approval[];
  ticketExternalId?: string;
  isSubmitting: boolean;
  onApprove: (approval: Approval, payload?: { answers?: Record<string, string | string[]>; response?: string }) => void;
  onReject: (approval: Approval) => void;
}) {
  if (approvals.length === 0) return null;

  return (
    <section
      style={{
        padding: "12px 16px",
        borderTop: "1px solid var(--bd)",
        background: "rgba(96,165,250,.04)",
        maxHeight: 280,
        overflowY: "auto",
      }}
    >
      <div className="state-label" style={{ marginBottom: 10 }}>
        Needs attention
      </div>
      {approvals.map((approval) => (
        <div key={approval.id}>
          {approval.ticket_external_id &&
            ticketExternalId &&
            approval.ticket_external_id !== ticketExternalId && (
              <div style={{ fontSize: 10.5, color: "var(--txl)", marginBottom: 4 }}>
                {approvalKindLabel(approval.kind)} · {approval.ticket_external_id}
              </div>
            )}
          <ApprovalCard
            approval={approval}
            compact
            isSubmitting={isSubmitting}
            onApprove={(payload) => onApprove(approval, payload)}
            onReject={() => onReject(approval)}
          />
        </div>
      ))}
    </section>
  );
}
