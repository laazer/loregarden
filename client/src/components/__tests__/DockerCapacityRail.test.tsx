/**
 * The rail is the summary beside the board, not the queue itself.
 *
 * Its whole job is the ceiling and how much it can be trusted, so the tests
 * that matter are the ones where a number is less solid than it looks: a stale
 * measurement, a machine nobody has measured, and a lease the reaper could not
 * verify. A failed read is also kept apart from an idle daemon — they are
 * indistinguishable if you only check whether the lists came back empty.
 */

import { render, screen } from "@testing-library/react";

import { DockerCapacityRail } from "../DockerCapacityRail";
import type { DockerCapacityStatus } from "../../api/dockerTypes";

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

const idle = { error: "", loading: false };

describe("DockerCapacityRail", () => {
  it("leads with what is free, because that is the decision being made", () => {
    render(<DockerCapacityRail status={status()} {...idle} />);
    // The machine is what every claim waits on; docker's room is a tile.
    expect(screen.getByText("10 cpus")).toBeInTheDocument();
    expect(screen.getByText("free on this machine")).toBeInTheDocument();
    expect(screen.getByText("3 cpus")).toBeInTheDocument();
  });

  it("counts holders and waiters but points at the board for the queue itself", () => {
    render(<DockerCapacityRail status={status()} {...idle} />);
    expect(screen.getByText("Slots held")).toBeInTheDocument();
    expect(screen.getByText("Waiting")).toBeInTheDocument();
    // The queue belongs on the board; an earlier cut squeezed it in here.
    expect(screen.getByText(/on the board/i)).toBeInTheDocument();
  });

  it("says when the ceiling is a stale measurement", () => {
    render(
      <DockerCapacityRail
        status={status({
          ceiling: {
            ...status().ceiling,
            source: "stale_probe",
            error: "Cannot connect to the Docker daemon",
          },
        })}
        {...idle}
      />,
    );
    expect(screen.getByText(/not answering/i)).toBeInTheDocument();
    expect(screen.getByText(/Cannot connect to the Docker daemon/)).toBeInTheDocument();
  });

  it("says reservations are being refused when nothing has been measured", () => {
    render(
      <DockerCapacityRail
        status={status({ ceiling: { ...status().ceiling, source: "unknown" } })}
        {...idle}
      />,
    );
    expect(screen.getByText(/docker reservations are being refused/i)).toBeInTheDocument();
  });

  it("says every reservation is refused when the machine was never measured", () => {
    // The host pool backs every claim, so its unknown ceiling is the louder
    // caveat, and the headline is a dash rather than a plausible zero.
    const base = status();
    render(
      <DockerCapacityRail
        status={status({
          host: {
            ...base.host,
            ceiling: { ...base.host.ceiling, cpus: 0, leases: 0, source: "unknown" },
            available: { cpus: 0, memory_mb: 0, leases: 0 },
          },
        })}
        {...idle}
      />,
    );
    expect(screen.getByText(/every reservation is being refused/i)).toBeInTheDocument();
    expect(screen.queryByText("0 cpus")).not.toBeInTheDocument();
  });

  it("surfaces leases the reaper could not verify", () => {
    render(
      <DockerCapacityRail
        status={status({
          unverifiable: [{ lease_id: "h1", outcome: "daemon_unreachable", error: "down" }],
        })}
        {...idle}
      />,
    );
    expect(screen.getByText(/could not be verified/i)).toBeInTheDocument();
  });

  it("keeps a failed read apart from an idle daemon", () => {
    render(<DockerCapacityRail status={null} error="boom" loading={false} />);
    expect(screen.getByText("boom")).toBeInTheDocument();
    expect(screen.queryByText("free on this machine")).not.toBeInTheDocument();
  });

  it("says so plainly when the ledger is switched off", () => {
    render(<DockerCapacityRail status={status({ enabled: false })} {...idle} />);
    expect(screen.getByText(/switched off/i)).toBeInTheDocument();
  });

  it("shows a wait rather than an empty panel while the first read is in flight", () => {
    render(<DockerCapacityRail status={null} error="" loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Reading the ledger");
  });
});
