import { useId } from "react";

import type { ImportedTicket } from "../../api/client";
import { Button } from "../ui/Button";
import { ModalShell } from "../ui/ModalShell";

const LISTED = 3;

/** The last stop before a smart import becomes tickets: what will be created, and from what. */
export function FinalizeImportDialog({
  open,
  ticketCount,
  importedTickets,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  /** The selected drafts, which are what the commit creates. */
  ticketCount: number;
  importedTickets: ImportedTicket[];
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const titleId = useId();
  const bodyId = useId();
  const plural = ticketCount === 1 ? "" : "s";
  const unlisted = importedTickets.length - LISTED;

  return (
    <ModalShell open={open} onDismiss={onCancel} labelledBy={titleId} describedBy={bodyId}>
      <div className="modal-header">
        <h2 id={titleId} className="modal-title">
          Finalize imported tickets?
        </h2>
      </div>
      <div
        className="modal-body scroll-focus-ring"
        role="group"
        aria-label="What finalizing this import does"
        tabIndex={0}
      >
        <p id={bodyId} style={{ margin: 0, fontSize: "var(--fs-body)", color: "var(--tx)" }}>
          This will create {ticketCount} work item{plural} in your workspace. This action cannot be undone.
        </p>
        {importedTickets.length > 0 && (
          <>
            <div className="studio-card-title">
              {importedTickets.length} imported {importedTickets.length === 1 ? "ticket" : "tickets"}
            </div>
            <ul className="modal-hint">
              {importedTickets.slice(0, LISTED).map((ticket) => (
                <li key={ticket.external_id}>{ticket.title}</li>
              ))}
              {unlisted > 0 && <li>and {unlisted} more</li>}
            </ul>
          </>
        )}
      </div>
      <div className="modal-footer">
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="primary" onClick={onConfirm}>
          Create {ticketCount} ticket{plural}
        </Button>
      </div>
    </ModalShell>
  );
}
