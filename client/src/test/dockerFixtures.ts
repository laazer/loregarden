/** Capacity-ledger rows for tests: a held lease and a measured two-pool status. */

import type { DockerCapacityStatus, DockerLeaseRow } from "../api/dockerTypes";

export function lease(overrides: Partial<DockerLeaseRow> = {}): DockerLeaseRow {
  return {
    lease_id: "lease-1",
    status: "held",
    holder_label: "e2e suite",
    holder: { what: "e2e suite", branch: null, worktree: null, pid: null },
    holder_kind: "ad_hoc",
    pool: "docker",
    parent_lease_id: null,
    agent_run_id: null,
    ticket_id: null,
    footprint: "stack",
    cpus: 2,
    memory_mb: 4096,
    compose_project: "",
    container_names: [],
    position: null,
    expires_at: null,
    expires_in_seconds: 600,
    estimated_wait_seconds: null,
    estimate_basis: "unknown",
    poll_count: 0,
    requested_at: null,
    held_seconds: null,
    progress: null,
    waiting_seconds: null,
    last_seen_seconds_ago: null,
    poll_stalled: false,
    drops_in_seconds: null,
    running_container_count: null,
    last_probe_outcome: "",
    last_probe_error: "",
    ...overrides,
  };
}

export function capacityStatus(overrides: Partial<DockerCapacityStatus> = {}): DockerCapacityStatus {
  return {
    enabled: true,
    ceiling: {
      cpus: 5,
      memory_mb: 9942,
      leases: 4,
      source: "probe",
      probed_at: "2026-09-10T12:00:00+00:00",
      error: "",
    },
    in_use: { cpus: 2, memory_mb: 4096, leases: 1 },
    available: { cpus: 3, memory_mb: 5846, leases: 3 },
    host: {
      ceiling: {
        cpus: 12,
        memory_mb: 32768,
        leases: 4,
        source: "probe",
        probed_at: "2026-09-10T12:00:00+00:00",
        error: "",
      },
      in_use: { cpus: 2, memory_mb: 4096, leases: 1 },
      available: { cpus: 10, memory_mb: 28672, leases: 3 },
    },
    holders: [],
    waiting: [],
    head_shortfall: null,
    orphaned: [],
    unverifiable: [],
    ...overrides,
  };
}
