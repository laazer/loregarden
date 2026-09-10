/**
 * The docker capacity ledger, as the board reads it.
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

export interface DockerLeaseRow {
  lease_id: string;
  status: "waiting" | "held" | "released" | "orphaned";
  holder_label: string;
  holder_kind: string;
  agent_run_id: string | null;
  ticket_id: string | null;
  footprint: string;
  cpus: number;
  memory_mb: number;
  compose_project: string;
  container_names: string[];
  /** Place in line, or null for a holder. */
  position: number | null;
  expires_at: string | null;
  expires_in_seconds: number | null;
  /** Null when nothing on record can predict it — unknown, not soon. */
  estimated_wait_seconds: number | null;
  estimate_basis: EstimateBasis;
  poll_count: number;
  last_probe_outcome: string;
  last_probe_error: string;
}

export interface DockerUnverifiable {
  lease_id: string;
  outcome: string;
  error: string;
}

export interface DockerCapacityStatus {
  enabled: boolean;
  ceiling: DockerCeiling;
  in_use: DockerCapacityAmounts;
  available: DockerCapacityAmounts;
  holders: DockerLeaseRow[];
  waiting: DockerLeaseRow[];
  /** Lease ids expired with containers still running. */
  orphaned: string[];
  /** Leases the reaper could not verify, and why. */
  unverifiable: DockerUnverifiable[];
}
