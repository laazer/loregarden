
import type { Approval } from "../api/client";
import { ApprovalCard, type ApprovalResolvePayload } from "./ApprovalCard";
import { IconCloseButton } from "./IconCloseButton";
import { useDialogFocusTrap } from "../hooks/useDialogFocusTrap";
import { useDialogDismiss } from "../hooks/useDialogDismiss";

/**
 * The full approval — criteria, checklist, questions — outside the narrow rail
 * that clamped it. Same card, unclamped, so approving from here behaves
 * identically to approving from the inbox.
 */
export function ApprovalDetailModal({
  open,
  approval,
  isSubmitting,
  onClose,
  onApprove,
  onReject,
  onOpenApprovalsTab,
}: {
  open: boolean;
  approval: Approval | null;
  isSubmitting?: boolean;
  onClose: () => void;
  onApprove: (payload?: ApprovalResolvePayload) => void;
  onReject: (payload?: ApprovalResolvePayload) => void;
  onOpenApprovalsTab?: () => void;
}) {
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  // Through the shared stack rather than a listener of its own: two of these
  // mounted at once both closed on a single press, because nothing decided
  // whose press it was.
  useDialogDismiss(open ? onClose : null);

  if (!open || !approval) return null;

  return (
    <>
      <div className="modal-overlay" onClick={onClose} role="presentation" />
      <div
        ref={dialogRef}
        className="modal-panel modal-panel-wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="approval-detail-title"
      >
        <div className="modal-header">
          <div>
            <div className="state-label">{approval.stage_name}</div>
            <h2 id="approval-detail-title" className="modal-title">
              Approval details
            </h2>
            {approval.ticket_external_id ? (
              <p className="modal-subtitle">{approval.ticket_external_id}</p>
            ) : null}
          </div>
          <IconCloseButton onClick={onClose} />
        </div>
        <div className="modal-body approval-detail-body">
          <ApprovalCard
            approval={approval}
            isSubmitting={isSubmitting}
            onApprove={onApprove}
            onReject={onReject}
            onInspect={onOpenApprovalsTab}
            inspectLabel="Open Approvals tab"
          />
        </div>
      </div>
    </>
  );
}
