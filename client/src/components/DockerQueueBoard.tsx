/**
 * The docker queue, drawn the way the lane board draws the agent queue.
 *
 * This is the second pool on the machine. The lanes beside it ration how many
 * agents run at once; this rations the containers those agents start, and the
 * question is the same shape — what is occupied, what is behind it, and how
 * long until my turn. So it uses the same vocabulary: a grid of slots across
 * the top, each holding something or idle, and the line waiting underneath.
 *
 * **One shared queue, not one per slot.** That is the real difference from the
 * lane board and the reason the waiting list sits below the grid rather than
 * inside each slot. A lane is a serial pipeline, so position 3 in lane 1 cannot
 * start in lane 2. Docker capacity is a single pool: the next claim starts
 * wherever room appears, and drawing a queue under each slot would imply a
 * choice of line that does not exist.
 *
 * **Slots are the lease ceiling, not physical hardware.** A claim also has to
 * fit on cpus and memory, so a slot being free does not by itself mean the next
 * waiter can start — which is why the head of the queue shows what it is waiting
 * for rather than just its position.
 */

import type { ReactNode } from "react";

import type { DockerCapacityStatus, DockerLeaseRow } from "../api/dockerTypes";
import { CapacityMeter } from "./ui/CapacityMeter";
import "./DockerQueueBoard.css";

function formatCpus(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  return `${rounded} ${rounded === 1 ? "cpu" : "cpus"}`;
}

function formatMemory(mb: number): string {
  if (mb >= 1024) return `${Math.round((mb / 1024) * 10) / 10} GB`;
  return `${Math.round(mb)} MB`;
}

