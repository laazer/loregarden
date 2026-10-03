/**
 * The machine-capacity queue, drawn the way the lane board draws the agent queue.
 *
 * This is the second pool on the machine. The lanes beside it ration how many
 * agents run at once; this rations the CPU and memory their test runs, builds
 * and containers take. Every lease is charged to the host pool, and a docker
 * claim to the docker pool as well. The question is the same shape — what is
 * occupied, what is behind it, and how long until my turn. So it uses the same
 * vocabulary: a grid of slots across the top, each holding something or idle,
 * and the line waiting underneath.
 *
 * **One shared queue, not one per slot.** That is the real difference from the
 * lane board and the reason the waiting list sits below the grid rather than
 * inside each slot. A lane is a serial pipeline, so position 3 in lane 1 cannot
 * start in lane 2. Capacity is a single shared line: the next claim starts
 * wherever room appears, and drawing a queue under each slot would imply a
 * choice of line that does not exist.
 *
 * **Slots are the host pool's lease ceiling, not physical hardware.** A lease
 * books one whichever pool it claims from, except a nested lease (it runs in
 * its parent's) and an agent run's standing claim, which book none. A claim also has to fit on cpus and memory — and
 * a docker claim on the Docker VM's smaller pool too — so a slot being free does
 * not by itself mean the next waiter can start, which is why the head of the
 * queue shows what it is waiting for rather than just its position.
 */

import type { ReactNode } from "react";

import type {
  CapacityPoolSummary,
  CapacityShortfall,
  DockerCapacityStatus,
  DockerLeaseRow,
} from "../api/dockerTypes";
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

const POOL_PLACE: Record<CapacityShortfall["pool"], string> = {
  host: "on the machine",
  docker: "in Docker",
};

/** "needs 4 cpus, 3 cpus free on the machine" — the reason the head is not running. */
function describeShortfall(gaps: CapacityShortfall[]): string {
  return gaps
    .map((gap) => {
      const where = POOL_PLACE[gap.pool];
      if (gap.resource === "slots") return `needs a slot, none free ${where}`;
      const format = gap.resource === "cpus" ? formatCpus : formatMemory;
      return `needs ${format(gap.needed)}, ${format(gap.free)} free ${where}`;
    })
    .join("; ");
}

function holderStatus(row: DockerLeaseRow): string {
  if (row.status === "orphaned") return "orphaned";
  if (row.last_probe_outcome && row.last_probe_outcome !== "ok") return "unverified";
  return "holding";
}

/** Which pool a lease draws on, in the reader's words rather than the enum's. */
function describePool(row: DockerLeaseRow): string {
  if (row.parent_lease_id) return "nested";
  return row.pool === "docker" ? "docker" : "machine";
}

/**
 * One pool's three meters, named for the pool. The pools nest — docker claims are
 * charged to the machine too — so they are drawn as two rows, not summed.
 */
function PoolMeters({
  title,
  summary,
  leasesLabel,
}: {
  title: string;
  summary: CapacityPoolSummary;
  leasesLabel: string;
}) {
  const { ceiling, in_use: inUse } = summary;
  return (
    <section className="docker-board-pool" aria-label={title}>
      <div className="docker-board-meters">
        <CapacityMeter
          label={`${title} CPU`}
          used={inUse.cpus}
          total={ceiling.cpus}
          format={formatCpus}
        />
        <CapacityMeter
          label={`${title} memory`}
          used={inUse.memory_mb}
          total={ceiling.memory_mb}
          format={formatMemory}
        />
        <CapacityMeter
          label={`${title} ${leasesLabel}`}
          used={inUse.leases}
          total={ceiling.leases}
          format={formatLeases}
        />
      </div>
    </section>
  );
}

