/**
 * The machine capacity ledger — host and docker pools — as the board reads it.
 *
 * Mirrors `services/docker_board.capacity_status`. Own module rather than more
 * of `types.ts`, which is 1142 lines against a 1200 cap — a file this feature
 * would push over, making its whole size ours to solve.
 *
 * The nullable fields are the point of the shape. `estimated_wait_seconds` is
 * null when nothing on record can predict a wait, and `estimate_basis` says
 * whether a number that IS present is a measurement or a ceiling. Both exist so
 * the UI can decline to state something it does not know, rather than rendering
 * a plausible zero.
 */

/** Where the enforced ceiling came from, and therefore how much it is worth. */
export type CeilingSource = "probe" | "stale_probe" | "config_override" | "unknown";

/** Whether a projected wait is a measurement or an upper bound. */
export type EstimateBasis = "history" | "ttl_bound" | "unknown";

export interface DockerCeiling {
  cpus: number;
  memory_mb: number;
  leases: number;
  source: CeilingSource;
  /** ISO-8601, or null when the machine has never been measured. */
  probed_at: string | null;
  /** Why the last probe failed. Empty when it did not. */
  error: string;
}

export interface DockerCapacityAmounts {
  cpus: number;
  memory_mb: number;
  leases: number;
}

/**
 * Which pool a lease claims from. They nest: every lease is charged to `host`
 * (the machine), and a `docker` lease to the Docker VM's pool as well.
 */
export type CapacityPool = "docker" | "host";

/**
 * A lease's label read back as what / where / pid. `holder_label` stays the raw
 * string; this is the same thing split by the server, which still reads the
 * older `worktree@branch` labels that repeated the branch.
 */
export interface HolderLabel {
  what: string;
  branch: string | null;
  /** Only when it says something the branch does not. */
  worktree: string | null;
  pid: number | null;
}

export interface DockerLeaseRow {
  lease_id: string;
  status: "waiting" | "held" | "released" | "orphaned";
  holder_label: string;
  holder: HolderLabel;
  holder_kind: string;
  pool: CapacityPool;
  /** Set on a nested lease, which draws on its parent's grant and books no slot. */
  parent_lease_id: string | null;
  agent_run_id: string | null;
  ticket_id: string | null;
  footprint: string;
  cpus: number;
  memory_mb: number;
  compose_project: string;
  container_names: string[];
  /** Place in line, from 1, for a waiter; null for a holder. */
  position: number | null;
  expires_at: string | null;
  expires_in_seconds: number | null;
  /** Null when nothing on record can predict it — unknown, not soon. */
  estimated_wait_seconds: number | null;
  estimate_basis: EstimateBasis;
  poll_count: number;
  /** ISO-8601: when the claim was made. */
  requested_at: string | null;
  /** How long a waiter has been in line; null for a holder. */
  waiting_seconds: number | null;
  /** Since a waiter last polled (or was queued); null for a holder. */
  last_seen_seconds_ago: number | null;
  /** A waiter that has stopped polling — its process has most likely gone. */
  poll_stalled: boolean;
  /** When the abandonment sweep drops a waiter that does not poll again. */
  drops_in_seconds: number | null;
  /** How many of its containers the last probe found running. */
  running_container_count: number | null;
  last_probe_outcome: string;
  last_probe_error: string;
}

/** One dimension a claim must fit in. */
export type CapacityResource = "cpus" | "memory_mb" | "slots";

/** One way the head of the line does not fit: what it needs, what is free. */
export interface CapacityShortfall {
  pool: CapacityPool;
  resource: CapacityResource;
  needed: number;
  free: number;
}

export interface DockerUnverifiable {
  lease_id: string;
  outcome: string;
  error: string;
}

/** One pool's ceiling and what is booked against it. */
export interface CapacityPoolSummary {
  ceiling: DockerCeiling;
  in_use: DockerCapacityAmounts;
  available: DockerCapacityAmounts;
}

/**
 * The top level is the docker pool, as it was before the host pool existed;
 * `host` is the machine, which docker claims are charged to too.
 */
export interface DockerCapacityStatus extends CapacityPoolSummary {
  enabled: boolean;
  host: CapacityPoolSummary;
  /**
   * What the head of the line is short of, from admission's own test. Null
   * with nobody waiting; empty when the head fits and is about to start.
   */
  head_shortfall: CapacityShortfall[] | null;
  holders: DockerLeaseRow[];
  waiting: DockerLeaseRow[];
  /** Lease ids expired with containers still running. */
  orphaned: string[];
  /** Leases the reaper could not verify, and why. */
  unverifiable: DockerUnverifiable[];
}

/** What ending a lease did. `released` is false when it had already ended. */
export interface ForceReleaseResult {
  lease_id: string;
  released: boolean;
}
