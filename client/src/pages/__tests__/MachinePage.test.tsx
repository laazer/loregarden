/**
 * Machine is one page with three tabs in the URL: Capacity (the docker board),
 * Lanes (the agent-lane board that was the Queue page) and Disk.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { diskApi } from "../../api/diskApi";
import { MachinePage } from "../MachinePage";

jest.mock("../../components/ParallelQueueVisualization", () => ({
  ParallelQueueVisualization: ({ headerSlot }: { headerSlot?: React.ReactNode }) => (
    <div data-testid="lane-board">{headerSlot}</div>
  ),
}));
jest.mock("../../api/diskApi");
// The topbar is the app shell's; render the page's half of it in place.
jest.mock("../../components/TopbarPageSlot", () => ({
  PageTopbar: ({ children }: { children: React.ReactNode }) => <div data-testid="topbar">{children}</div>,
}));
jest.mock("../../api/dockerApi", () => ({
  dockerApi: {
    capacity: jest.fn().mockResolvedValue({
      enabled: true,
      ceiling: { cpus: 5, memory_mb: 9942, leases: 4, source: "probe", probed_at: null, error: "" },
      in_use: { cpus: 0, memory_mb: 0, leases: 0 },
      available: { cpus: 5, memory_mb: 9942, leases: 4 },
      host: {
        ceiling: {
          cpus: 12,
          memory_mb: 32768,
          leases: 4,
          source: "probe",
          probed_at: null,
          error: "",
        },
        in_use: { cpus: 0, memory_mb: 0, leases: 0 },
        available: { cpus: 12, memory_mb: 32768, leases: 4 },
      },
      holders: [],
      waiting: [],
      head_shortfall: null,
      orphaned: [],
      unverifiable: [],
    }),
  },
}));
const mockQueueStatus = jest.fn();
jest.mock("../../state/QueueStatusContext", () => ({
  useQueueStatus: () => mockQueueStatus(),
}));
const QUEUE_STATUS = {
  activeRuns: [],
  queuedRuns: [],
  stats: { active_count: 1, max_concurrent: 3, queued_count: 2 },
  isWebSocket: true,
  workspaces: [{ id: "w1", slug: "loregarden", name: "loregarden" }],
  workspacesLoading: false,
};

const mockDisk = diskApi as jest.Mocked<typeof diskApi>;

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/machine/*" element={<MachinePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockQueueStatus.mockReturnValue(QUEUE_STATUS);
  mockDisk.overview.mockResolvedValue({
    volume: { path: "/", total_bytes: 100, used_bytes: 90, free_bytes: 10 },
    status: { state: "idle", started_at: null, entries: 0, current_root: "", error: "" },
    scan: null,
    added_roots: [],
  });
});

describe("MachinePage", () => {
  it("opens on Capacity: what needs a person, then the board, with no rail beside it", async () => {
    renderAt("/machine");

    expect(await screen.findByTestId("docker-free-slots")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Needs attention" })).toBeInTheDocument();
    // The summary is drawn once: the rail's Capacity/Attention tabs are gone.
    expect(screen.queryAllByRole("tab")).toEqual([]);
    expect(screen.getByRole("meter", { name: "Disk" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Capacity" })).toHaveAttribute("aria-current", "page");
  });

  it("flags a full disk, and its scan opens the Disk tab to show the scan running", async () => {
    mockDisk.overview.mockResolvedValue({
      volume: { path: "/", total_bytes: 100, used_bytes: 96, free_bytes: 4 },
      status: { state: "idle", started_at: null, entries: 0, current_root: "", error: "" },
      scan: null,
      added_roots: [],
    });
    mockDisk.scan.mockResolvedValue({
      state: "running",
      started_at: null,
      entries: 0,
      current_root: "",
      error: "",
    });
    renderAt("/machine");

    const item = await screen.findByTestId("attention-disk");
    await userEvent.click(within(item).getByRole("button", { name: "Scan now" }));

    expect(mockDisk.scan).toHaveBeenCalledTimes(1);
    // The tab changes when the scan request answers, not on the click.
    await waitFor(() =>
      expect(screen.getByRole("link", { name: "Disk" })).toHaveAttribute("aria-current", "page"),
    );
  });

  it("moves between Capacity and Disk by link, so each has its own URL", async () => {
    renderAt("/machine");

    await userEvent.click(screen.getByRole("link", { name: "Disk" }));

    expect(await screen.findByRole("heading", { name: "Where did the disk go?" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Disk" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByTestId("docker-free-slots")).not.toBeInTheDocument();
  });
});

describe("Lanes, once the Queue page", () => {
  it("is the tab between Capacity and Disk, with the lane board, its rail and its metrics", async () => {
    renderAt("/machine/lanes");

    expect(screen.getAllByRole("link").map((link) => link.textContent)).toEqual(["Capacity", "Lanes", "Disk"]);
    expect(screen.getByRole("link", { name: "Lanes" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByTestId("lane-board")).toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Overview",
      "History",
      "Review",
      "Controls",
      "Analytics",
    ]);
    expect(screen.getByText("Agent runs holding a lane, and what waits for one")).toBeInTheDocument();
    expect(within(screen.getByTestId("topbar")).getByText("1/3")).toBeInTheDocument();
    // Capacity is a tab away, so the board no longer links out to it.
    expect(screen.queryByRole("link", { name: /Machine capacity/ })).not.toBeInTheDocument();
  });

  it("says why there is nothing to run before any workspace exists", () => {
    mockQueueStatus.mockReturnValue({ ...QUEUE_STATUS, workspaces: [] });
    renderAt("/machine/lanes");

    expect(screen.getByText("Add a workspace before agents can run in a lane.")).toBeInTheDocument();
    expect(screen.queryByTestId("lane-board")).not.toBeInTheDocument();
  });

  it("shows the lanes' metrics only on their own tab", async () => {
    renderAt("/machine");

    expect(await screen.findByTestId("docker-free-slots")).toBeInTheDocument();
    expect(within(screen.getByTestId("topbar")).getByText(/CPU and memory/)).toBeInTheDocument();
    expect(within(screen.getByTestId("topbar")).queryByText("1/3")).not.toBeInTheDocument();
  });
});
