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

/**
 * Pending decisions the agent raised.
 *
 * `strip` is chrome (logs panel): a labeled band outside the transcript.
 * `ask` is chat: the same cards, but as something the agent is asking in-thread —
 * no inbox banner, no overlay styling.
 */
export function PendingApprovalsSection({
  approvals,
  ticketExternalId,
  submittingApprovalId,
  submitError,
  onApprove,
  onReject,
  variant = "strip",
}: {
  approvals: Approval[];
  ticketExternalId?: string;
  submittingApprovalId?: string | null;
  submitError?: string | null;
  onApprove: (approval: Approval, payload?: ApprovalResolvePayload) => void;
  onReject: (approval: Approval, payload?: ApprovalResolvePayload) => void;
  variant?: "strip" | "ask";
}) {
  if (approvals.length === 0) return null;

  const isAsk = variant === "ask";

  return (
    <section
      className={isAsk ? "pending-approvals pending-approvals--ask" : "pending-approvals"}
      aria-label={isAsk ? "Agent is asking" : "Needs attention"}
    >
      {isAsk ? null : <div className="state-label pending-approvals-label">Needs attention</div>}
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
