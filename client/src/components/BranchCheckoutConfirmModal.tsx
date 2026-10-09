import { IconCloseButton } from "./IconCloseButton";

import type { BranchTriageEntry } from "../lib/branchTriageApi";
import { Button } from "./ui/Button";
import { ModalShell } from "./ui/ModalShell";

interface BranchCheckoutConfirmModalProps {
  open: boolean;
  branch: BranchTriageEntry | null;
  currentBranch: BranchTriageEntry | null;
  isCheckingOut: boolean;
  error?: string | null;
  onClose: () => void;
  onConfirm: () => void;
}

export function BranchCheckoutConfirmModal({
  open,
  branch,
  currentBranch,
  isCheckingOut,
  error,
  onClose,
  onConfirm,
}: BranchCheckoutConfirmModalProps) {
  if (!open || !branch) {
    // Closed but mounted: the shell plays its exit with the last content it drew.
    return <ModalShell open={false} onDismiss={undefined} labelledBy="branch-checkout-confirm-title">{null}</ModalShell>;
  }

  return (
    <ModalShell open onDismiss={isCheckingOut ? undefined : onClose} labelledBy="branch-checkout-confirm-title" panelClassName="branch-delete-confirm-modal">
      <div className="modal-header">
        <div>
          <div className="state-label">Branch triage</div>
          <h2 id="branch-checkout-confirm-title" className="modal-title">
            Checkout branch?
          </h2>
          <p className="modal-subtitle" style={{ fontFamily: "var(--mono)" }}>
            {branch.name}
          </p>
        </div>
        <IconCloseButton disabled={isCheckingOut} onClick={onClose} />
      </div>

      <div className="modal-body">
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.55, color: "var(--txm)" }}>
          This switches your workspace's local git checkout to{" "}
          <strong style={{ color: "var(--tx)" }}>{branch.name}</strong>. Anything running against
          this workspace's files will start seeing that branch's contents.
        </p>

        {currentBranch?.dirty ? (
          <p className="modal-hint" style={{ marginTop: 12 }}>
            Your current branch (<code>{currentBranch.name}</code>) has uncommitted changes.
            Commit or stash them first if you don't want them carried over.
          </p>
        ) : null}

        {error ? (
          <div className="branch-triage-delete-error" style={{ marginTop: 12 }}>
            {error}
          </div>
        ) : null}
      </div>

      <div className="modal-footer">
        <Button variant="secondary" disabled={isCheckingOut} onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="primary"
          disabled={isCheckingOut}
          onClick={onConfirm}
        >
          {isCheckingOut ? "Checking out…" : "Checkout branch"}
        </Button>
      </div>
    </ModalShell>
  );
}
