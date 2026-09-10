/**
 * The rail's contract is what it refuses to claim.
 *
 * Most of these pin a case where showing a plausible number would be worse than
 * showing nothing: a wait nothing can predict, a bound presented as a forecast,
 * a ceiling that is a stale measurement, and a failed read that would otherwise
 * be indistinguishable from an idle daemon.
 */

import { render, screen, waitFor } from "@testing-library/react";

import { DockerCapacityRail } from "../DockerCapacityRail";
import type { DockerCapacityStatus, DockerLeaseRow } from "../../api/dockerTypes";

jest.mock("../../api/dockerApi", () => ({
  dockerApi: { capacity: jest.fn() },
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { dockerApi } = require("../../api/dockerApi");

function lease(overrides: Partial<DockerLeaseRow> = {}): DockerLeaseRow {
  return {
    lease_id: "lease-1",
    status: "held",
    holder_label: "e2e suite",
    holder_kind: "ad_hoc",
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
    last_probe_outcome: "",
    last_probe_error: "",
    ...overrides,
  };
}

function status(overrides: Partial<DockerCapacityStatus> = {}): DockerCapacityStatus {
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
    holders: [],
    waiting: [],
    orphaned: [],
    unverifiable: [],
    ...overrides,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe("DockerCapacityRail", () => {
  it("leads with what is free, because that is the decision being made", async () => {
    dockerApi.capacity.mockResolvedValue(status());
    render(<DockerCapacityRail />);

    // Not "40% utilised": the reader is deciding whether to start something.
    expect(await screen.findByText("3 cpus")).toBeInTheDocument();
    expect(screen.getByText("free now")).toBeInTheDocument();
  });

  it("says a wait is unknown rather than guessing at one", async () => {
    dockerApi.capacity.mockResolvedValue(
      status({
        waiting: [lease({ lease_id: "w1", status: "waiting", position: 1 })],
      }),
    );
    render(<DockerCapacityRail />);

    // The backend refuses to invent this number; rendering a 0 would undo that.
    expect(await screen.findByText("wait unknown")).toBeInTheDocument();
  });

  it("marks a TTL-derived wait as a bound, not a forecast", async () => {
    dockerApi.capacity.mockResolvedValue(
      status({
        waiting: [
          lease({
            lease_id: "w1",
            status: "waiting",
            position: 1,
            estimated_wait_seconds: 900,
            estimate_basis: "ttl_bound",
          }),
          lease({
            lease_id: "w2",
            status: "waiting",
            position: 2,
            estimated_wait_seconds: 360,
            estimate_basis: "history",
          }),
        ],
      }),
    );
    render(<DockerCapacityRail />);

    // "≤ 15m" is a ceiling the holder will probably beat; "≈ 6m" is a median of
    // what these leases actually cost. Rendering both the same way would present
    // the first as a prediction.
    expect(await screen.findByText("≤ 15m")).toBeInTheDocument();
    expect(screen.getByText("≈ 6m")).toBeInTheDocument();
  });

  it("says when the ceiling is a stale measurement", async () => {
    dockerApi.capacity.mockResolvedValue(
      status({
        ceiling: {
          ...status().ceiling,
          source: "stale_probe",
          error: "Cannot connect to the Docker daemon",
        },
      }),
    );
    render(<DockerCapacityRail />);

    expect(await screen.findByText(/not answering/i)).toBeInTheDocument();
    expect(screen.getByText(/Cannot connect to the Docker daemon/)).toBeInTheDocument();
  });

  it("says reservations are being refused when nothing has been measured", async () => {
    dockerApi.capacity.mockResolvedValue(
      status({
        ceiling: { ...status().ceiling, cpus: 0, memory_mb: 0, leases: 0, source: "unknown" },
        in_use: { cpus: 0, memory_mb: 0, leases: 0 },
        available: { cpus: 0, memory_mb: 0, leases: 0 },
      }),
    );
    render(<DockerCapacityRail />);

    expect(await screen.findByText(/never been measured/i)).toBeInTheDocument();
    // And the meters must not read as an idle machine.
    expect(screen.getAllByText("not measured").length).toBeGreaterThan(0);
  });

  it("keeps a failed read apart from an idle daemon", async () => {
    dockerApi.capacity.mockRejectedValue(new Error("boom"));
    render(<DockerCapacityRail />);

    // An empty holders list and a failed request look identical if you only
    // check the list length — the mistake the Review tab already made.
    await waitFor(() => expect(screen.getByText(/boom|Failed to read/i)).toBeInTheDocument());
    expect(screen.queryByText("Nothing is holding docker capacity.")).not.toBeInTheDocument();
  });

  it("gives both lists a meaningful empty state", async () => {
    dockerApi.capacity.mockResolvedValue(status());
    render(<DockerCapacityRail />);

    expect(await screen.findByText("Nothing is holding docker capacity.")).toBeInTheDocument();
    expect(screen.getByText("Nobody is queued for capacity.")).toBeInTheDocument();
  });

  it("names an orphaned or unverified lease in words, not only in colour", async () => {
    dockerApi.capacity.mockResolvedValue(
      status({
        holders: [
          lease({ lease_id: "h1", status: "orphaned", compose_project: "myapp" }),
          lease({
            lease_id: "h2",
            last_probe_outcome: "daemon_unreachable",
            last_probe_error: "down",
          }),
        ],
        orphaned: ["h1"],
      }),
    );
    render(<DockerCapacityRail />);

    expect(await screen.findByText("orphaned")).toBeInTheDocument();
    expect(screen.getByText("unverified")).toBeInTheDocument();
  });

  it("says so plainly when the ledger is switched off", async () => {
    dockerApi.capacity.mockResolvedValue(status({ enabled: false }));
    render(<DockerCapacityRail />);

    expect(await screen.findByText(/switched off/i)).toBeInTheDocument();
  });
});
