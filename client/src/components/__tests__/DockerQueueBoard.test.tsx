/**
 * The docker queue drawn as a board, and what it must not imply.
 *
 * Two of these pin structure rather than text, because the structure is the
 * claim: slots stand for the lease ceiling, so an unmeasured ceiling must draw
 * NO grid rather than zero slots (zero slots reads as "the machine is full"),
 * and the waiting list is ONE shared line rather than a queue per slot, because
 * capacity is one shared line and a per-slot queue would imply a choice of line
 * that does not exist.
 */

import { act, render as rtlRender, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";

import { DockerQueueBoard } from "../DockerQueueBoard";
import { dockerApi } from "../../api/dockerApi";
import type { DockerCapacityStatus, DockerLeaseRow } from "../../api/dockerTypes";
import { uiActionRegistry } from "../../lib/agentActions/registry";
import { pushToast } from "../../state/toastStore";

jest.mock("../../api/dockerApi", () => ({
  dockerApi: { capacity: jest.fn(), releaseLease: jest.fn() },
}));
jest.mock("../../state/toastStore", () => ({
  ...jest.requireActual("../../state/toastStore"),
  pushToast: jest.fn(),
}));

const releaseLease = dockerApi.releaseLease as jest.Mock;

/** The board links to tickets, so it renders inside a router. */
function render(ui: ReactElement) {
  const view = rtlRender(<MemoryRouter>{ui}</MemoryRouter>);
  return {
    ...view,
    rerender: (next: ReactElement) => view.rerender(<MemoryRouter>{next}</MemoryRouter>),
  };
}

function lease(overrides: Partial<DockerLeaseRow> = {}): DockerLeaseRow {
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

const onChanged = jest.fn();
const idle = { error: "", loading: false, onChanged };

beforeEach(() => {
  releaseLease.mockReset();
  onChanged.mockReset();
  (pushToast as jest.Mock).mockReset();
});

describe("DockerQueueBoard", () => {
  it("draws a card per holder and counts the free slots on one line", () => {
    // A card per free slot pushed the waiting line below the fold.
    render(<DockerQueueBoard status={status({ holders: [lease()] })} {...idle} />);

    const grid = screen.getByTestId("docker-slot-grid");
    expect(grid.children).toHaveLength(1);
    expect(screen.getByTestId("docker-slot-lease-1")).toBeInTheDocument();
    expect(screen.getByTestId("docker-free-slots")).toHaveTextContent("3 of 4");
  });

  it("draws no holder grid on an idle machine, but still counts its slots", () => {
    render(<DockerQueueBoard status={status({ host: { ...status().host, in_use: { cpus: 0, memory_mb: 0, leases: 0 } } })} {...idle} />);
    expect(screen.queryByTestId("docker-slot-grid")).not.toBeInTheDocument();
    expect(screen.getByTestId("docker-free-slots")).toHaveTextContent("4 of 4");
  });

  it("draws no grid at all when the ceiling was never measured", () => {
    // Zero slots would read as "the machine is full"; the truth is that nobody
    // has looked, and the meters say so instead.
    render(
      <DockerQueueBoard
        status={status({
          host: {
            ...status().host,
            ceiling: {
              ...status().host.ceiling,
              cpus: 0,
              memory_mb: 0,
              leases: 0,
              source: "unknown",
            },
            in_use: { cpus: 0, memory_mb: 0, leases: 0 },
          },
        })}
        {...idle}
      />,
    );

    expect(screen.queryByTestId("docker-free-slots")).not.toBeInTheDocument();
    expect(screen.getByText("Capacity not measured")).toBeInTheDocument();
    expect(screen.getAllByText("not measured").length).toBeGreaterThan(0);
  });

  it("counts free slots from the pool, so a nested lease hides none", () => {
    // A nested lease draws on its parent and books no slot. Subtracting the
    // holder list from the ceiling would show two free slots here, not three.
    const parent = lease({ lease_id: "parent", pool: "host", footprint: "heavy" });
    const child = lease({ lease_id: "child", parent_lease_id: "parent" });
    render(<DockerQueueBoard status={status({ holders: [parent, child] })} {...idle} />);

    expect(screen.getByTestId("docker-free-slots")).toHaveTextContent("3 of 4");
    expect(within(screen.getByTestId("docker-slot-parent")).getByText(/machine/)).toBeTruthy();
    expect(within(screen.getByTestId("docker-slot-child")).getByText(/nested/)).toBeTruthy();
  });

  it("meters the machine and the docker pool separately", () => {
    render(<DockerQueueBoard status={status()} {...idle} />);
    expect(screen.getByRole("meter", { name: "Machine CPU" })).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "Docker CPU" })).toBeInTheDocument();
  });

  it("says what the head of the line is short of, and that free slots are no use to it", () => {
    // A free slot is one of three things a claim needs. Beside a stalled line,
    // "Available" told people the machine had room it did not have.
    render(
      <DockerQueueBoard
        status={status({
          holders: [lease()],
          waiting: [lease({ lease_id: "w1", status: "waiting", position: 1, cpus: 4 })],
          head_shortfall: [{ pool: "host", resource: "cpus", needed: 4, free: 3 }],
        })}
        {...idle}
      />,
    );

    expect(screen.getByTestId("docker-head-reason")).toHaveTextContent(
      "needs 4 cpus, 3 cpus free on the machine",
    );
    // The free-slot line carries the same reason, so it cannot read as room.
    expect(screen.getByTestId("docker-free-slots")).toHaveTextContent(
      "needs 4 cpus, 3 cpus free on the machine",
    );
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
    expect(screen.getByText(/Nothing is holding machine capacity/)).toBeInTheDocument();

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

  it("shows the footprint once on a holder card", () => {
    render(<DockerQueueBoard status={status({ holders: [lease({ footprint: "heavy" })] })} {...idle} />);
    const card = screen.getByTestId("docker-slot-lease-1");
    expect(within(card).getAllByText(/heavy/)).toHaveLength(1);
  });

  it("names what runs, the branch once, and the pid apart", () => {
    const row = lease({
      holder_label: "pre-push client-tests · lg-x-e33a13@claude/lg-x-e33a13 · pid 22339",
      holder: { what: "pre-push client-tests", branch: "claude/lg-x-e33a13", worktree: null, pid: 22339 },
    });
    render(<DockerQueueBoard status={status({ holders: [row] })} {...idle} />);

    const card = screen.getByTestId("docker-slot-lease-1");
    expect(within(card).getAllByText(/lg-x-e33a13/)).toHaveLength(1);
    expect(within(card).getByText("pid 22339")).toBeInTheDocument();
    expect(within(card).queryByText(/in lg-x-e33a13/)).not.toBeInTheDocument();
  });

  it("shows how far a holder has got, and how long it has run", () => {
    const row = lease({
      held_seconds: 250,
      progress: {
        step: "pytest on 3 file(s) (5/5)",
        done: 412,
        total: 1830,
        summary: "pytest on 3 file(s) (5/5) · 412/1830 (22%)",
        reported_at: null,
      },
    });
    render(<DockerQueueBoard status={status({ holders: [row] })} {...idle} />);

    const card = screen.getByTestId("docker-slot-lease-1");
    expect(within(card).getByText("pytest on 3 file(s) (5/5) · 412/1830 (22%)")).toBeInTheDocument();
    const bar = within(card).getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "412");
    expect(bar).toHaveAttribute("aria-valuemax", "1830");
    expect(within(card).getByText(/running 4m/)).toBeInTheDocument();
  });

  it("draws no bar for a step with no count, and nothing for a holder that never reported", () => {
    const stepOnly = lease({
      lease_id: "lease-1",
      progress: { step: "tsc -b (2/3)", done: null, total: null, summary: "tsc -b (2/3)", reported_at: null },
    });
    const silent = lease({ lease_id: "lease-2" });
    render(<DockerQueueBoard status={status({ holders: [stepOnly, silent] })} {...idle} />);

    expect(within(screen.getByTestId("docker-slot-lease-1")).getByText("tsc -b (2/3)")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.queryByTestId("docker-progress-lease-2")).not.toBeInTheDocument();
  });

  it("links a lease to its ticket only when it names one", () => {
    render(
      <DockerQueueBoard
        status={status({
          holders: [
            lease({ lease_id: "staged", ticket_id: "t-1", agent_run_id: "r-1" }),
            lease({ lease_id: "adhoc" }),
          ],
        })}
        {...idle}
      />,
    );

    expect(within(screen.getByTestId("docker-slot-staged")).getByRole("link")).toHaveAttribute(
      "href",
      "/tickets/t-1/timeline",
    );
    expect(within(screen.getByTestId("docker-slot-adhoc")).queryByRole("link")).toBeNull();
  });

  it("shows how long a waiter has queued, and flags one that stopped polling", () => {
    render(
      <DockerQueueBoard
        status={status({
          waiting: [
            lease({ lease_id: "w1", status: "waiting", position: 1, waiting_seconds: 1500, last_seen_seconds_ago: 10 }),
            lease({
              lease_id: "w2",
              status: "waiting",
              position: 2,
              waiting_seconds: 600,
              last_seen_seconds_ago: 300,
              poll_stalled: true,
              drops_in_seconds: 300,
            }),
          ],
        })}
        {...idle}
      />,
    );

    expect(within(screen.getByTestId("docker-waiting-w1")).getByText(/25m/)).toBeInTheDocument();
    expect(screen.queryByTestId("docker-stalled-w1")).not.toBeInTheDocument();
    expect(screen.getByTestId("docker-stalled-w2")).toBeInTheDocument();
  });

  it("releases a holder only after confirming, once, then re-reads the ledger", async () => {
    let finish: (value: { lease_id: string; released: boolean }) => void = () => {};
    releaseLease.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    render(<DockerQueueBoard status={status({ holders: [lease()] })} {...idle} />);

    await userEvent.click(screen.getByRole("button", { name: /^Release: e2e suite/ }));
    expect(releaseLease).not.toHaveBeenCalled();

    const dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Release" }));
    expect(releaseLease).toHaveBeenCalledWith("lease-1", expect.any(String));
    // In flight: both the dialog's button and the row's are held.
    expect(within(dialog).getByRole("button", { name: /Ending/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^Release: e2e suite/ })).toBeDisabled();

    await act(async () => finish({ lease_id: "lease-1", released: true }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(releaseLease).toHaveBeenCalledTimes(1);
    expect(onChanged).toHaveBeenCalled();
  });

  it("drops a waiter, and says so when the ledger refuses", async () => {
    releaseLease.mockRejectedValue(new Error("database is locked"));
    render(
      <DockerQueueBoard
        status={status({ waiting: [lease({ lease_id: "w1", status: "waiting", position: 1 })] })}
        {...idle}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /^Drop from line: e2e suite/ }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Drop from line" }));

    await waitFor(() => expect(pushToast).toHaveBeenCalledWith(expect.objectContaining({ tone: "error" })));
    // The dialog stays, so the operator can try again or cancel.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("closes the confirm on Escape without releasing", async () => {
    render(<DockerQueueBoard status={status({ holders: [lease()] })} {...idle} />);
    await userEvent.click(screen.getByRole("button", { name: /^Release: e2e suite/ }));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(releaseLease).not.toHaveBeenCalled();
  });

  it("offers capacity.release to agents for a lease on the board, and refuses others", async () => {
    releaseLease.mockResolvedValue({ lease_id: "lease-1", released: true });
    const view = render(<DockerQueueBoard status={status({ holders: [lease()] })} {...idle} />);

    await act(() => uiActionRegistry.run("capacity.release", { lease_id: "lease-1", reason: "stuck" }));
    expect(releaseLease).toHaveBeenCalledWith("lease-1", "stuck");
    await expect(
      uiActionRegistry.run("capacity.release", { lease_id: "nope", reason: "x" }),
    ).rejects.toThrow("nope");

    view.unmount();
    expect(uiActionRegistry.available()).not.toContain("capacity.release");
  });
});
