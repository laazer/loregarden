/**
 * The confirmation an agent delete goes through before anything is destroyed.
 *
 * `delete_agent` has never once succeeded — every db-backed agent carries at
 * least one version row, and the FK ordering bug fired on all of them — so
 * fixing it arms a one-click irreversible destroy for the first time. The row
 * takes its whole version history with it: 10.9 rows on average on the live
 * database, up to 17, and there is no restore endpoint behind them.
 *
 * It stays short because it is rare. The reference scan refuses every agent
 * something still names, so this dialog is only ever reached for an agent
 * nothing uses — which is what keeps it a confirmation worth reading rather
 * than a click to learn past.
 */

import { Button } from "../ui/Button";
import { ModalShell } from "../ui/ModalShell";

interface AgentDeleteConfirmProps {
  open: boolean;
  /** The agent about to be destroyed. */
  slug: string;
  /** How many stored versions go with the row. */
  versionCount: number;
  /** Whether the delete request is already away. */
  pending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

const TITLE_ID = "studio-agent-delete-confirm-title";

export function AgentDeleteConfirm({
  open,
  slug,
  versionCount,
  pending,
  onConfirm,
  onCancel,
}: AgentDeleteConfirmProps) {
  return (
    <ModalShell
      open={open}
      // Undefined while the request is away: a dialog that closes under Escape
      // or a backdrop click mid-flight leaves the outcome — the refusal notice
      // most of the time — with nowhere to be read.
      onDismiss={pending ? undefined : onCancel}
      labelledBy={TITLE_ID}
    >
      <div className="modal-header">
        <div>
          <div className="state-label">Agent</div>
          <h2 id={TITLE_ID} className="modal-title">
            Delete agent?
          </h2>
          <p className="modal-subtitle">{slug}</p>
        </div>
      </div>

      <div className="modal-body">
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.55, color: "var(--txm)" }}>
          This deletes <code>{slug}</code> and its {versionCount} stored version
          {versionCount === 1 ? "" : "s"}. It cannot be undone.
        </p>
      </div>

      <div className="modal-footer">
        <Button variant="secondary" disabled={pending} onClick={onCancel}>
          Cancel
        </Button>
        <Button
          variant="primary"
          style={{ background: "var(--rdl)", borderColor: "transparent" }}
          disabled={pending}
          onClick={onConfirm}
        >
          {pending ? "Deleting…" : "Delete agent"}
        </Button>
      </div>
    </ModalShell>
  );
}
