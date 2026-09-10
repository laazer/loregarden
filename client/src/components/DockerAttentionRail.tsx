/**
 * Docker leases that need a person, and why.
 *
 * Its own panel rather than badges on the board, because these two states are
 * the ones the ledger cannot resolve by itself and both need an explanation
 * longer than a badge:
 *
 * - **Orphaned** — the lease expired but Docker confirms its containers are
 *   still running, so the capacity is still genuinely in use. The ledger never
 *   runs `docker compose down`; somebody has to decide whether that stack is
 *   stale.
 * - **Unverified** — the reaper could not reach Docker to ask, so it is holding
 *   the capacity rather than guessing. Nothing is wrong with the lease; the
 *   check itself failed, and that distinction is the whole point of listing the
 *   probe error next to it.
 *
 * Empty is the normal case and says so plainly, rather than leaving a blank
 * panel that reads as broken.
 */

import type { DockerCapacityStatus } from "../api/dockerTypes";

export function DockerAttentionRail({ status }: { status: DockerCapacityStatus | null }) {
  if (!status) {
    return (
      <>
        <div className="queue-rail-heading">Needs attention</div>
        <p className="queue-rail-empty">Capacity has not been read yet.</p>
      </>
    );
  }

  const orphaned = status.holders.filter((row) => row.status === "orphaned");
  const unverifiable = status.unverifiable;

  if (!orphaned.length && !unverifiable.length) {
    return (
      <>
        <div className="queue-rail-heading">Needs attention</div>
        <p className="queue-rail-empty">
          Every held lease is accounted for and verified. Nothing to do.
        </p>
      </>
    );
  }

  return (
    <>
      <div className="queue-rail-heading">Needs attention</div>

      {orphaned.length ? (
        <>
          <div className="docker-section-heading">
            Still running <span className="docker-count">{orphaned.length}</span>
          </div>
          {orphaned.map((row) => (
            <div className="docker-attention-item" key={row.lease_id}>
              <div className="docker-attention-title">{row.holder_label || "unlabelled"}</div>
              <div className="docker-attention-body">
                Expired, but {row.running_container_count ?? "some"} of its containers are still
                up. The ledger will not stop them — stop the stack yourself if it is stale, or
                renew the lease if the work is still wanted.
              </div>
              {row.compose_project ? (
                <div className="docker-attention-cmd">
                  docker compose -p {row.compose_project} down
                </div>
              ) : null}
            </div>
          ))}
        </>
      ) : null}

      {unverifiable.length ? (
        <>
          <div className="docker-section-heading">
            Could not verify <span className="docker-count">{unverifiable.length}</span>
          </div>
          {unverifiable.map((entry) => (
            <div className="docker-attention-item" key={entry.lease_id}>
              <div className="docker-attention-title">{entry.lease_id.slice(0, 8)}</div>
              <div className="docker-attention-body">
                The reaper could not reach Docker to check this lease, so it is holding the
                capacity rather than guessing it is free.
              </div>
              <div className="docker-attention-cmd">{entry.error || entry.outcome}</div>
            </div>
          ))}
        </>
      ) : null}
    </>
  );
}
