/**
 * Switching pools switches the rail with it.
 *
 * The rail's panels are per queue — an agent lane has a history and a review,
 * docker capacity has a ceiling and a short list of leases nobody could resolve
 * — so carrying one queue's tabs into the other would offer panels with nothing
 * to put in them. The case worth pinning is the transition: leaving a tablist
 * with a selection that no longer exists shows an empty rail, which reads as
 * broken rather than as switched.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { QueueDashboard } from "../QueueDashboard";

jest.mock("../ParallelQueueVisualization", () => ({
  ParallelQueueVisualization: ({ headerSlot }: { headerSlot?: React.ReactNode }) => (
    <div data-testid="lane-board">{headerSlot}</div>
  ),
}));
jest.mock("../QueueHistoryRail", () => ({ QueueHistoryRail: () => <div>history rail</div> }));
jest.mock("../../api/dockerApi", () => ({
  dockerApi: {
    capacity: jest.fn().mockResolvedValue({
      enabled: true,
      ceiling: { cpus: 5, memory_mb: 9942, leases: 4, source: "probe", probed_at: null, error: "" },
      in_use: { cpus: 0, memory_mb: 0, leases: 0 },
      available: { cpus: 5, memory_mb: 9942, leases: 4 },
      holders: [],
      waiting: [],
      orphaned: [],
      unverifiable: [],
    }),
  },
}));
jest.mock("../../state/QueueStatusContext", () => ({
  useQueueStatus: () => ({
    activeRuns: [],
    queuedRuns: [],
    stats: { active_count: 0, max_concurrent: 3 },
    workspaces: [{ id: "w1", slug: "loregarden", name: "loregarden" }],
  }),
}));

function tabNames(): string[] {
  return screen
    .getAllByRole("tab")
    .map((t) => t.textContent?.trim() ?? "")
    .filter(Boolean);
}

describe("QueueDashboard queue kinds", () => {
  it("offers the lane panels for agents and the docker panels for docker", async () => {
    render(<QueueDashboard />);

    expect(tabNames()).toEqual(
      expect.arrayContaining(["Overview", "History", "Review", "Controls", "Analytics"]),
    );
    expect(tabNames()).not.toContain("Capacity");

    await userEvent.click(screen.getByRole("tab", { name: "Docker" }));

    await waitFor(() => expect(tabNames()).toContain("Capacity"));
    expect(tabNames()).toContain("Attention");
    // The lane panels are gone: there is no history or review for a capacity pool.
    expect(tabNames()).not.toContain("History");
    expect(tabNames()).not.toContain("Review");
  });

  it("lands on a panel that exists rather than leaving the rail blank", async () => {
    render(<QueueDashboard />);

    // Pick a tab that only the agent queue has, then switch pools.
    await userEvent.click(screen.getByRole("tab", { name: "History" }));
    expect(screen.getByText("history rail")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Docker" }));

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Capacity" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    expect(screen.queryByText("history rail")).not.toBeInTheDocument();
  });

  it("swaps the board itself, not just the rail", async () => {
    render(<QueueDashboard />);
    expect(screen.getByTestId("lane-board")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Docker" }));

    await waitFor(() => expect(screen.queryByTestId("lane-board")).not.toBeInTheDocument());
    // Assert on the grid, not the title: "Docker capacity" is also the rail's
    // heading, and matching both would pass even if only the rail had switched.
    expect(await screen.findByTestId("docker-slot-grid")).toBeInTheDocument();
    expect(screen.getByText(/Waiting for capacity/)).toBeInTheDocument();
  });
});
