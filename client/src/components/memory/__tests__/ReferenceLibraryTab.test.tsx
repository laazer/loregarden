import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";

import { ApiError } from "../../../api/http";
import { api } from "../../../api/client";
import type { LibraryDetail, LibraryList, LibraryRow } from "../../../api/referenceLibraryApi";
import { findUsabilityProblems } from "../../../lib/usabilityCheck";
import { MemoryPage } from "../../../pages/MemoryPage";
import { useToastStore } from "../../../state/toastStore";
import { useUiStore } from "../../../state/uiStore";
import deepSource from "../../../test/fixtures/prod-shape/reference-library-deep-source.json";
import libraryFixture from "../../../test/fixtures/prod-shape/reference-library-loregarden.json";
import { TopbarPageSlot, TopbarPageSlotProvider } from "../../TopbarPageSlot";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;
// Recorded from the API over the prod-shape scenario (server/tests/test_prod_shape_fixtures.py),
// calibrated from the legacy import of the live record: 113 sources, the deepest with
// 24 passes, 71 linked tickets, 41 unmatched references and 22 learnings.
const LIBRARY = libraryFixture as unknown as LibraryList;
const DEEP = deepSource as unknown as LibraryDetail;

function Path() {
  return <span data-testid="path">{useLocation().pathname}</span>;
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <TopbarPageSlotProvider>
          <TopbarPageSlot />
          <MemoryPage />
          <Path />
        </TopbarPageSlotProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function row(overrides: Partial<LibraryRow>): LibraryRow {
  return { ...LIBRARY.sources[0], ...overrides };
}

function listOf(rows: LibraryRow[]): LibraryList {
  const counts = { candidate: 0, awaiting_approval: 0, queued: 0, reading: 0, surveyed: 0, refused: 0 };
  rows.forEach((r) => (counts[r.state] += 1));
  return { workspace_slug: "loregarden", counts, sources: rows };
}

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.setState({ toasts: [] });
  useUiStore.setState({ inboxOpen: false });
  mockApi.workspaces.mockResolvedValue([
    { id: "w1", slug: "loregarden", name: "loregarden" } as Awaited<ReturnType<typeof api.workspaces>>[number],
  ]);
  mockApi.referenceLibrary.mockResolvedValue(LIBRARY);
  mockApi.referenceSource.mockResolvedValue(DEEP);
});

describe("the reference library at production volume", () => {
  it("opens on the queue, counts every view, and lists all 113 sources under All", async () => {
    renderAt("/memory/library");
    const views = await screen.findByRole("group", { name: "Which sources" });
    await waitFor(() => expect(within(views).getByRole("button", { name: /Queue/ })).toHaveAttribute("aria-pressed", "true"));
    expect(within(views).getByRole("button", { name: "Queue 5" })).toBeInTheDocument();
    expect(within(views).getByRole("button", { name: "Surveyed 99" })).toBeInTheDocument();
    expect(within(views).getByRole("button", { name: "Refused 9" })).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(1 + 5);

    fireEvent.click(within(views).getByRole("button", { name: "All 113" }));
    expect(screen.getAllByRole("row")).toHaveLength(1 + 113);
    expect(findUsabilityProblems(document.body)).toEqual([]);
  });

  it("is a real table a screen reader can sort", async () => {
    renderAt("/memory/library?view=all");
    const table = await screen.findByRole("table");
    const tickets = within(table).getByRole("columnheader", { name: /Tickets/ });
    expect(tickets).toHaveAttribute("aria-sort", "none");
    fireEvent.click(within(tickets).getByRole("button"));
    expect(tickets).toHaveAttribute("aria-sort", "descending");
    const first = within(table).getAllByRole("row")[1];
    expect(within(first).getByRole("link", { name: "openharness" })).toBeInTheDocument();
  });

  it("would be flagged if the long list had no filter — the check is not vacuous", async () => {
    const { container } = renderAt("/memory/library?view=all");
    await screen.findByRole("table");
    container.querySelectorAll("input[type=search]").forEach((input) => input.remove());
    expect(findUsabilityProblems(container).map((p) => p.kind)).toContain("unfiltered-long-list");
  });

  it("opens the deepest source with its tickets grouped, its learnings, and its latest passes", async () => {
    renderAt(`/memory/library/${DEEP.source.id}?view=all`);
    const heading = await screen.findByRole("heading", { level: 2, name: "openharness" });
    await waitFor(() => expect(heading).toHaveFocus());
    const tickets = screen.getByRole("region", { name: /Tickets it shaped/ });
    expect(within(tickets).getByRole("searchbox", { name: "Filter this source's tickets" })).toBeInTheDocument();
    expect(within(tickets).getByText(/41 references from the old record matched no ticket/)).toBeInTheDocument();
    const learnings = screen.getByRole("region", { name: /Learnings on those tickets/ });
    expect(within(learnings).getAllByRole("link").length).toBe(12);
    fireEvent.click(within(learnings).getByRole("button", { name: "Show all 22" }));
    expect(within(learnings).getAllByRole("link").length).toBe(22);
    const passes = screen.getByRole("region", { name: /Passes/ });
    expect(within(passes).getAllByRole("listitem")).toHaveLength(5);
    expect(within(passes).getByRole("button", { name: "Show all 24 passes" })).toHaveAttribute("aria-expanded", "false");
    expect(findUsabilityProblems(screen.getByRole("region", { name: "openharness" }))).toEqual([]);
  });

  it("filters a source's tickets and says when nothing matches", async () => {
    renderAt(`/memory/library/${DEEP.source.id}`);
    const filter = await screen.findByRole("searchbox", { name: "Filter this source's tickets" });
    fireEvent.change(filter, { target: { value: "no-such-ticket" } });
    expect(screen.getByText("No ticket matches “no-such-ticket”.")).toBeInTheDocument();
  });
});

