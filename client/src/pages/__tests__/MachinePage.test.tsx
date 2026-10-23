/**
 * Machine is its own page: Capacity (the docker board that used to be the
 * Queue page's toggle) and Disk, both in the URL. The Queue board no longer
 * switches to it; it links to it.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { diskApi } from "../../api/diskApi";
import { QueueDashboard } from "../../components/QueueDashboard";
import { MachinePage } from "../MachinePage";

jest.mock("../../components/ParallelQueueVisualization", () => ({
  ParallelQueueVisualization: ({ headerSlot }: { headerSlot?: React.ReactNode }) => (
    <div data-testid="lane-board">{headerSlot}</div>
  ),
}));
jest.mock("../../api/diskApi");
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
jest.mock("../../state/QueueStatusContext", () => ({
  useQueueStatus: () => ({
    activeRuns: [],
    queuedRuns: [],
    stats: { active_count: 0, max_concurrent: 3 },
    workspaces: [{ id: "w1", slug: "loregarden", name: "loregarden" }],
  }),
}));

const mockDisk = diskApi as jest.Mocked<typeof diskApi>;

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/machine/*" element={<MachinePage />} />
          <Route path="/queue" element={<QueueDashboard />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockDisk.overview.mockResolvedValue({
    volume: { path: "/", total_bytes: 100, used_bytes: 90, free_bytes: 10 },
    status: { state: "idle", started_at: null, entries: 0, current_root: "", error: "" },
    scan: null,
    added_roots: [],
  });
});

describe("MachinePage", () => {
  it("opens on Capacity: the machine board with its own rail, no agent-lane panels", async () => {
    renderAt("/machine");

    expect(await screen.findByTestId("docker-free-slots")).toBeInTheDocument();
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual(["Capacity", "Attention"]);
    expect(screen.getByRole("link", { name: "Capacity" })).toHaveAttribute("aria-current", "page");
  });

  it("moves between Capacity and Disk by link, so each has its own URL", async () => {
    renderAt("/machine");

    await userEvent.click(screen.getByRole("link", { name: "Disk" }));

    expect(await screen.findByRole("heading", { name: "Where did the disk go?" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Disk" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByTestId("docker-free-slots")).not.toBeInTheDocument();
  });
});

describe("QueueDashboard after the move", () => {
  it("has no Machine toggle, and links to the Machine page instead", async () => {
    renderAt("/queue");

    expect(screen.getByTestId("lane-board")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Machine" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Machine capacity →" })).toHaveAttribute("href", "/machine");
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Overview",
      "History",
      "Review",
      "Controls",
      "Analytics",
    ]);
  });
});
