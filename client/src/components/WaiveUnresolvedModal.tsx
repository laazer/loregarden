import { useEffect, useId, useRef, useState } from "react";

import { IconCloseButton } from "./IconCloseButton";
import { Button } from "./ui/Button";
import { ModalShell } from "./ui/ModalShell";
import { Textarea } from "./ui/Textarea";

export interface WaiveUnresolvedPayload {
  response: string;
}

interface WaiveUnresolvedModalProps {
  open: boolean;
  names: string[];
  isSubmitting?: boolean;
  onClose: () => void;
  onConfirm: (payload: WaiveUnresolvedPayload) => void;
}

/**
 * Written reason that waives every currently listed unresolved name on a
 * Rework paused card. Copy of RejectApprovalModal's one-field shape: no
 * per-name picker, no route select, no fetch.
 */
export function WaiveUnresolvedModal({
  open,
  names,
  isSubmitting,
  onClose,
  onConfirm,
}: WaiveUnresolvedModalProps) {
  const [reason, setReason] = useState("");
  const fieldId = useId();
  const submittedRef = useRef(false);

  useEffect(() => {
    if (!open) {
      setReason("");
      submittedRef.current = false;
      return;
    }
  }, [open]);

  useEffect(() => {
    if (!isSubmitting) submittedRef.current = false;
  }, [isSubmitting]);

  if (!open) {
    return (
      <ModalShell open={false} onDismiss={undefined} labelledBy="waive-unresolved-title">
        {null}
      </ModalShell>
    );
  }

  const canSubmit = reason.trim().length > 0;
  const busy = Boolean(isSubmitting);

  const handleConfirm = () => {
    if (!canSubmit || busy || submittedRef.current) return;
    submittedRef.current = true;
    onConfirm({ response: reason.trim() });
  };

  return (
    <ModalShell
      open
      onDismiss={busy ? undefined : onClose}
      labelledBy="waive-unresolved-title"
    >
      <div className="modal-header">
        <div>
          <h2 id="waive-unresolved-title" className="modal-title">
            Waive unresolved items?
          </h2>
          <p className="modal-subtitle">One reason covers every listed item.</p>
        </div>
        <IconCloseButton disabled={busy} onClick={onClose} />
      </div>

      <div className="modal-body">
        <ul
          style={{
            margin: "0 0 16px",
            paddingLeft: 20,
            fontSize: "var(--fs-small)",
            color: "var(--txm)",
          }}
        >
          {names.map((name) => (
            <li key={name}>
              <code style={{ fontFamily: "var(--mono)", color: "var(--tx)" }}>{name}</code>
            </li>
          ))}
        </ul>

        <div style={{ marginBottom: 16 }}>
          <div id={`${fieldId}-reason`} className="modal-section-title">
            Reason
          </div>
          <Textarea
            aria-labelledby={`${fieldId}-reason`}
            value={reason}
            disabled={busy}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why are these remaining items acceptable?"
            rows={4}
            autoFocus
            style={{
              width: "100%",
              padding: "8px 10px",
              borderRadius: 8,
              border: "1px solid var(--bd)",
              background: "var(--bg2)",
              color: "var(--tx)",
              fontSize: "var(--fs-small)",
              resize: "vertical",
              boxSizing: "border-box",
            }}
          />
          <p className="modal-hint" style={{ marginTop: 6 }}>
            Shared with the next run as the waiver. Does not pick names one by one.
          </p>
        </div>
      </div>

      <div className="modal-footer">
        <Button variant="secondary" disabled={busy} onClick={onClose}>
          Cancel
        </Button>
        <Button variant="primary" disabled={!canSubmit || busy} onClick={handleConfirm}>
          {busy ? "Waiving…" : "Waive and continue"}
        </Button>
      </div>
    </ModalShell>
  );
}
