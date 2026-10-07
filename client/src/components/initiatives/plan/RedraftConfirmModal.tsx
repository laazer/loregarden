import type { ScheduleToReplace } from "../../../lib/scheduleFormat";
import { useDialogDismiss } from "../../../hooks/useDialogDismiss";
import { useDialogFocusTrap } from "../../../hooks/useDialogFocusTrap";
import { IconCloseButton } from "../../IconCloseButton";
import { Button } from "../../ui/Button";

/**
 * The warning "Draft schedule" goes through once a schedule exists.
 *
 * Drafting writes no targets by itself, but it does replace a waiting proposal
 * immediately, and accepting the draft overwrites the dates and order set now —
 * so the operator hears both before an agent run is spent on it.
 */
export function RedraftConfirmModal({
  replaces,
  onClose,
  onConfirm,
}: {
  /** What drafting again would replace, or null when the dialog is closed. */
  replaces: ScheduleToReplace | null;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  useDialogDismiss(replaces ? onClose : null);
  if (!replaces) return null;

  return (
    <>
      <div className="modal-overlay" onClick={onClose} role="presentation" />
      <div
        ref={dialogRef}
        className="modal-panel"
        role="dialog"
        aria-labelledby="redraft-confirm-title"
        aria-describedby="redraft-confirm-body"
        aria-modal="true"
      >
        <div className="modal-header">
          <div>
            <div className="state-label">Schedule</div>
            <h2 id="redraft-confirm-title" className="modal-title">
              Replace the current schedule?
            </h2>
          </div>
          <IconCloseButton onClick={onClose} />
        </div>

        <div id="redraft-confirm-body" className="modal-body plan-redraft-body">
          <ul>
            {replaces.pendingProposal ? (
              <li>The proposal waiting for you is replaced as soon as the new draft is filed.</li>
            ) : null}
            {replaces.targets > 0 ? (
              <li>
                Accepting the new draft overwrites the {replaces.targets} target date
                {replaces.targets === 1 ? "" : "s"} set now, and can reorder the milestones.
              </li>
            ) : null}
          </ul>
          <p className="plan-muted">Your current dates stay in force until you press Accept on the new draft.</p>
        </div>

        <div className="modal-footer">
          <Button variant="secondary" onClick={onClose}>
            Keep the current schedule
          </Button>
          <Button variant="primary" onClick={onConfirm}>
            Draft a new schedule
          </Button>
        </div>
      </div>
    </>
  );
}
