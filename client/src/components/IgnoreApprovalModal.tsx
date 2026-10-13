import { useEffect, useId, useState } from "react";

import type { Approval, IgnoreEffect } from "../api/client";
import { ignorePresets, suggestIgnoreReason } from "../lib/ignoreReason";
import { IconCloseButton } from "./IconCloseButton";
import "./IgnoreApprovalModal.css";
import { Button } from "./ui/Button";
import { ModalShell } from "./ui/ModalShell";
import { Textarea } from "./ui/Textarea";

/** The longest reason the server keeps (`approval_ignore.MAX_IGNORE_REASON`). */
const MAX_REASON = 500;

/** What the operator is told before ignoring a card something still waits on. */
function waiterWarning(effect: IgnoreEffect | undefined, approval: Approval): string {
  if (effect === "denies_agent") {
    return "An agent is waiting on this prompt right now. Ignoring answers it with a denial, as Deny would.";
  }
  if (effect === "declines_stage") {
    const stage = approval.stage_name || approval.stage_key || "A stage";
    return `${stage} is parked on this card. Ignoring answers it the way Reject does: the workflow's reject route runs.`;
  }
  return "";
}

/**
 * Close an approval nobody needs to decide — finished ticket, duplicate, a
 * test — with a reason, instead of approving or rejecting something stale.
 *
 * Five states: nothing loads (the card is in hand); empty is a blank reason,
 * which keeps Ignore disabled; the server's refusal reaches the toast through
 * the resolve mutation's `errorTitle`; in flight disables every control and the
 * dismiss so a second click cannot fire; the shell traps focus, Escape closes,
 * and focus starts in the reason box.
 */
export function IgnoreApprovalModal({
  open,
  approval,
  hasDuplicate = false,
  isSubmitting,
  onClose,
  onConfirm,
}: {
  open: boolean;
  approval: Approval | null;
  /** Another pending card on the same ticket has the same title. */
  hasDuplicate?: boolean;
  isSubmitting?: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  const fieldId = useId();

  useEffect(() => {
    if (open) setReason("");
  }, [open, approval?.id]);

  if (!open || !approval) {
    // Closed but mounted: the shell plays its exit with the last content it drew.
    return <ModalShell open={false} onDismiss={undefined} labelledBy="ignore-approval-title">{null}</ModalShell>;
  }

  const trimmed = reason.trim();
  const canSubmit = trimmed.length > 0 && trimmed.length <= MAX_REASON;
  const warning = waiterWarning(approval.ignore_effect, approval);

  return (
    <ModalShell open onDismiss={isSubmitting ? undefined : onClose} labelledBy="ignore-approval-title">
      <div className="modal-header">
        <div>
          {approval.ticket_external_id ? (
            <div className="state-label">{approval.ticket_external_id}</div>
          ) : null}
          <h2 id="ignore-approval-title" className="modal-title">
            Ignore this approval?
          </h2>
          <p className="modal-subtitle">{approval.title}</p>
        </div>
        <IconCloseButton disabled={isSubmitting} onClick={onClose} />
      </div>

      <div className="modal-body">
        {warning ? (
          <p className="ignore-approval-warning" role="alert">
            {warning}
          </p>
        ) : null}

        <div id={`${fieldId}-presets`} className="modal-section-title">
          Common reasons
        </div>
        <div className="ignore-approval-presets" role="group" aria-labelledby={`${fieldId}-presets`}>
          {ignorePresets(approval).map((preset) => (
            <Button
              key={preset.key}
              variant="secondary"
              compact
              aria-pressed={trimmed === preset.label}
              disabled={isSubmitting}
              onClick={() => setReason(preset.label)}
            >
              {preset.label}
            </Button>
          ))}
          <Button
            variant="secondary"
            compact
            disabled={isSubmitting}
            onClick={() => setReason(suggestIgnoreReason(approval, { hasDuplicate }))}
          >
            Write one for me
          </Button>
        </div>

        <div id={`${fieldId}-reason`} className="modal-section-title" style={{ marginTop: 14 }}>
          Reason
        </div>
        <Textarea
          aria-labelledby={`${fieldId}-reason`}
          aria-describedby={`${fieldId}-hint`}
          value={reason}
          disabled={isSubmitting}
          maxLength={MAX_REASON}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Why does this card not need a decision?"
          rows={3}
          autoFocus
          className="ignore-approval-reason"
        />
        <p id={`${fieldId}-hint`} className="modal-hint" style={{ marginTop: 6 }}>
          Kept on the approval's history. A card the control plane files itself comes back if
          its cause is still there.
        </p>
      </div>

      <div className="modal-footer">
        <Button variant="secondary" disabled={isSubmitting} onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="primary"
          disabled={!canSubmit || isSubmitting}
          onClick={() => {
            if (canSubmit) onConfirm(trimmed);
          }}
        >
          {isSubmitting ? "Ignoring…" : "Ignore"}
        </Button>
      </div>
    </ModalShell>
  );
}
