import type { TicketDetail } from "../api/client";
import { describeError } from "../state/toastStore";
import { formatApprovalResolveError } from "../utils/approvalErrors";
import { useResolveApproval, useTicketApprovals } from "../hooks/useTicketApprovals";
import { ApprovalRow } from "./dashboard/ApprovalRow";

/**
 * "Does this ticket need me?" answered where the ticket is already open: its
 * pending sign-offs and agent questions, each resolvable in place, so a peek
 * from a ticket link is enough to unblock it without a trip to the Approvals tab.
 *
 * Loading and empty render nothing — the modal is about the ticket, and most
 * tickets have nothing pending; the section appears when there is something to
 * act on. A failed fetch is said out loud, never shown as "nothing pending".
 */
export function TicketModalApprovals({ ticket, criteria }: { ticket: TicketDetail; criteria: string[] }) {
  const approvals = useTicketApprovals(ticket.id);
  const resolveApproval = useResolveApproval();

  if (approvals.isError) {
    return (
      <div className="state-card" role="alert">
        <div className="state-label">Approvals</div>
        <p className="modal-hint" style={{ color: "var(--red)", margin: 0 }}>
          Could not load this ticket&rsquo;s approvals: {describeError(approvals.error, "the request failed")}.
          Retrying every few seconds.
        </p>
      </div>
    );
  }

  const pending = approvals.data ?? [];
  // ux-ok: an empty or still-loading list is the common case and renders nothing on purpose; see the header.
  if (pending.length === 0) return null;

  return (
    <section className="state-card ticket-modal-approvals" aria-label="Pending approvals">
      <div className="state-label">Needs you ({pending.length})</div>
      {resolveApproval.isError && (
        <div className="approvals-view-error" role="alert">
          {formatApprovalResolveError(resolveApproval.error)}
        </div>
      )}
      {pending.map((approval) => (
        <ApprovalRow
          key={approval.id}
          approval={approval}
          ticketId={ticket.id}
          criteria={criteria}
          isSubmitting={resolveApproval.isPending && resolveApproval.variables?.id === approval.id}
          onResolve={(action, payload) => resolveApproval.mutate({ id: approval.id, action, payload })}
        />
      ))}
    </section>
  );
}
