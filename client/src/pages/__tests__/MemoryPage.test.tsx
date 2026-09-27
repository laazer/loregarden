import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../api/client";
import type {
  GraphHealthReport,
  MemoryBriefingStats,
  MemoryNode,
  MemoryNodeDetail,
  MemoryProposal,
} from "../../api/memoryApi";
import { TopbarPageSlot, TopbarPageSlotProvider } from "../../components/TopbarPageSlot";
import { MemoryPage } from "../MemoryPage";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function stats(overrides: Partial<MemoryBriefingStats> = {}): MemoryBriefingStats {
  return {
    window_days: 7,
    window_from: "2026-09-19T00:00:00Z",
    window_to: "2026-09-26T00:00:00Z",
    runs_in_window: 12,
    runs_with_briefing_row: 9,
    runs_with_no_briefing_row: 3,
    rows_in_window: 9,
    newest_run_at: "2026-09-25T12:00:00",
    last_row_at: "2026-09-25T12:00:04",
    built: 6,
    empty: 2,
    store_error: 1,
    no_store: 0,
    skipped: 0,
    ...overrides,
  };
}

const NODE: MemoryNode = {
  id: "n1",
  title: "Use DELETE journal on iCloud",
  body: "WAL corrupts under sync.",
  tags: [],
  ticket_id: "",
  workspace_slug: "lg",
  node_type: "learning",
  created_at: "2026-09-01T00:00:00",
  updated_at: "2026-09-01T00:00:00",
  discredited: false,
  aliases: [],
  confidence: { mean: 0.5, lower_bound: 0.02, observations: 0, trusted: false },
};

function detail(overrides: Partial<MemoryNodeDetail> = {}): MemoryNodeDetail {
  return {
    ...NODE,
    versions: [],
    ladder: { clean_pass: 0, passed_after_autofix: 0, rerouted: 0, blocked: 0 },
    relations: [],
    superseded_by: [],
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TopbarPageSlotProvider>
          <TopbarPageSlot />
          <MemoryPage />
        </TopbarPageSlotProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.memoryBriefings.mockResolvedValue(stats());
  mockApi.workspaces.mockResolvedValue([
    { id: "w1", slug: "lg", name: "loregarden" } as Awaited<
      ReturnType<typeof api.workspaces>
    >[number],
  ]);
  mockApi.memoryNodes.mockResolvedValue({
    workspace_slug: "lg",
    include_discredited: true,
    nodes: [NODE],
  });
  mockApi.memoryNode.mockResolvedValue(detail());
  mockApi.memoryGraphHealth.mockResolvedValue(healthReport());
  mockApi.memoryProposals.mockResolvedValue([]);
  mockApi.memoryLineage.mockResolvedValue({ node_id: "n1", steps: [] });
});

function reading(shares: Partial<GraphHealthReport["current"]["shares"]>, learnings = 10) {
  return {
    workspace_slug: "lg",
    measured_at: "2026-09-20T00:00:00",
    figures: {
      learnings,
      unlinked: 0,
      never_surfaced: 0,
      surfaced_unscored: 0,
      stale: 0,
      contested: 0,
      superseded: 1,
      discredited: 2,
    },
    shares: {
      unlinked: 0,
      never_surfaced: 0,
      contested: 0,
      stale: 0,
      surfaced_unscored: 0,
      ...shares,
    },
  };
}

function healthReport(overrides: Partial<GraphHealthReport> = {}): GraphHealthReport {
  return {
    current: reading({ unlinked: 40 }),
    previous: reading({ unlinked: 20 }, 6),
    moved: [{ metric: "unlinked", was: 20, now: 40 }],
    notes: ["Learnings grew from 6 to 10 while the unlinked share rose: stop."],
    watch: "unlinked",
    ...overrides,
  };
}

it("shows holes apart from the recorded outcomes", async () => {
  renderPage();
  const holes = await screen.findByRole("region", { name: /runs that recorded nothing/i });
  expect(within(holes).getByText("3")).toBeInTheDocument();
  expect(within(holes).getByText(/of 12 runs \(25%\)/)).toBeInTheDocument();
  const outcomes = screen.getByRole("region", { name: /recorded outcomes/i });
  expect(within(outcomes).queryByText(/recorded nothing/i)).not.toBeInTheDocument();
});

