/**
 * The docker queue drawn as a board, and what it must not imply.
 *
 * Two of these pin structure rather than text, because the structure is the
 * claim: slots stand for the lease ceiling, so an unmeasured ceiling must draw
 * NO grid rather than zero slots (zero slots reads as "the machine is full"),
 * and the waiting list is ONE shared line rather than a queue per slot, because
 * capacity is a single pool and a per-slot queue would imply a choice of line
 * that does not exist.
 */

import { render, screen, within } from "@testing-library/react";

import { DockerQueueBoard } from "../DockerQueueBoard";
import type { DockerCapacityStatus, DockerLeaseRow } from "../../api/dockerTypes";

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
    running_container_count: null,
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

const idle = { error: "", loading: false };

describe("DockerQueueBoard", () => {
  it("draws one slot per lease in the ceiling, filled or free", () => {
    render(<DockerQueueBoard status={status({ holders: [lease()] })} {...idle} />);

    const grid = screen.getByTestId("docker-slot-grid");
    // One holder plus three free = the ceiling of four.
    expect(within(grid).getAllByText(/holding|available/).length).toBe(4);
    expect(screen.getByTestId("docker-slot-lease-1")).toBeInTheDocument();
    expect(screen.getByTestId("docker-slot-free-0")).toBeInTheDocument();
  });

  it("draws no grid at all when the ceiling was never measured", () => {
    // Zero slots would read as "the machine is full"; the truth is that nobody
    // has looked, and the meters say so instead.
    render(
      <DockerQueueBoard
        status={status({
          ceiling: { ...status().ceiling, cpus: 0, memory_mb: 0, leases: 0, source: "unknown" },
          in_use: { cpus: 0, memory_mb: 0, leases: 0 },
        })}
        {...idle}
      />,
    );

    expect(screen.queryByTestId("docker-slot-grid")).not.toBeInTheDocument();
    expect(screen.getByText("Capacity not measured")).toBeInTheDocument();
    expect(screen.getAllByText("not measured").length).toBeGreaterThan(0);
  });

  it("keeps the waiting line shared rather than one queue per slot", () => {
    render(
      <DockerQueueBoard
        status={status({
          holders: [lease()],
          waiting: [
            lease({ lease_id: "w1", status: "waiting", position: 1 }),
            lease({ lease_id: "w2", status: "waiting", position: 2 }),
          ],
        })}
        {...idle}
      />,
    );

    // Both waiters live outside the slot grid: capacity is one pool, so the next
    // claim starts wherever room appears.
    const grid = screen.getByTestId("docker-slot-grid");
    expect(within(grid).queryByTestId("docker-waiting-w1")).not.toBeInTheDocument();
    expect(screen.getByTestId("docker-waiting-w1")).toBeInTheDocument();
    expect(screen.getByTestId("docker-waiting-w2")).toBeInTheDocument();
    expect(screen.getByText(/one shared line/i)).toBeInTheDocument();
  });

  it("shows position and marks a bound apart from a forecast", () => {
    render(
      <DockerQueueBoard
        status={status({
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
        })}
        {...idle}
      />,
    );

    expect(screen.getByText("starts in ≤ 15m")).toBeInTheDocument();
    expect(screen.getByText("starts in ≈ 6m")).toBeInTheDocument();
    expect(within(screen.getByTestId("docker-waiting-w2")).getByText("2")).toBeInTheDocument();
  });

  it("says a wait is unknown rather than guessing", () => {
    render(
      <DockerQueueBoard
        status={status({ waiting: [lease({ lease_id: "w1", status: "waiting", position: 1 })] })}
        {...idle}
      />,
    );
    expect(screen.getByText("wait unknown")).toBeInTheDocument();
  });

  it("distinguishes an idle pool from a busy one with nobody queued", () => {
    const { rerender } = render(<DockerQueueBoard status={status()} {...idle} />);
    expect(screen.getByText(/Nothing is holding docker capacity/)).toBeInTheDocument();

    rerender(<DockerQueueBoard status={status({ holders: [lease()] })} {...idle} />);
    expect(screen.getByText(/next claim starts immediately if it fits/)).toBeInTheDocument();
  });

  it("names an orphaned or unverified holder in words", () => {
    render(
      <DockerQueueBoard
        status={status({
          holders: [
            lease({ lease_id: "h1", status: "orphaned" }),
            lease({
              lease_id: "h2",
              last_probe_outcome: "daemon_unreachable",
              last_probe_error: "down",
            }),
          ],
        })}
        {...idle}
      />,
    );

    expect(screen.getByText("orphaned")).toBeInTheDocument();
    expect(screen.getByText("unverified")).toBeInTheDocument();
  });
});