describe("the five states", () => {
  it("shows a skeleton while the list loads", async () => {
    mockApi.referenceLibrary.mockReturnValue(new Promise(() => {}));
    renderAt("/memory/library");
    expect(await screen.findByText("Loading the reference library…")).toBeInTheDocument();
  });

  it("names the failure, toasts it, and retries without a second click firing twice", async () => {
    mockApi.referenceLibrary.mockRejectedValueOnce(new ApiError(500, "database is locked"));
    renderAt("/memory/library");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The reference library did not load: database is locked.");
    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.title)).toContain("Reference library did not load"),
    );
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("explains an empty library differently from an empty filter", async () => {
    mockApi.referenceLibrary.mockResolvedValue(listOf([]));
    renderAt("/memory/library");
    expect(await screen.findByText(/No repositories or sites are in the reference library/)).toBeInTheDocument();
  });

  it("offers to clear a search that matches nothing", async () => {
    renderAt("/memory/library?view=all");
    await screen.findByRole("table");
    fireEvent.change(screen.getByRole("searchbox", { name: "Search sources" }), { target: { value: "zzz" } });
    expect(screen.getByText("No source in the library matches “zzz”.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    expect(screen.getAllByRole("row")).toHaveLength(1 + 113);
  });

  it("an empty view points to where the sources are", async () => {
    mockApi.referenceLibrary.mockResolvedValue(listOf([row({ id: "s1", name: "only", state: "surveyed" })]));
    renderAt("/memory/library?view=refused");
    expect(await screen.findByText("No source has been refused.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show all 1 sources" }));
    expect(screen.getByRole("link", { name: "only" })).toBeInTheDocument();
  });

  it("leads a source awaiting approval to the inbox", async () => {
    const awaiting = row({ id: "a1", name: "pending-repo", state: "awaiting_approval", approval_id: "card-1" });
    mockApi.referenceLibrary.mockResolvedValue(listOf([awaiting]));
    mockApi.referenceSource.mockResolvedValue({ ...DEEP, source: awaiting, tickets: [], unresolved_refs: [], passes: [] });
    renderAt("/memory/library/a1");
    fireEvent.click(await screen.findByRole("button", { name: "Review in inbox" }));
    expect(useUiStore.getState().inboxOpen).toBe(true);
  });

  it("says when learnings could not be read, rather than showing none", async () => {
    mockApi.referenceSource.mockResolvedValue({
      ...DEEP,
      learnings: { availability: "unavailable", reason: "loregarden: Memory graph SQLite is not configured.", items: [] },
    });
    renderAt(`/memory/library/${DEEP.source.id}`);
    expect(await screen.findByText(/Could not read learnings: .*This is not the same as having none\./)).toBeInTheDocument();
  });

  it("tells a missing source apart from a failure", async () => {
    mockApi.referenceSource.mockRejectedValue(new ApiError(404, "No survey source 'gone'"));
    renderAt("/memory/library/gone");
    expect(await screen.findByText("This source is no longer in the library.")).toBeInTheDocument();
    expect(useToastStore.getState().toasts).toEqual([]);
  });
});

describe("keyboard", () => {
  it("opens a source from its name, focuses it, and Escape returns to the list", async () => {
    renderAt("/memory/library?view=all");
    const link = await screen.findByRole("link", { name: "openharness" });
    fireEvent.click(link);
    const heading = await screen.findByRole("heading", { level: 2, name: "openharness" });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(screen.getByTestId("path")).toHaveTextContent(`/memory/library/${DEEP.source.id}`);
    fireEvent.keyDown(heading, { key: "Escape" });
    expect(screen.getByTestId("path")).toHaveTextContent(/^\/memory\/library$/);
  });

  it("has a named way back that is a button, not only a key", async () => {
    renderAt(`/memory/library/${DEEP.source.id}`);
    fireEvent.click(await screen.findByRole("button", { name: "← Back to sources" }));
    expect(screen.getByTestId("path")).toHaveTextContent(/^\/memory\/library$/);
  });
});
