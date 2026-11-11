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
import { Button } from "../ui/Button";
import { dockerApi } from "../../api/dockerApi";
import type { DockerLeaseRow } from "../../api/dockerTypes";
import { capacityStatus as status, lease } from "../../test/dockerFixtures";
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
    const card = screen.getByTestId("docker-slot-parent");
    expect(within(card).getByText(/machine/)).toBeTruthy();
    // Inside its parent's card, not beside it: see R10b below. This assertion
    // read the child from a sibling card before 1923 nested it.
    expect(within(card).getByTestId("docker-child-child")).toBeTruthy();
    expect(screen.queryByTestId("docker-slot-child")).not.toBeInTheDocument();
  });

  it("meters the machine and the docker pool separately", () => {
    render(<DockerQueueBoard status={status()} {...idle} />);
    expect(screen.getByRole("meter", { name: "Machine CPU" })).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "Docker CPU" })).toBeInTheDocument();
  });

  it("shows CPU load from outside the ledger on the machine meter", () => {
    const base = status();
    render(
      <DockerQueueBoard
        status={status({
          host: {
            ...base.host,
            ceiling: { ...base.host.ceiling, cpus: 7 },
            in_use: { ...base.host.in_use, cpus: 0.25 },
            external_cpus: 9.75,
          },
        })}
        {...idle}
      />,
    );

    const machine = screen.getByRole("meter", { name: "Machine CPU" });
    expect(machine).toHaveAttribute("data-tone", "full");
    expect(machine).toHaveAttribute(
      "aria-valuetext",
      "0.3 cpus of 7 cpus, plus 9.8 cpus outside the ledger",
    );
    // The docker pool has no outside-load figure of its own.
    expect(screen.getByRole("meter", { name: "Docker CPU" }).getAttribute("aria-valuetext")).not.toMatch(
      /outside/,
    );
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
  it("hands the attention slot its own confirm, so a lease ends one way", async () => {
    releaseLease.mockResolvedValue({ lease_id: "lease-1", released: true });
    const row = lease();
    render(
      <DockerQueueBoard
        status={status({ holders: [row] })}
        {...idle}
        attention={(ending) => (
          <Button variant="secondary" onClick={() => ending.onEnd(row)} disabled={ending.pendingId === row.lease_id}>
            From the band
          </Button>
        )}
        meters={<div data-testid="extra-meter" />}
      />,
    );
    expect(screen.getByTestId("extra-meter")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "From the band" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Release" }));
    expect(releaseLease).toHaveBeenCalledWith("lease-1", expect.any(String));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("draws the attention slot while the first read is still in flight", () => {
    render(
      <DockerQueueBoard status={null} error="" loading onChanged={onChanged} attention={() => <p>band</p>} />,
    );
    expect(screen.getByText("band")).toBeInTheDocument();
  });

  // ---- lg-machine-resource-1923 R10/R11: a run's parent lease, and its children ----
  //
  // A run-scoped parent is 0.5 cpu held for the whole orchestration — 17
  // minutes on average, 116 at the tail — and for most of that nothing is using
  // it. Today its card is word-for-word identical to a working lease, so the
  // board would report reserved-and-idle capacity as scarcity. Two decisions
  // fix that, and both are structure rather than copy: the parent says it is
  // idle, and its children are drawn INSIDE it rather than beside it.

  /** A run-scoped parent: ORCHESTRATION kind, top-level, booking no slot. */
  function parentLease(overrides: Partial<DockerLeaseRow> = {}): DockerLeaseRow {
    return lease({
      lease_id: "parent-1",
      holder_kind: "orchestration",
      pool: "host",
      footprint: "light",
      cpus: 0.5,
      memory_mb: 512,
      parent_lease_id: null,
      ticket_id: "t-1923",
      holder_label: "run orch_4535af · loregarden · lg-machine-resource-1923",
      holder: {
        what: "run orch_4535af",
        branch: "loregarden/lg-machine-resource-1923",
        worktree: null,
        pid: 4242,
      },
      held_seconds: 250,
      ...overrides,
    });
  }

  function gateChild(overrides: Partial<DockerLeaseRow> = {}): DockerLeaseRow {
    return lease({
      lease_id: "child-1",
      parent_lease_id: "parent-1",
      pool: "host",
      footprint: "service",
      cpus: 1,
      memory_mb: 1024,
      holder_label: "gates plan_to_plan-synthesis · loregarden · lg-machine-resource-1923",
      holder: { what: "gates plan_to_plan-synthesis", branch: null, worktree: null, pid: 4242 },
      // Priced from the parent's 0.5-cpu grant: half covered, half booked
      // ahead of the queue. Measured off a live row, 2026-11-02.
      covered_cpus: 0.5,
      covered_memory_mb: 512,
      held_seconds: 12,
      ...overrides,
    });
  }

  it("a parent lease with no live child reads as reserved, idle", () => {
    // Not "holding". 0.5 cpu that nobody is using, drawn identically to a
    // working lease, is what makes a held lease look like scarcity — and the
    // operator's question here is "what is actually using this machine right
    // now, and what is only reserved?".
    render(<DockerQueueBoard status={status({ holders: [parentLease()] })} {...idle} />);

    const card = screen.getByTestId("docker-slot-parent-1");
    expect(within(card).getByText("reserved, idle")).toBeInTheDocument();
    expect(within(card).queryByText("holding")).not.toBeInTheDocument();
    // How long it has been idle, in the same vocabulary the rest of the card uses.
    expect(within(card).getByText(/idle 4m/)).toBeInTheDocument();
  });

  it("a parent with a live child reads as holding, not idle", () => {
    // The pair matters more than either half: a parent that always said "idle"
    // would be as wrong as one that never did.
    render(
      <DockerQueueBoard status={status({ holders: [parentLease(), gateChild()] })} {...idle} />,
    );

    const card = screen.getByTestId("docker-slot-parent-1");
    expect(within(card).getByText("holding")).toBeInTheDocument();
    expect(within(card).queryByText("reserved, idle")).not.toBeInTheDocument();
  });

  it("a reaped parent keeps its orphaned badge rather than reading as idle", () => {
    // An orphaned or unverifiable lease is a different fact: the reaper could
    // not account for it. Idling that badge away would hide the one state on
    // this board that needs a person.
    render(
      <DockerQueueBoard
        status={status({
          holders: [
            parentLease({ lease_id: "reaped", status: "orphaned" }),
            parentLease({
              lease_id: "unprobeable",
              last_probe_outcome: "daemon_unreachable",
              last_probe_error: "docker daemon unreachable",
            }),
          ],
        })}
        {...idle}
      />,
    );

    expect(within(screen.getByTestId("docker-slot-reaped")).getByText("orphaned")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("docker-slot-unprobeable")).getByText("unverified"),
    ).toBeInTheDocument();
    expect(screen.queryByText("reserved, idle")).not.toBeInTheDocument();
  });

  it("a child lease renders inside its parent's card, not as a sibling", () => {
    // The grid implies peers, and a 0.5 parent drawn beside its 1.0 child reads
    // as 1.5 cpu to anyone adding the cards up. The child states its own cost
    // and how much of it was booked ahead of the line.
    render(
      <DockerQueueBoard status={status({ holders: [parentLease(), gateChild()] })} {...idle} />,
    );

    const grid = screen.getByTestId("docker-slot-grid");
    expect(grid.children).toHaveLength(1);
    const card = screen.getByTestId("docker-slot-parent-1");
    const child = within(card).getByTestId("docker-child-child-1");
    expect(child).toHaveTextContent("gates plan_to_plan-synthesis");
    expect(child).toHaveTextContent("1 cpu");
    expect(child).toHaveTextContent(/0\.5 (cpus? )?booked ahead of the line/);
  });

  it("a child whose parent is not on the board is labelled, not dropped", () => {
    // A child the board cannot place is a ledger fault, and a fault that
    // renders as nothing is the one kind nobody ever reports.
    render(<DockerQueueBoard status={status({ holders: [gateChild()] })} {...idle} />);

    const card = screen.getByTestId("docker-slot-child-1");
    expect(card).toHaveTextContent("nested · parent not on the board");
    expect(within(card).getByText(/gates plan_to_plan-synthesis/)).toBeInTheDocument();
  });

  it("releasing a parent says how many children draw on it", async () => {
    // Releasing a parent cascades: `release_lease` ends every child under it.
    // Offering that as if it ended one lease is the dialog claiming less than
    // the action does.
    releaseLease.mockResolvedValue({ lease_id: "parent-1", released: true });
    render(
      <DockerQueueBoard status={status({ holders: [parentLease(), gateChild()] })} {...idle} />,
    );

    await userEvent.click(screen.getByRole("button", { name: /^Release: run orch_4535af/ }));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Release this run's parent lease?");
    expect(dialog).toHaveTextContent("1 gate child draws on it");
  });

  it("one confirm disables the parent's Release and its children's", async () => {
    // The existing pendingId guard holds the row whose lease is in flight. A
    // child's button left live during its parent's release is a second confirm
    // for a lease that is already ending.
    let finish: (value: { lease_id: string; released: boolean }) => void = () => {};
    releaseLease.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    render(
      <DockerQueueBoard status={status({ holders: [parentLease(), gateChild()] })} {...idle} />,
    );

    await userEvent.click(screen.getByRole("button", { name: /^Release: run orch_4535af/ }));
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Release" }),
    );

    expect(screen.getByRole("button", { name: /^Release: run orch_4535af/ })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: /^Release: gates plan_to_plan-synthesis/ }),
    ).toBeDisabled();

    await act(async () => finish({ lease_id: "parent-1", released: true }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(releaseLease).toHaveBeenCalledTimes(1);
  });

  it("keeps the parent's buttons before its children's in DOM order", () => {
    // Tab order follows the DOM, so nesting the children visually while leaving
    // their buttons ahead of the parent's would make the keyboard order
    // disagree with the nesting a sighted reader sees. No new focus trap and no
    // disclosure: the children are a plain list inside the card.
    render(
      <DockerQueueBoard
        status={status({ holders: [parentLease(), gateChild(), gateChild({ lease_id: "child-2" })] })}
        {...idle}
      />,
    );

    const names = within(screen.getByTestId("docker-slot-parent-1"))
      .getAllByRole("button")
      .map((button) => button.getAttribute("aria-label") ?? button.textContent ?? "");
    expect(names[0]).toMatch(/^Release: run orch_4535af/);
    expect(names.slice(1)).toHaveLength(2); // both children's, inside the parent's card
    expect(names.slice(1).every((name) => /gates plan_to_plan-synthesis/.test(name))).toBe(true);
  });
});
