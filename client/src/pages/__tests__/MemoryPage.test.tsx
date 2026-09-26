import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../api/client";
import type { MemoryBriefingStats, MemoryNode, MemoryNodeDetail } from "../../api/memoryApi";
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
  confidence: { mean: 0.5, lower_bound: 0.02, observations: 0, trusted: false },
};

function detail(overrides: Partial<MemoryNodeDetail> = {}): MemoryNodeDetail {
  return {
    ...NODE,
    versions: [],
    ladder: { clean_pass: 0, passed_after_autofix: 0, rerouted: 0, blocked: 0 },
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
});

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
