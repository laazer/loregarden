import type { Approval } from "../../api/client";
import { useApprovalResolution } from "../../hooks/useApprovalResolution";
import { formatApprovalResolveError } from "../../utils/approvalErrors";
import { PendingApprovalsSection } from "../PendingApprovalsSection";

/**
 * An agent's pending questions and permission asks, as the trailing turn of a
 * chat thread.
 *
 * They arrive as approvals rather than messages, so every chat surface has to
 * draw them itself; this is that drawing, with its resolution wired, so a new
 * chat pane cannot forget the ask and leave a turn waiting on nobody.
 */
export function ChatApprovalsAsk({
  approvals,
  ticketId,
}: {
  approvals: Approval[];
  /** The ticket the conversation belongs to, when it has one. */
  ticketId?: string;
}) {
  const resolveApproval = useApprovalResolution(ticketId);
  if (approvals.length === 0) return null;
  return (
    <PendingApprovalsSection
      variant="ask"
      approvals={approvals}
      submittingApprovalId={resolveApproval.isPending ? resolveApproval.variables?.id ?? null : null}
      submitError={resolveApproval.isError ? formatApprovalResolveError(resolveApproval.error) : null}
      onApprove={(approval, payload) => resolveApproval.mutate({ id: approval.id, action: "approve", ...payload })}
      onRecheck={(approval, payload) => resolveApproval.mutate({ id: approval.id, action: "recheck", ...payload })}
      onReject={(approval, payload) => resolveApproval.mutate({ id: approval.id, action: "reject", ...payload })}
    />
  );
}
