import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Approval, type TicketDetail } from "../../api/client";
import { hasHumanCriteria, impactWithoutCriteria } from "../../utils/approvalCriteria";
import { formatApprovalResolveError } from "../../utils/approvalErrors";
import { ApprovalCard, type ApprovalResolvePayload } from "../ApprovalCard";

function kindLabel(approval: Approval): string {
  if (approval.kind === "workflow_gate") return "Stage sign-off";
  if (approval.kind === "cli_permission") return "Agent permission";
  return "Agent question";
}

/**
 * The human side of a ticket: what has to be true before sign-off, and the
 * approvals currently asking for it — at full width, so a long criteria list or
 * testing checklist is readable instead of clamped into the inbox rail.
 */
export function ApprovalsView({ ticket }: { ticket?: TicketDetail }) {
  const qc = useQueryClient();
  const ticketId = ticket?.id;

  const approvals = useQuery({
    queryKey: ["approvals", ticketId],
    queryFn: () => api.approvals(ticketId),
    refetchInterval: 5000,
    enabled: !!ticketId,
  });

  const resolveApproval = useMutation({
    meta: { errorTitle: "Resolve approval" },
    mutationFn: ({
      id,
      action,
      payload,
    }: {
      id: string;
      action: "approve" | "reject";
      payload?: ApprovalResolvePayload;
    }) => api.resolveApproval(id, { action, ...payload }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["approvals"] });
      qc.invalidateQueries({ queryKey: ["ticket"] });
    },
  });

  if (!ticket) {
    return <div style={{ padding: 40, color: "var(--txl)", textAlign: "center" }}>No ticket selected</div>;
  }

  const pending = approvals.data ?? [];
  const criteria = ticket.acceptance_criteria ?? [];
  const humanPending = pending.filter(hasHumanCriteria);
  const otherPending = pending.filter((a) => !hasHumanCriteria(a));

  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 16, minHeight: 0 }}>
      {resolveApproval.isError && (
        <div className="approvals-view-error">
          {formatApprovalResolveError(resolveApproval.error)}
        </div>
      )}

      <section>
        <div className="state-label" style={{ marginBottom: 8 }}>
          Acceptance criteria
        </div>
        {criteria.length ? (
          <ul className="approvals-view-criteria">
            {criteria.map((item, idx) => (
              <li key={idx}>{item}</li>
            ))}
          </ul>
        ) : (
          <div style={{ fontSize: 12.5, color: "var(--txm)" }}>
            No acceptance criteria recorded on this ticket.
          </div>
        )}
      </section>

      <section>
        <div className="state-label" style={{ marginBottom: 8 }}>
          Awaiting your sign-off ({humanPending.length})
        </div>
        {humanPending.length ? (
          humanPending.map((approval) => (
            <ApprovalRow
              key={approval.id}
              approval={approval}
              ticketId={ticket.id}
              dropRestatedCriteria={criteria.length > 0}
              isSubmitting={
                resolveApproval.isPending && resolveApproval.variables?.id === approval.id
              }
              onResolve={(action, payload) =>
                resolveApproval.mutate({ id: approval.id, action, payload })
              }
            />
          ))
        ) : (
          <div style={{ fontSize: 12.5, color: "var(--txm)" }}>
            {approvals.isLoading
              ? "Loading approvals…"
              : "Nothing is waiting on a human review right now."}
          </div>
        )}
      </section>

      {otherPending.length > 0 && (
        <section>
          <div className="state-label" style={{ marginBottom: 8 }}>
            Other pending approvals ({otherPending.length})
          </div>
          {otherPending.map((approval) => (
            <ApprovalRow
              key={approval.id}
              approval={approval}
              ticketId={ticket.id}
              dropRestatedCriteria={criteria.length > 0}
              isSubmitting={
                resolveApproval.isPending && resolveApproval.variables?.id === approval.id
              }
              onResolve={(action, payload) =>
                resolveApproval.mutate({ id: approval.id, action, payload })
              }
            />
          ))}
        </section>
      )}
    </div>
  );
}

/**
 * The scope hint matters here: the approvals list covers the ticket's whole
 * subtree, so a card can belong to a child ticket rather than the open one.
 */
function ApprovalRow({
  approval,
  ticketId,
  dropRestatedCriteria,
  isSubmitting,
  onResolve,
}: {
  approval: Approval;
  ticketId: string;
  /** The criteria section above already shows them; trim the brief's copy. */
  dropRestatedCriteria: boolean;
  isSubmitting: boolean;
  onResolve: (action: "approve" | "reject", payload?: ApprovalResolvePayload) => void;
}) {
  return (
    <div>
      {approval.ticket_id && approval.ticket_id !== ticketId && approval.ticket_external_id && (
        <div className="approvals-view-scope-hint">
          {kindLabel(approval)} · {approval.ticket_external_id}
        </div>
      )}
      <ApprovalCard
        approval={approval}
        impactText={dropRestatedCriteria ? impactWithoutCriteria(approval.impact) : undefined}
        isSubmitting={isSubmitting}
        onApprove={(payload) => onResolve("approve", payload)}
        onReject={(payload) => onResolve("reject", payload)}
      />
    </div>
  );
}
