/**
 * The machine-capacity queue, drawn the way the lane board draws the agent queue.
 *
 * This is the second pool on the machine. The lanes beside it ration how many
 * agents run at once; this rations the CPU and memory their test runs, builds
 * and containers take. Every lease is charged to the host pool, and a docker
 * claim to the docker pool as well. The question is the same shape — what is
 * occupied, what is behind it, and how long until my turn. So it uses the same
 * vocabulary: the holders across the top, one line counting the free slots,
 * and the line waiting underneath. Free slots are counted rather than drawn —
 * a card per empty slot pushed the line below the fold.
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

import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { dockerApi } from "../api/dockerApi";
import type {
  CapacityPoolSummary,
  CapacityShortfall,
  DockerCapacityStatus,
  DockerLeaseRow,
} from "../api/dockerTypes";
import { ticketPath } from "../lib/appNavigation";
import { useAgentAction } from "../lib/agentActions/useAgentAction";
import { describeError, pushToast } from "../state/toastStore";
import { DockerLeaseReleaseDialog } from "./DockerLeaseReleaseDialog";
import { Button } from "./ui/Button";
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

/** "heavy · 4 cpus · 8 GB" — the footprint once, with what it costs. */
function describeCost(row: DockerLeaseRow): string {
  return `${row.footprint} · ${formatCpus(row.cpus)} · ${formatMemory(row.memory_mb)}`;
}

/**
 * What is running, then where, then — quietly — which process. The raw label
 * repeated the worktree inside the branch name; the server splits it, and the
 * worktree only appears when it says something the branch does not.
 */
function HolderIdentity({
  row,
  titleClass,
  detail,
}: {
  row: DockerLeaseRow;
  titleClass: string;
  /** What it costs, on the same line as where it runs — one line, not three. */
  detail: string;
}) {
  const { what, branch, worktree, pid } = row.holder;
  return (
    <>
      <div className={titleClass} title={row.holder_label}>
        {what || "unlabelled"}
      </div>
      <div className="docker-holder-where">
        {branch ? <span className="docker-holder-branch">{branch}</span> : null}
        {worktree ? <span className="docker-holder-worktree">in {worktree}</span> : null}
        <span>{detail}</span>
        {pid ? <span className="docker-holder-pid">pid {pid}</span> : null}
      </div>
    </>
  );
}

/**
 * Where to go from a lease, and how to end it. The ticket link only exists
 * when the lease names one (a stage's lease names its run, which the server
 * resolves to the run's ticket); most ad-hoc leases name neither.
 */
function LeaseActions({
  row,
  pending,
  onEnd,
}: {
  row: DockerLeaseRow;
  pending: boolean;
  onEnd: (row: DockerLeaseRow) => void;
}) {
  const waiting = row.status === "waiting";
  // The branch makes the name unique: a line of seven is mostly "pre-push
  // client-tests", and seven identical button names cannot be told apart.
  const what = `${row.holder.what || "unlabelled lease"}${row.holder.branch ? ` on ${row.holder.branch}` : ""}`;
  return (
    <div className="docker-lease-actions">
      {row.ticket_id ? (
        <Link
          className="docker-lease-link"
          to={ticketPath(row.ticket_id, row.agent_run_id ? "logs" : "diff")}
        >
          {row.agent_run_id ? "Open run" : "Open ticket"}
        </Link>
      ) : null}
      <Button
        variant="secondary"
        compact
        disabled={pending}
        aria-label={`${waiting ? "Drop from line" : "Release"}: ${what}`}
        onClick={() => onEnd(row)}
      >
        {pending ? "Ending…" : waiting ? "Drop" : "Release"}
      </Button>
    </div>
  );
}

function HolderSlot({
  row,
  pending,
  onEnd,
}: {
  row: DockerLeaseRow;
  pending: boolean;
  onEnd: (row: DockerLeaseRow) => void;
}) {
  const status = holderStatus(row);
  return (
    <div className="queue-slot queue-slot--busy" data-testid={`docker-slot-${row.lease_id}`}>
      <div className="queue-slot-head">
        <span className="queue-slot-dot" aria-hidden />
        <span className="queue-slot-name">{describePool(row)}</span>
        <span className="queue-slot-badge" data-run-status={status}>
          {status}
        </span>
      </div>
      <div className="queue-slot-body">
        <HolderIdentity
          row={row}
          titleClass="queue-slot-title"
          detail={`${describeCost(row)}${row.compose_project ? ` · ${row.compose_project}` : ""}`}
        />
        {row.last_probe_error ? (
          <div className="docker-slot-error" title={row.last_probe_error}>
            {row.last_probe_error}
          </div>
        ) : null}
        <div className="docker-slot-foot">
          <span className="docker-slot-expiry">
            {row.expires_in_seconds === null
              ? "no expiry"
              : `expires in ${formatDuration(row.expires_in_seconds)}`}
          </span>
          <LeaseActions row={row} pending={pending} onEnd={onEnd} />
        </div>
      </div>
    </div>
  );
}

/**
 * The free slots, as one line rather than a card each. Six identical "Free
 * slot" cards pushed the waiting line — what the operator came to see — below
 * the fold. When the head of the line still cannot start, the line says why:
 * a slot is one of three things a claim needs, so free slots beside a stalled
 * line must not read as room.
 */
