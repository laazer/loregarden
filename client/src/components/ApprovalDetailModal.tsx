
import type { Approval } from "../api/client";
import { ApprovalCard, type ApprovalResolvePayload } from "./ApprovalCard";
import { IconCloseButton } from "./IconCloseButton";
import { ModalShell } from "./ui/ModalShell";

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
  onRecheck,
  onReject,
  onOpenApprovalsTab,
}: {
  open: boolean;
  approval: Approval | null;
  isSubmitting?: boolean;
  onClose: () => void;
  onApprove: (payload?: ApprovalResolvePayload) => void;
  onRecheck?: (payload?: ApprovalResolvePayload) => void;
  onReject: (payload?: ApprovalResolvePayload) => void;
  onOpenApprovalsTab?: () => void;
}) {

  if (!open || !approval) {
    // Closed but mounted: the shell plays its exit with the last content it drew.
    return <ModalShell open={false} onDismiss={undefined} labelledBy="approval-detail-title">{null}</ModalShell>;
  }

  return (
    <ModalShell open onDismiss={onClose} labelledBy="approval-detail-title" panelClassName="modal-panel-wide">
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
          onRecheck={onRecheck}
          onReject={onReject}
          onInspect={onOpenApprovalsTab}
          inspectLabel="Open Approvals tab"
        />
      </div>
    </ModalShell>
  );
}
