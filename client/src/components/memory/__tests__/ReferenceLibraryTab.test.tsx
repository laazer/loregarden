import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";

import { ApiError } from "../../../api/http";
import { api } from "../../../api/client";
import type { LibraryDetail, LibraryList, LibraryRow, QueueSchedule } from "../../../api/referenceLibraryApi";
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

const INITIATIVE = {
  id: "init-uuid-1",
  external_id: "init-survey-1",
  title: "Make surveys pay off",
  description: "",
  state: "in_progress",
  priority: 2,
  milestones: [],
  progress: { resolved: 0, total: 0 },
  workspaces: ["loregarden"],
} as Awaited<ReturnType<typeof api.initiatives>>[number];

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
  return { workspace_slug: "loregarden", counts, sources: rows, schedule: LIBRARY.schedule };
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
  mockApi.initiatives.mockResolvedValue([INITIATIVE]);
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
    // Under the name: what happened and what it left, not a gloss on the badge.
    expect(
      screen.getByText(/^Surveyed .* \(24 passes\)\. 71 tickets carry its findings, plus 41 old references/),
    ).toBeInTheDocument();
    const record = screen.getByRole("region", { name: "What the survey recorded" });
    expect(within(record).getByText(/keeps a verdict only when a second instrument agrees/)).toBeInTheDocument();
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

describe("initiatives following a source", () => {
  it("links an initiative, says what joined, and shows it as following", async () => {
    mockApi.linkSourceInitiative.mockResolvedValue({
      initiative_id: INITIATIVE.id,
      initiative: "init-survey-1",
      added: ["lg-a-1", "lg-a-2"],
      skipped: [{ ticket: "lg-a-3", reason: "init-survey-1 already covers lg-a-3 through lg-a-1, a member of it." }],
      settled_left_out: 2,
    });
    renderAt(`/memory/library/${DEEP.source.id}`);
    const section = await screen.findByRole("region", { name: /Initiatives following it/ });
    expect(within(section).getByText(/No initiative follows this source/)).toBeInTheDocument();
    const link = within(section).getByRole("button", { name: "Link" });
    expect(link).toBeDisabled();
    fireEvent.change(await within(section).findByLabelText("Link an initiative"), { target: { value: INITIATIVE.id } });
    mockApi.referenceSource.mockResolvedValue({
      ...DEEP,
      initiatives: [{ id: INITIATIVE.id, external_id: "init-survey-1", title: INITIATIVE.title, members_added: 2 }],
    });
    fireEvent.click(link);
    expect(await within(section).findByRole("status")).toHaveTextContent(
      "2 open tickets joined init-survey-1; 2 settled tickets stayed out; 1 the initiative already covers.",
    );
    expect(mockApi.linkSourceInitiative).toHaveBeenCalledTimes(1);
    expect(mockApi.linkSourceInitiative).toHaveBeenCalledWith(DEEP.source.id, INITIATIVE.id);
    expect(await within(section).findByRole("link", { name: /init-survey-1 Make surveys pay off/ })).toBeInTheDocument();
    expect(within(section).getByText("Why 1 ticket did not join")).toBeInTheDocument();
    expect(within(section).getByText("Every initiative already follows this source.")).toBeInTheDocument();
  });

  it("asks before unlinking, saying what will leave, and reports what did", async () => {
    mockApi.referenceSource.mockResolvedValue({
      ...DEEP,
      initiatives: [{ id: INITIATIVE.id, external_id: "init-survey-1", title: INITIATIVE.title, members_added: 69 }],
    });
    mockApi.unlinkSourceInitiative.mockResolvedValue({ initiative: "init-survey-1", removed: ["lg-a-1"], kept: ["lg-a-2"] });
    renderAt(`/memory/library/${DEEP.source.id}`);
    const section = await screen.findByRole("region", { name: /Initiatives following it/ });
    fireEvent.click(await within(section).findByRole("button", { name: "Unlink init-survey-1" }));
    const confirm = within(section).getByRole("group", { name: "Unlink init-survey-1?" });
    expect(confirm).toHaveTextContent("The 69 tickets this link added leave init-survey-1");
    expect(mockApi.unlinkSourceInitiative).not.toHaveBeenCalled();
    fireEvent.click(within(confirm).getByRole("button", { name: "Unlink" }));
    expect(await within(section).findByRole("status")).toHaveTextContent(
      "Unlinked init-survey-1: 1 ticket left it; 1 stayed, held by another source.",
    );
  });

  it("names a failed link in a toast", async () => {
    mockApi.linkSourceInitiative.mockRejectedValue(new ApiError(422, "init-x is not an initiative"));
    renderAt(`/memory/library/${DEEP.source.id}`);
    const section = await screen.findByRole("region", { name: /Initiatives following it/ });
    fireEvent.change(await within(section).findByLabelText("Link an initiative"), { target: { value: INITIATIVE.id } });
    fireEvent.click(within(section).getByRole("button", { name: "Link" }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.title)).toContain("Could not link the initiative"),
    );
  });

  it("points to the Initiatives page when there are none", async () => {
    mockApi.initiatives.mockResolvedValue([]);
    renderAt(`/memory/library/${DEEP.source.id}`);
    expect(await screen.findByRole("link", { name: "Create one on the Initiatives page" })).toHaveAttribute(
      "href",
      "/initiatives",
    );
  });
});