it("says in words when recording has stopped", async () => {
  mockApi.memoryBriefings.mockResolvedValue(stats({ last_row_at: "2026-09-20T12:00:00" }));
  renderPage();
  expect(await screen.findByText(/recording appears to have stopped/i)).toBeInTheDocument();
});

it("reads an empty window as no runs, not as an error", async () => {
  mockApi.memoryBriefings.mockResolvedValue(
    stats({
      runs_in_window: 0,
      runs_with_briefing_row: 0,
      runs_with_no_briefing_row: 0,
      rows_in_window: 0,
      newest_run_at: null,
      last_row_at: null,
      built: 0,
      empty: 0,
      store_error: 0,
    }),
  );
  renderPage();
  expect(await screen.findByText(/no runs in this window/i)).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("renders a failure as an error with a retry", async () => {
  mockApi.memoryBriefings.mockRejectedValue(new Error("server down"));
  renderPage();
  const alert = await screen.findByText(/could not load briefing health: server down/i);
  expect(alert).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
});

it("switches the window through a keyboard-reachable radio group", async () => {
  renderPage();
  await screen.findByRole("region", { name: /runs that recorded nothing/i });
  fireEvent.click(screen.getByRole("radio", { name: "30 days" }));
  await waitFor(() => expect(mockApi.memoryBriefings).toHaveBeenCalledWith(30));
  expect(screen.getByRole("radio", { name: "30 days" })).toHaveAttribute("aria-checked", "true");
});

it("disables Refresh while a refresh is in flight", async () => {
  renderPage();
  await screen.findByRole("region", { name: /runs that recorded nothing/i });
  mockApi.memoryBriefings.mockReturnValue(new Promise(() => {}));
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  expect(await screen.findByRole("button", { name: /refreshing/i })).toBeDisabled();
});

it("discredits a learning only after a confirm step with a reason", async () => {
  mockApi.setMemoryNodeDiscredited.mockResolvedValue(detail({ discredited: true }));
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: /use delete journal on icloud/i }));
  fireEvent.click(await screen.findByRole("button", { name: "Discredit" }));

  const dialog = await screen.findByRole("dialog");
  const confirm = within(dialog).getByRole("button", { name: "Discredit learning" });
  expect(confirm).toBeDisabled();
  fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "Wrong since 3.45" } });
  fireEvent.click(confirm);

  await waitFor(() =>
    expect(mockApi.setMemoryNodeDiscredited).toHaveBeenCalledWith("n1", {
      workspace_slug: "lg",
      discredited: true,
      reason: "Wrong since 3.45",
    }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(await screen.findByRole("button", { name: "Restore" })).toBeInTheDocument();
});

it("closes the confirm dialog on Escape without writing", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: /use delete journal on icloud/i }));
  fireEvent.click(await screen.findByRole("button", { name: "Discredit" }));
  await screen.findByRole("dialog");
  fireEvent.keyDown(document, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(mockApi.setMemoryNodeDiscredited).not.toHaveBeenCalled();
});

it("says a learning with no observed runs is not observed yet", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: /use delete journal on icloud/i }));
  expect(await screen.findByText(/not observed yet/i)).toBeInTheDocument();
});

it("shows graph shares against the last snapshot and names one to watch", async () => {
  renderPage();
  const panel = await screen.findByRole("region", { name: /graph health/i });
  expect(await within(panel).findByText("40%")).toBeInTheDocument();
  expect(within(panel).getByText("+20 pts")).toBeInTheDocument();
  expect(within(panel).getByText(/unlinked · watch/i)).toBeInTheDocument();
  expect(within(panel).getByText(/unlinked share rose/i)).toBeInTheDocument();
});

it("records a snapshot without letting a second click fire twice", async () => {
  mockApi.recordMemoryGraphHealth.mockReturnValue(new Promise(() => {}));
  renderPage();
  const panel = await screen.findByRole("region", { name: /graph health/i });
  await within(panel).findByText("40%");
  fireEvent.click(within(panel).getByRole("button", { name: "Record snapshot" }));
  const busy = await within(panel).findByRole("button", { name: /recording/i });
  expect(busy).toBeDisabled();
  fireEvent.click(busy);
  expect(mockApi.recordMemoryGraphHealth).toHaveBeenCalledTimes(1);
});