function FreeSlots({
  free,
  ceiling,
  blockedBy,
}: {
  free: number;
  ceiling: number;
  blockedBy: string | null;
}) {
  const count = free === 0 ? `All ${ceiling} slots held` : `${free} of ${ceiling} slots free`;
  return (
    <div className="docker-free-slots" data-testid="docker-free-slots">
      <span className="docker-free-slots-count">{count}</span>
      {free > 0 && blockedBy ? (
        <span className="docker-free-slots-note">— no room yet: the next claim {blockedBy}</span>
      ) : null}
    </div>
  );
}

/**
 * How long a waiter has queued, and whether it is still asking. Age alone
 * cannot tell a stuck waiter from a patient one at the back of a long line;
 * a waiter that stopped polling has most likely lost its process, and the
 * sweep drops it at `drops_in_seconds` unless it polls again.
 */
function WaitingAge({ row }: { row: DockerLeaseRow }) {
  if (row.waiting_seconds === null) return null;
  return (
    <span className="docker-waiting-age">
      waiting {formatDuration(row.waiting_seconds)}
      {row.poll_stalled ? (
        <span className="docker-waiting-stalled" data-testid={`docker-stalled-${row.lease_id}`}>
          {" "}
          · no poll for {formatDuration(row.last_seen_seconds_ago ?? 0)}
          {row.drops_in_seconds !== null
            ? `, dropped in ${formatDuration(row.drops_in_seconds)} unless it polls`
            : ""}
        </span>
      ) : null}
    </span>
  );
}

/**
 * Ending a lease from the board: confirm, send once, refresh, and say what
 * happened. Also offered to agents as `capacity.release` while the board is on
 * screen, through the same call.
 */
function useLeaseRelease(status: DockerCapacityStatus | null, onChanged: () => void) {
  const [confirming, setConfirming] = useState<DockerLeaseRow | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);

  const release = async (leaseId: string, reason: string) => {
    setPendingId(leaseId);
    try {
      return await dockerApi.releaseLease(leaseId, reason);
    } finally {
      setPendingId(null);
      onChanged();
    }
  };

  useAgentAction("capacity.release", async ({ lease_id, reason }) => {
    const listed = [...(status?.holders ?? []), ...(status?.waiting ?? [])];
    if (!listed.some((row) => row.lease_id === lease_id)) {
      throw new Error(`lease ${lease_id} is not on the Machine board`);
    }
    return release(lease_id, reason);
  });

  const confirm = async (reason: string) => {
    if (!confirming || pendingId) return;
    const row = confirming;
    try {
      const result = await release(row.lease_id, reason);
      setConfirming(null);
      if (!result.released) {
        pushToast({
          tone: "info",
          title: "Already ended",
          message: `${row.holder.what || "That lease"} had ended before this reached it.`,
        });
      }
    } catch (error) {
      pushToast({
        tone: "error",
        title: row.status === "waiting" ? "Could not drop from the line" : "Could not release",
        message: describeError(error, "The capacity ledger did not answer; try again."),
      });
    }
  };

  return { confirming, setConfirming, pendingId, confirm };
}

export function DockerQueueBoard({
  status,
  error,
  loading,
  headerSlot,
  onChanged,
}: {
  status: DockerCapacityStatus | null;
  error: string;
  loading: boolean;
  headerSlot?: ReactNode;
  /** Re-read the ledger after the board changed it. */
  onChanged: () => void;
}) {
  const { confirming, setConfirming, pendingId, confirm } = useLeaseRelease(status, onChanged);

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

      {holders.length > 0 ? (
        <div className="queue-slot-grid docker-holder-grid" data-testid="docker-slot-grid">
          {holders.map((row) => (
            <HolderSlot
              key={row.lease_id}
              row={row}
              pending={pendingId === row.lease_id}
              onEnd={setConfirming}
            />
          ))}
        </div>
      ) : null}
      {slotCeiling > 0 ? (
        <FreeSlots free={freeSlots} ceiling={slotCeiling} blockedBy={headBlockedBy} />
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
                <HolderIdentity
                  row={row}
                  titleClass="queue-lane-item-title"
                  detail={`${describePool(row)} · ${describeCost(row)}`}
                />
                {index === 0 && headBlockedBy ? (
                  <div className="docker-queue-head-reason" data-testid="docker-head-reason">
                    Waiting: {headBlockedBy}
                  </div>
                ) : null}
              </div>
              <div className="docker-waiting-side">
                <div className="queue-lane-item-timing">{describeWait(row)}</div>
                <WaitingAge row={row} />
              </div>
              <LeaseActions
                row={row}
                pending={pendingId === row.lease_id}
                onEnd={setConfirming}
              />
            </div>
          ))}
        </div>
      )}

      {confirming ? (
        <DockerLeaseReleaseDialog
          lease={confirming}
          inFlight={pendingId === confirming.lease_id}
          onClose={() => setConfirming(null)}
          onConfirm={(reason) => void confirm(reason)}
        />
      ) : null}
    </div>
  );
}
