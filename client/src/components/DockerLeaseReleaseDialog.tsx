/**
 * The confirm step before ending a capacity lease from the Machine board.
 *
 * Ending a lease does not stop its process. A released holder keeps running
 * with the ledger no longer counting it, and a dropped waiter whose process is
 * still alive queues again at the back — so the dialog says which of those the
 * operator is about to do, and asks why. The reason is kept on the lease.
 *
 * Releasing a run's parent lease does more than end one lease: `release_lease`
 * cascades to every child drawing on it, so the dialog counts them. Offering a
 * cascade as if it ended one lease is the dialog claiming less than the action.
 *
 * Focus is trapped and returned to the button that opened it; Escape and the
 * backdrop dismiss only while nothing is in flight, so a release cannot be
 * abandoned half-sent and fired again.
 */

import { useState } from "react";

import type { DockerLeaseRow } from "../api/dockerTypes";
import { IconCloseButton } from "./IconCloseButton";
import { Button } from "./ui/Button";
import { ModalShell } from "./ui/ModalShell";
import { Textarea } from "./ui/Textarea";

const DEFAULT_REASON = "Ended from the Machine capacity board";

interface DockerLeaseReleaseProps {
  lease: DockerLeaseRow;
  /** How many live leases draw on this one, and would end with it. */
  childCount: number;
  inFlight: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => void;
}

/** A run's standing claim, which gates draw children from. */
function isRunParent(lease: DockerLeaseRow): boolean {
  return lease.holder_kind === "orchestration" && lease.parent_lease_id === null;
}

/**
 * Open while there is a lease to confirm. The shell stays mounted so a close
 * can play its exit; the form mounts only while open, so each confirmation
 * starts from the default reason.
 */
export function DockerLeaseReleaseDialog({
  lease,
  ...props
}: Omit<DockerLeaseReleaseProps, "lease"> & { lease: DockerLeaseRow | null }) {
  return (
    <ModalShell
      open={lease !== null}
      onDismiss={props.inFlight ? undefined : props.onClose}
      labelledBy="docker-release-title"
      describedBy="docker-release-effect"
    >
      {lease ? <DockerLeaseReleaseBody {...props} lease={lease} /> : null}
    </ModalShell>
  );
}

function DockerLeaseReleaseBody({
  lease,
  childCount,
  inFlight,
  onClose,
  onConfirm,
}: DockerLeaseReleaseProps) {
  const [reason, setReason] = useState(DEFAULT_REASON);

  const waiting = lease.status === "waiting";
  const parent = !waiting && isRunParent(lease);
  const what = lease.holder.what || "this unlabelled lease";
  const pid = lease.holder.pid ? `pid ${lease.holder.pid}` : "its process";
  const trimmed = reason.trim();
  const cascade = parent
    ? childCount === 0
      ? "Nothing draws on it right now."
      : `${childCount} gate ${childCount === 1 ? "child draws" : "children draw"} on it, and ${childCount === 1 ? "it ends" : "they end"} with it.`
    : "";

  return (
    <>
      <div className="modal-header">
        <div>
          <div className="state-label">Machine capacity</div>
          <h2 id="docker-release-title" className="modal-title">
            {waiting
              ? "Drop from the line?"
              : parent
                ? "Release this run's parent lease?"
                : "Release this hold?"}
          </h2>
          <p className="modal-subtitle">
            {what}
            {lease.holder.branch ? ` · ${lease.holder.branch}` : ""}
          </p>
        </div>
        <IconCloseButton disabled={inFlight} onClick={onClose} />
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!inFlight && trimmed) onConfirm(trimmed);
        }}
      >
        <div className="modal-body">
          <p id="docker-release-effect" className="docker-release-effect">
            {waiting
              ? `It leaves the line now. If ${pid} is still running it will queue again at the back.`
              : `The ledger stops counting it and the next claim in line can start. ${pid} is not stopped — stop it yourself if it is stuck.`}
          </p>
          {cascade ? <p className="docker-release-effect">{cascade}</p> : null}
          <label className="docker-release-field">
            <span>Why (kept on the lease)</span>
            <Textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              rows={2}
              required
              disabled={inFlight}
            />
          </label>
        </div>
        <div className="modal-footer">
          <Button variant="secondary" disabled={inFlight} onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" disabled={inFlight || !trimmed}>
            {inFlight ? "Ending…" : waiting ? "Drop from line" : "Release"}
          </Button>
        </div>
      </form>
    </>
  );
}