it("says an empty graph has no shape rather than showing zero percent", async () => {
  mockApi.memoryGraphHealth.mockResolvedValue(
    healthReport({ current: reading({}, 0), previous: null, moved: [], notes: [], watch: null }),
  );
  renderPage();
  expect(await screen.findByText(/no live learnings in lg yet/i)).toBeInTheDocument();
});

it("says there is nothing to decide when no proposals come back", async () => {
  renderPage();
  expect(await screen.findByText(/nothing to decide/i)).toBeInTheDocument();
});

const DUPLICATE: MemoryProposal = {
  kind: "near_duplicate",
  node_ids: ["n1", "n2"],
  titles: ["Use DELETE journal on iCloud", "iCloud journal mode"],
  reason: "These read as the same idea.",
  suggested_title: null,
  similarity: 0.82,
};

it("merges a duplicate only after choosing what to keep and saying why", async () => {
  mockApi.memoryProposals.mockResolvedValue([DUPLICATE]);
  mockApi.mergeMemoryNodes.mockResolvedValue({
    survivor: NODE,
    absorbed_id: "n1",
    aliases_added: [],
    edges_moved: 0,
    outcomes_moved: 0,
  });
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Merge…" }));
  const dialog = await screen.findByRole("dialog", { name: /merge these learnings/i });
  fireEvent.click(within(dialog).getByRole("radio", { name: "iCloud journal mode" }));
  const confirm = within(dialog).getByRole("button", { name: "Merge" });
  expect(confirm).toBeDisabled();
  fireEvent.change(within(dialog).getByRole("textbox", { name: /reason/i }), {
    target: { value: "same lesson" },
  });
  fireEvent.click(confirm);
  await waitFor(() =>
    expect(mockApi.mergeMemoryNodes).toHaveBeenCalledWith("n2", {
      workspace_slug: "lg",
      absorbed_id: "n1",
      reason: "same lesson",
    }),
  );
});

it("retitles with the suggested name prefilled and editable", async () => {
  mockApi.memoryProposals.mockResolvedValue([
    {
      kind: "generic_title",
      node_ids: ["n1"],
      titles: ["Learning — t-1"],
      reason: "Titled by its ticket.",
      suggested_title: "Pin the retry budget",
      similarity: null,
    },
  ]);
  mockApi.retitleMemoryNode.mockResolvedValue(detail());
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Retitle…" }));
  const dialog = await screen.findByRole("dialog");
  const title = within(dialog).getByRole("textbox", { name: "New title" });
  expect(title).toHaveValue("Pin the retry budget");
  fireEvent.change(title, { target: { value: "   " } });
  fireEvent.change(within(dialog).getByRole("textbox", { name: /reason/i }), {
    target: { value: "name it" },
  });
  expect(within(dialog).getByRole("button", { name: "Retitle" })).toBeDisabled();
  fireEvent.keyDown(document, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(mockApi.retitleMemoryNode).not.toHaveBeenCalled();
});

it("shows a learning's relations, its successor and what changed", async () => {
  mockApi.memoryNode.mockResolvedValue(
    detail({
      aliases: ["journal mode"],
      superseded_by: [{ id: "n9", title: "Use the native store" }],
      relations: [
        {
          id: "r1",
          relation_type: "supersedes",
          direction: "in",
          node_id: "n9",
          title: "Use the native store",
          discredited: false,
        },
      ],
    }),
  );
  mockApi.memoryLineage.mockResolvedValue({
    node_id: "n1",
    steps: [
      { ...NODE, versions: [] },
      { ...NODE, id: "n9", title: "Use the native store", versions: [] },
    ],
  });
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: /use delete journal on icloud/i }));
  expect(await screen.findByText(/also known as journal mode/i)).toBeInTheDocument();
  expect(screen.getByRole("note")).toHaveTextContent("Superseded by Use the native store");
  const relations = screen.getByRole("list", { name: "Relations" });
  expect(within(relations).getByText(/← supersedes/)).toBeInTheDocument();
  const lineage = await screen.findByRole("list", { name: /lineage/i });
  expect(within(lineage).getAllByRole("listitem")).toHaveLength(2);
});
