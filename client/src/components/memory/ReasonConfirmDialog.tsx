/**
 * The confirm step every memory action goes through.
 *
 * Each of them — discredit, restore, merge, supersede, withdraw — changes what
 * every future agent run is briefed with. So each asks first, and the ones that
 * rewrite a learning ask why: the reason is stored with the change in the
 * node's version history, where the next person to wonder can read it.
 *
 * Same contract as lore-eden's `ConfirmDialog`: focus trapped and returned,
 * Escape and the backdrop dismiss only while nothing is in flight, and a
 * required reason is required — whitespace does not count.
 */

import { useState, type ReactNode } from "react";

import { useDialogDismiss } from "../../hooks/useDialogDismiss";
import { useDialogFocusTrap } from "../../hooks/useDialogFocusTrap";
import { IconCloseButton } from "../IconCloseButton";

export function ReasonConfirmDialog({
  open,
  eyebrow = "Learning",
  title,
  subtitle,
  description,
  confirmLabel,
  reasonLabel,
  danger = false,
  isSaving,
  confirmBlocked = false,
  onClose,
  onConfirm,
  children,
}: {
  open: boolean;
  eyebrow?: string;
  title: string;
  subtitle?: string;
  description: ReactNode;
  confirmLabel: string;
  /** Label for the reason field. Omit for an action that records none. */
  reasonLabel?: string;
  danger?: boolean;
  isSaving: boolean;
  /** Holds the confirm button disabled for a reason of the caller's (an empty field). */
  confirmBlocked?: boolean;
  onClose: () => void;
  /** The trimmed reason, or "" when none is asked for. */
  onConfirm: (reason: string) => void;
  /** Extra controls between the description and the reason (e.g. a choice). */
  children?: ReactNode;
}) {
  const [reason, setReason] = useState("");
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  useDialogDismiss(!open ? null : isSaving ? undefined : onClose);
  if (!open) return null;

  const trimmed = reason.trim();
  const blocked = isSaving || confirmBlocked || (reasonLabel !== undefined && !trimmed);

  return (
    <>
      <div className="modal-overlay" onClick={isSaving ? undefined : onClose} role="presentation" />
      <div
        ref={dialogRef}
        className="modal-panel"
        role="dialog"
        aria-labelledby="reason-confirm-title"
        aria-modal="true"
      >
        <div className="modal-header">
          <div>
            <div className="state-label">{eyebrow}</div>
            <h2 id="reason-confirm-title" className="modal-title">
              {title}
            </h2>
            {subtitle ? <p className="modal-subtitle">{subtitle}</p> : null}
          </div>
          <IconCloseButton disabled={isSaving} onClick={onClose} />
        </div>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (!blocked) onConfirm(reasonLabel === undefined ? "" : trimmed);
          }}
        >
          <div className="modal-body">
            <div className="memory-muted">{description}</div>
            {children}
            {reasonLabel !== undefined ? (
              <label className="memory-field">
                <span>{reasonLabel}</span>
                <textarea
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  rows={3}
                  required
                  disabled={isSaving}
                />
              </label>
            ) : null}
          </div>
          <div className="modal-footer">
            <button type="button" className="btn-secondary" disabled={isSaving} onClick={onClose}>
              Cancel
            </button>
            <button
              type="submit"
              className={danger ? "btn-primary memory-danger" : "btn-primary"}
              disabled={blocked}
            >
              {isSaving ? "Saving…" : confirmLabel}
            </button>
          </div>
        </form>
      </div>
    </>
  );
}