function HolderSlot({ row }: { row: DockerLeaseRow }) {
  const status = holderStatus(row);
  return (
    <div className="queue-slot queue-slot--busy" data-testid={`docker-slot-${row.lease_id}`}>
      <div className="queue-slot-head">
        <span className="queue-slot-dot" aria-hidden />
        <span className="queue-slot-name">
          {describePool(row)} · {row.footprint}
        </span>
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

/**
 * A slot no lease holds. `blockedBy` is set when the head of the line still
 * cannot start — slots are only one of three things a claim needs, so a free
 * one beside a stalled line must not read as room to start something.
 */
function EmptySlot({ index, blockedBy }: { index: number; blockedBy: string | null }) {
  return (
    <div className="queue-slot" data-testid={`docker-slot-free-${index}`}>
      <div className="queue-slot-head">
        <span className="queue-slot-dot" aria-hidden />
        <span className="queue-slot-name">Free</span>
        <span className="queue-slot-badge" data-run-status="available">
          {blockedBy ? "no room" : "available"}
        </span>
      </div>
      <div className="queue-slot-body">
        <div className="queue-slot-title">{blockedBy ? "Free slot, no room" : "Available"}</div>
        {/* Wraps: the reason is the point, and the lane board's one-line
            ellipsis cut it at "3 cpus fre…". */}
        <div className="queue-slot-sub docker-slot-reason">
          {blockedBy
            ? `The next claim ${blockedBy}`
            : "Reserve before a test run, build or container stack"}
        </div>
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
      <div className="queue-panel">
        <div className="queue-panel-head">
          <div className="queue-panel-title">Machine capacity</div>
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
      <div className="queue-panel">
        <div className="queue-panel-head">
          <div className="queue-panel-title">Machine capacity</div>
          {headerSlot}
        </div>
        <div className="queue-idle">
          The capacity ledger is switched off. Nothing is being tracked or limited.
        </div>
      </div>
    );
  }

  const { host, holders, waiting } = status;

  // Slots are the host pool's lease ceiling. Free slots come from the pool's
  // own booked count, not from the holder list: a nested lease or an agent
  // run's standing claim is a holder that books no slot, so subtracting holders
  // would hide a free slot for each of them.
  // An unmeasured ceiling has no slot count to draw, so the grid is skipped
  // entirely rather than rendered as zero slots — which would read as "the
  // machine is full" when it means "nobody has looked".
  const slotCeiling = host.ceiling.leases;
  const freeSlots = Math.max(0, slotCeiling - host.in_use.leases);
  const headBlockedBy = status.head_shortfall?.length
    ? describeShortfall(status.head_shortfall)
    : null;

  return (
    <div className="queue-panel">
      <div className="queue-panel-head">
        <div className="queue-panel-title">Machine capacity</div>
        {headerSlot}
        <div className="docker-board-summary">
          {slotCeiling > 0
            ? `${host.in_use.leases} of ${slotCeiling} slots held · ${waiting.length} waiting`
            : "Capacity not measured"}
        </div>
      </div>

      <PoolMeters title="Machine" summary={host} leasesLabel="slots" />
      <PoolMeters title="Docker" summary={status} leasesLabel="leases" />

      {slotCeiling > 0 ? (
        <div className="queue-slot-grid" data-testid="docker-slot-grid">
          {holders.map((row) => (
            <HolderSlot key={row.lease_id} row={row} />
          ))}
          {Array.from({ length: freeSlots }, (_, index) => (
            <EmptySlot key={`free-${index}`} index={index} blockedBy={headBlockedBy} />
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
            ? "Nothing is holding machine capacity, and nobody is queued for it."
            : "Nobody is queued — the next claim starts immediately if it fits."}
        </div>
      ) : (
        <div className="docker-queue-list">
          {waiting.map((row, index) => (
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
                  {formatCpus(row.cpus)} · {formatMemory(row.memory_mb)} ·{" "}
                  {describePool(row)} · {row.footprint}
                </div>
                {index === 0 && headBlockedBy ? (
                  <div className="docker-queue-head-reason" data-testid="docker-head-reason">
                    Waiting: {headBlockedBy}
                  </div>
                ) : null}
              </div>
              <div className="queue-lane-item-timing">{describeWait(row)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
