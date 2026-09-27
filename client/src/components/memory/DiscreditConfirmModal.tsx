/**
 * The confirm step for discrediting or restoring a learning — the reason is
 * stored with the change (see `ReasonConfirmDialog`).
 */

import type { MemoryNode } from "../../api/memoryApi";
import { ReasonConfirmDialog } from "./ReasonConfirmDialog";

export function DiscreditConfirmModal({
  node,
  isSaving,
  onClose,
  onConfirm,
}: {
  /** The learning awaiting confirmation, or null when nothing is. */
  node: MemoryNode | null;
  isSaving: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => void;
}) {
  const restoring = node?.discredited ?? false;
  return (
    <ReasonConfirmDialog
      open={node !== null}
      title={restoring ? "Restore this learning?" : "Discredit this learning?"}
      subtitle={node?.title}
      description={
        restoring
          ? "Agents will be briefed with this learning again on their next run."
          : "Agents stop being briefed with this learning on their next run. It stays in the record and can be restored."
      }
      reasonLabel="Reason (stored with the change)"
      confirmLabel={restoring ? "Restore learning" : "Discredit learning"}
      danger={!restoring}
      isSaving={isSaving}
      onClose={onClose}
      onConfirm={onConfirm}
    />
  );
}