describe("how far each repository has moved since review", () => {
  const checked = (overrides: Partial<LibraryRow>) =>
    row({ upstream_checked_at: "2026-10-22T08:00:00", upstream_check_error: "", ...overrides });

  it("shows the newest commit date and the change since review, sortable most-changed first", async () => {
    mockApi.referenceLibrary.mockResolvedValue(
      listOf([
        checked({ id: "a", name: "quiet-repo", state: "surveyed", upstream_committed_at: "2026-09-01T00:00:00", commits_since_review: 0 }),
        checked({ id: "b", name: "busy-repo", state: "surveyed", upstream_committed_at: "2026-10-21T00:00:00", commits_since_review: 142 }),
        row({ id: "c", name: "unchecked-repo", state: "surveyed", upstream_checked_at: null, upstream_committed_at: null, commits_since_review: null }),
        checked({ id: "d", name: "broken-repo", state: "surveyed", upstream_committed_at: null, commits_since_review: null, upstream_check_error: "gh: Not Found (HTTP 404)" }),
      ]),
    );
    renderAt("/memory/library?view=surveyed");
    const table = await screen.findByRole("table");
    const cellFor = (name: string) => {
      const tr = within(table).getByRole("link", { name }).closest("tr") as HTMLElement;
      return tr.querySelector(".rl-col-since_review")?.textContent;
    };
    expect(cellFor("busy-repo")).toBe("+142 commits");
    expect(cellFor("quiet-repo")).toBe("no change");
    expect(cellFor("unchecked-repo")).toBe("not checked");
    expect(cellFor("broken-repo")).toBe("couldn’t check");
    const since = within(table).getByRole("columnheader", { name: /Since review/ });
    fireEvent.click(within(since).getByRole("button"));
    expect(since).toHaveAttribute("aria-sort", "descending");
    expect(within(within(table).getAllByRole("row")[1]).getByRole("link")).toHaveTextContent("busy-repo");
  });

  it("checks now from the source and says why a check could not answer", async () => {
    const source = checked({
      id: "s1", name: "broken-repo", state: "surveyed", last_read_at: "2026-10-14T00:00:00",
      upstream_committed_at: null, commits_since_review: null, upstream_check_error: "gh: Not Found (HTTP 404)",
    });
    mockApi.referenceLibrary.mockResolvedValue(listOf([source]));
    mockApi.referenceSource.mockResolvedValue({ ...DEEP, source, initiatives: [] });
    mockApi.checkSourceUpstream.mockResolvedValue(source);
    renderAt("/memory/library/s1");
    expect(await screen.findByText("gh: Not Found (HTTP 404)")).toBeInTheDocument();
    expect(screen.getAllByText("couldn’t check").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Check now" }));
    await waitFor(() => expect(mockApi.checkSourceUpstream).toHaveBeenCalledWith("s1"));
  });

  it("stays usable when every repository at production volume has been checked", async () => {
    mockApi.referenceLibrary.mockResolvedValue({
      ...LIBRARY,
      sources: LIBRARY.sources.map((r, i) => ({
        ...r,
        upstream_checked_at: "2026-10-22T08:00:00",
        upstream_committed_at: "2026-10-20T00:00:00",
        commits_since_review: i % 3 === 0 ? 0 : i,
      })),
    });
    renderAt("/memory/library?view=all");
    await screen.findByRole("table");
    expect(findUsabilityProblems(document.body)).toEqual([]);
  });
});

describe("the review queue", () => {
  const queued = (id: string, name: string) =>
    row({ id, name, state: "queued", passes: 0, tickets: 0, last_read_at: null, question: "How do they retry?" });
  const schedule = (overrides: Partial<QueueSchedule>): QueueSchedule => ({
    ...LIBRARY.schedule,
    next: { source_id: "q1", name: "retry-kit", ordinal: 1, mode: "initial", waiting: "" },
    positions: { q1: 1, q2: 2 },
    ...overrides,
  });

  it("says nothing is queued at production shape, and how to queue something", async () => {
    renderAt("/memory/library?view=all");
    const status = await screen.findByRole("status", { name: "Review queue" });
    expect(within(status).getByText(/Nothing is queued\. Add a reference/)).toBeInTheDocument();
  });

  it("names the next review and when it starts, and reviews it now", async () => {
    mockApi.referenceLibrary.mockResolvedValue({
      ...listOf([queued("q1", "retry-kit"), queued("q2", "later-kit")]),
      schedule: schedule({}),
    });
    mockApi.reviewSourceNow.mockResolvedValue({ started: true, ordinal: 1, carrier: "lg-survey-9", waiting: "", refused: "" });
    renderAt("/memory/library?view=queue");
    const status = await screen.findByRole("status", { name: "Review queue" });
    expect(status).toHaveTextContent("Next review retry-kit, pass 1 (1 more after it) — starts at the next check, within 2 minutes.");
    fireEvent.click(within(status).getByRole("button", { name: "Review now" }));
    await waitFor(() => expect(mockApi.reviewSourceNow).toHaveBeenCalledWith("q1", ""));
    await waitFor(() => expect(useToastStore.getState().toasts[0]?.title).toBe("Reviewing retry-kit"));
  });

  it("says what holds the next review, and offers no button that cannot help", async () => {
    const waiting = "no open ticket in loregarden is tagged `reference-survey-carrier`; tag the feature survey passes should be filed under";
    mockApi.referenceLibrary.mockResolvedValue({
      ...listOf([queued("q1", "retry-kit")]),
      schedule: schedule({ next: { source_id: "q1", name: "retry-kit", ordinal: 1, mode: "initial", waiting } }),
    });
    renderAt("/memory/library?view=queue");
    const status = await screen.findByRole("status", { name: "Review queue" });
    expect(status).toHaveTextContent("held: no open ticket in loregarden is tagged reference-survey-carrier");
    expect(within(status).queryByRole("button", { name: "Review now" })).not.toBeInTheDocument();
  });

  it("shows a queued source's place in line and says when Review now could not start it", async () => {
    const second = queued("q2", "later-kit");
    mockApi.referenceLibrary.mockResolvedValue({ ...listOf([queued("q1", "retry-kit"), second]), schedule: schedule({}) });
    mockApi.referenceSource.mockResolvedValue({
      ...DEEP, source: second, tickets: [], unresolved_refs: [], record: "",
      passes: [{ ...DEEP.passes[0], ordinal: 1, status: "queued", finished_at: null, record: "" }],
    });  // prettier-ignore
    mockApi.reviewSourceNow.mockResolvedValue({
      started: false, ordinal: 1, carrier: null, waiting: "1 pass(es) running, at the limit of 1", refused: "",
    });  // prettier-ignore
    renderAt("/memory/library/q2?view=queue");
    const detail = await screen.findByRole("region", { name: "later-kit" });
    expect(await within(detail).findByText(/Pass 1 is 2nd in line\./)).toBeInTheDocument();
    fireEvent.click(within(detail).getByRole("button", { name: "Review now" }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts[0]).toMatchObject({
        title: "later-kit is first in line but has not started",
        message: "Pass 1 is waiting: 1 pass(es) running, at the limit of 1.",
      }),
    );
  });

  it("reviews a surveyed source again against a question it can edit", async () => {
    mockApi.reviewSourceNow.mockResolvedValue({ started: true, ordinal: 25, carrier: "lg-survey-10", waiting: "", refused: "" });
    renderAt(`/memory/library/${DEEP.source.id}`);
    fireEvent.click(await screen.findByRole("button", { name: "Review again now…" }));
    const question = screen.getByRole("textbox", { name: "What should this review answer?" });
    fireEvent.change(question, { target: { value: "What changed in their verdict ledger?" } });
    fireEvent.click(screen.getByRole("button", { name: "Review now" }));
    await waitFor(() =>
      expect(mockApi.reviewSourceNow).toHaveBeenCalledWith(DEEP.source.id, "What changed in their verdict ledger?"),
    );
  });
});

describe("adding a reference", () => {
  function fill(form: HTMLElement) {
    fireEvent.change(within(form).getByRole("textbox", { name: "URL" }), { target: { value: "https://github.com/acme/retry-kit" } });
    fireEvent.change(within(form).getByRole("textbox", { name: "What should the survey answer from it?" }), {
      target: { value: "How do they retry?" },
    });
    fireEvent.change(within(form).getByRole("textbox", { name: "Why this one?" }), { target: { value: "A retry ledger" } });
  }

  it("proposes a repository, sends it to the inbox, and opens it", async () => {
    const added = row({ id: "new1", name: "retry-kit", state: "awaiting_approval", approval_id: "card-9" });
    mockApi.addReferenceSource.mockResolvedValue({ source: added, attached: ["loregarden"], scoped_pass: null });
    renderAt("/memory/library?view=queue");
    const toggle = await screen.findByRole("button", { name: "Add a reference" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    const form = screen.getByRole("form", { name: "Add a reference" });
    const submit = within(form).getByRole("button", { name: "Propose for review" });
    expect(submit).toBeDisabled();
    fill(form);
    fireEvent.click(submit);
    await waitFor(() =>
      expect(mockApi.addReferenceSource).toHaveBeenCalledWith({
        workspace_slugs: ["loregarden"], kind: "repo", url: "https://github.com/acme/retry-kit",
        question: "How do they retry?", queued_reason: "A retry ledger",
      }),
    );  // prettier-ignore
    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent("/memory/library/new1"));
    expect(useUiStore.getState().inboxOpen).toBe(true);
    expect(screen.queryByRole("form", { name: "Add a reference" })).not.toBeInTheDocument();
  });

  it("keeps the form and says why when the URL is refused", async () => {
    mockApi.addReferenceSource.mockRejectedValue(new ApiError(422, "Not a repository URL this survey can clone"));
    renderAt("/memory/library?view=queue");
    fireEvent.click(await screen.findByRole("button", { name: "Add a reference" }));
    const form = screen.getByRole("form", { name: "Add a reference" });
    fill(form);
    fireEvent.click(within(form).getByRole("button", { name: "Propose for review" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(/Not added: .*Not a repository URL/);
    fireEvent.keyDown(within(form).getByRole("textbox", { name: "URL" }), { key: "Escape" });
    expect(screen.queryByRole("form", { name: "Add a reference" })).not.toBeInTheDocument();
  });

  it("proposes a listed candidate from its own page, URL fixed", async () => {
    const candidate = row({ id: "c1", name: "agent-mux", state: "candidate", url: "https://github.com/openwork/agent-mux" });
    mockApi.referenceLibrary.mockResolvedValue(listOf([candidate]));
    mockApi.referenceSource.mockResolvedValue({ ...DEEP, source: candidate, passes: [], tickets: [], unresolved_refs: [], record: "" });
    renderAt("/memory/library/c1");
    fireEvent.click(await screen.findByRole("button", { name: "Propose it for review…" }));
    const form = screen.getByRole("form", { name: "Add a reference" });
    expect(within(form).getByRole("textbox", { name: "URL" })).toHaveAttribute("readonly");
    expect(within(form).getByRole("textbox", { name: "URL" })).toHaveValue("https://github.com/openwork/agent-mux");
  });
});