function formatLeases(value: number): string {
  return `${Math.round(value)} ${Math.round(value) === 1 ? "lease" : "leases"}`;
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.round((minutes / 60) * 10) / 10}h`;
}

/**
 * A projected wait, carrying how much it is worth.
 *
 * `≈` is a median of what these leases have actually cost; `≤` is a TTL nobody
 * has beaten yet. Rendering them identically would present a ceiling as a
 * prediction.
 */
function describeWait(row: DockerLeaseRow): string {
  if (row.estimated_wait_seconds === null) return "wait unknown";
  const amount = formatDuration(row.estimated_wait_seconds);
  return row.estimate_basis === "ttl_bound" ? `starts in ≤ ${amount}` : `starts in ≈ ${amount}`;
}

function holderStatus(row: DockerLeaseRow): string {
  if (row.status === "orphaned") return "orphaned";
  if (row.last_probe_outcome && row.last_probe_outcome !== "ok") return "unverified";
  return "holding";
}

function HolderSlot({ row }: { row: DockerLeaseRow }) {
  const status = holderStatus(row);
  return (
    <div className="queue-slot queue-slot--busy" data-testid={`docker-slot-${row.lease_id}`}>
      <div className="queue-slot-head">
        <span className="queue-slot-dot" aria-hidden />
        <span className="queue-slot-name">{row.footprint}</span>
        <span className="queue-slot-badge" data-run-status={status}>
          {status}
        </span>
      </div>
      <div className="queue-slot-body">
        <div className="queue-slot-title" title={row.holder_label}>
          {row.holder_label || "unlabelled"}
        </div>
        <div className="queue-slot-sub">
          {formatCpus(row.cpus)} · {formatMemory(row.memory_mb)}
          {row.compose_project ? ` · ${row.compose_project}` : ""}
        </div>
        <div className="docker-slot-expiry">
          {row.expires_in_seconds === null
            ? "no expiry"
            : `expires in ${formatDuration(row.expires_in_seconds)}`}
        </div>
        {row.last_probe_error ? (
          <div className="docker-slot-error" title={row.last_probe_error}>
            {row.last_probe_error}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function EmptySlot({ index }: { index: number }) {
  return (
    <div className="queue-slot" data-testid={`docker-slot-free-${index}`}>
      <div className="queue-slot-head">
        <span className="queue-slot-dot" aria-hidden />
        <span className="queue-slot-name">Free</span>
        <span className="queue-slot-badge" data-run-status="available">
          available
        </span>
      </div>
      <div className="queue-slot-body">
        <div className="queue-slot-title">Available</div>
        <div className="queue-slot-sub">Reserve capacity before starting containers</div>
      </div>
    </div>
  );
}

export function DockerQueueBoard({
  status,
  error,
  loading,
  headerSlot,
}: {
  status: DockerCapacityStatus | null;
  error: string;
  loading: boolean;
  headerSlot?: ReactNode;
}) {
  // The board owns the main area now, so it owns the states the rail used to
  // cover: a first read still in flight, a read that failed, and a ledger that
  // is switched off. A failed read and an idle pool are kept apart — they are
  // indistinguishable if you only check whether the lists came back empty.
  if (!status) {
    return (
      <div className="queue-panel docker-board">
        <div className="queue-panel-head">
          <div className="queue-panel-title">Docker capacity</div>
          {headerSlot}
        </div>
        <div className="queue-idle" role={loading ? "status" : undefined}>
          {loading ? "Reading the ledger…" : error || "Capacity is unavailable."}
        </div>
      </div>
    );
  }

  if (!status.enabled) {
    return (
      <div className="queue-panel docker-board">
        <div className="queue-panel-head">
          <div className="queue-panel-title">Docker capacity</div>
          {headerSlot}
        </div>
        <div className="queue-idle">
          The capacity ledger is switched off. Nothing is being tracked or limited.
        </div>
      </div>
    );
  }

  const { ceiling, in_use: inUse, holders, waiting } = status;

  // Slots are the lease ceiling. An unmeasured ceiling has no slot count to
  // draw, so the grid is skipped entirely rather than rendered as zero slots —
  // which would read as "the machine is full" when it means "nobody has looked".
  const freeSlots = Math.max(0, ceiling.leases - holders.length);

  return (
    <div className="queue-panel docker-board">
      <div className="queue-panel-head">
        <div className="queue-panel-title">Docker capacity</div>
        {headerSlot}
        <div className="docker-board-summary">
          {ceiling.leases > 0
            ? `${holders.length} of ${ceiling.leases} leases held · ${waiting.length} waiting`
            : "Capacity not measured"}
        </div>
      </div>

      <div className="docker-board-meters">
        <CapacityMeter label="CPU" used={inUse.cpus} total={ceiling.cpus} format={formatCpus} />
        <CapacityMeter
          label="Memory"
          used={inUse.memory_mb}
          total={ceiling.memory_mb}
          format={formatMemory}
        />
        <CapacityMeter
          label="Leases"
          used={inUse.leases}
          total={ceiling.leases}
          format={formatLeases}
        />
      </div>

      {ceiling.leases > 0 ? (
        <div className="queue-slot-grid" data-testid="docker-slot-grid">
          {holders.map((row) => (
            <HolderSlot key={row.lease_id} row={row} />
          ))}
          {Array.from({ length: freeSlots }, (_, index) => (
            <EmptySlot key={`free-${index}`} index={index} />
          ))}
        </div>
      ) : null}

      <div className="queue-section-head">
        <div className="queue-section-title">Waiting for capacity ({waiting.length})</div>
        {/* One line, not one per slot: capacity is a single pool, so the next
            claim starts wherever room appears. */}
        <div className="queue-section-hint">
          One shared line — strict order, no jumping the head
        </div>
      </div>

      {waiting.length === 0 ? (
        <div className="queue-idle">
          {holders.length === 0
            ? "Nothing is holding docker capacity, and nobody is queued for it."
            : "Nobody is queued — the next claim starts immediately if it fits."}
        </div>
      ) : (
        <div className="docker-queue-list">
          {waiting.map((row) => (
            <div
              className="queue-lane-item"
              key={row.lease_id}
              data-testid={`docker-waiting-${row.lease_id}`}
            >
              <span className="queue-lane-item-position">{row.position ?? "?"}</span>
              <div className="queue-lane-item-copy">
                <div className="queue-lane-item-title" title={row.holder_label}>
                  {row.holder_label || "unlabelled"}
                </div>
                <div className="queue-lane-item-sub">
                  {formatCpus(row.cpus)} · {formatMemory(row.memory_mb)} · {row.footprint}
                </div>
              </div>
              <div className="queue-lane-item-timing">{describeWait(row)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
