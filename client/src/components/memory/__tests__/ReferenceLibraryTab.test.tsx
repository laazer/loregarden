import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";

import { ApiError } from "../../../api/http";
import { api } from "../../../api/client";
import type { LibraryDetail, LibraryList, LibraryRow, QueueSchedule, TopicView } from "../../../api/referenceLibraryApi";
import { findUsabilityProblems } from "../../../lib/usabilityCheck";
import { MemoryPage } from "../../../pages/MemoryPage";
import { useToastStore } from "../../../state/toastStore";
import { useUiStore } from "../../../state/uiStore";
import deepSource from "../../../test/fixtures/prod-shape/reference-library-deep-source.json";
import libraryFixture from "../../../test/fixtures/prod-shape/reference-library-loregarden.json";
import { TopbarPageSlot, TopbarPageSlotProvider } from "../../TopbarPageSlot";
import { ReferenceTargetsPicker } from "../ReferenceTargetsPicker";

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

const TOPICS: TopicView[] = [
  {
    id: "memory_layer", label: "Improved memory layer", search: "agent memory layers",
    question: "How do they capture, store, retrieve, rank and retire what agents learn?",
    why: "It works on agent memory, which ours could do better.",
  },
  {
    id: "ui_ux", label: "UI/UX improvements and inspiration", search: "dense data UIs",
    question: "What layouts would make our operator surfaces clearer?", why: "Its interface handles dense data well.",
  },
  {
    id: "similar_tools", label: "Similar tools", search: "same purpose",
    question: "What do tools with the same purpose do differently?", why: "It solves the same problem we do.",
  },
];  // prettier-ignore

const NOTHING_MORE = { question: "", workspace_slugs: [], initiative_ids: [] };

function row(overrides: Partial<LibraryRow>): LibraryRow {
  return { ...LIBRARY.sources[0], ...overrides };
}

function listOf(rows: LibraryRow[]): LibraryList {
  const counts = { candidate: 0, awaiting_approval: 0, queued: 0, reading: 0, surveyed: 0, refused: 0 };
  rows.forEach((r) => (counts[r.state] += 1));
  return { workspace_slug: "loregarden", counts, sources: rows, schedule: LIBRARY.schedule, discoveries: [] };
}

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.setState({ toasts: [] });
  useUiStore.setState({ inboxOpen: false });
  mockApi.workspaces.mockResolvedValue(
    [
      { id: "w1", slug: "loregarden", name: "loregarden", archived_at: null },
      { id: "w2", slug: "blobert", name: "blobert", archived_at: null },
      { id: "w3", slug: "old-one", name: "old-one", archived_at: "2026-09-01T00:00:00" },
    ] as Awaited<ReturnType<typeof api.workspaces>>,
  );
  mockApi.referenceLibrary.mockResolvedValue(LIBRARY);
  mockApi.referenceSource.mockResolvedValue(DEEP);
  mockApi.initiatives.mockResolvedValue([INITIATIVE]);
  mockApi.referenceTopics.mockResolvedValue(TOPICS);
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
    // An agent's proposal: keep or drop it here, or decide in the inbox.
    const decide = await screen.findByRole("group", { name: "Keep or drop pending-repo" });
    expect(within(decide).getByRole("button", { name: "Keep" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review in inbox" }));
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
  it("links several workspaces and initiatives at once from a modal, and says what each did", async () => {
    const second = { ...INITIATIVE, id: "init-uuid-2", external_id: "init-survey-2", title: "Second" };
    mockApi.initiatives.mockResolvedValue([INITIATIVE, second]);
    mockApi.linkSourceTargets.mockResolvedValue({
      attached: ["blobert"],
      scoped_pass: 25,
      initiatives: [
        {
          initiative_id: INITIATIVE.id, initiative: "init-survey-1", added: ["lg-a-1", "lg-a-2"], settled_left_out: 2,
          skipped: [{ ticket: "lg-a-3", reason: "init-survey-1 already covers lg-a-3 through lg-a-1, a member of it." }],
        },
        { initiative_id: second.id, initiative: "init-survey-2", added: [], skipped: [], settled_left_out: 0 },
      ],
    });  // prettier-ignore
    renderAt(`/memory/library/${DEEP.source.id}`);
    const section = await screen.findByRole("region", { name: /Initiatives following it/ });
    expect(within(section).getByText(/No initiative follows this source/)).toBeInTheDocument();
    const open = within(section).getByRole("button", { name: "Link workspaces and initiatives…" });
    expect(open).toHaveAttribute("aria-haspopup", "dialog");
    fireEvent.click(open);
    const dialog = await screen.findByRole("dialog", { name: `Link to ${DEEP.source.name}` });
    const submit = within(dialog).getByRole("button", { name: "Link" });
    expect(submit).toBeDisabled();
    fireEvent.click(await within(dialog).findByRole("checkbox", { name: "blobert" }));
    fireEvent.click(await within(dialog).findByRole("checkbox", { name: /init-survey-1/ }));
    fireEvent.click(within(dialog).getByRole("checkbox", { name: /init-survey-2/ }));
    mockApi.referenceSource.mockResolvedValue({
      ...DEEP,
      initiatives: [{ id: INITIATIVE.id, external_id: "init-survey-1", title: INITIATIVE.title, members_added: 2 }],
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Link 3" }));
    await waitFor(() =>
      expect(mockApi.linkSourceTargets).toHaveBeenCalledWith(DEEP.source.id, {
        workspace_slugs: ["blobert"], initiative_ids: [INITIATIVE.id, second.id], question: "", queued_reason: "",
      }),
    );  // prettier-ignore
    expect(await within(section).findByRole("status")).toHaveTextContent(
      `${DEEP.source.name} now reads for blobert (pass 25 reads it again for them); ` +
        "2 open tickets joined init-survey-1 (2 settled tickets stayed out; 1 it already covers); " +
        "0 open tickets joined init-survey-2.",
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await within(section).findByRole("link", { name: /init-survey-1 Make surveys pay off/ })).toBeInTheDocument();
    expect(within(section).getByText("Why 1 ticket did not join")).toBeInTheDocument();
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

  it("keeps the modal open and names a failed link", async () => {
    mockApi.linkSourceTargets.mockRejectedValue(new ApiError(404, "No initiative 'init-x'"));
    renderAt(`/memory/library/${DEEP.source.id}`);
    const section = await screen.findByRole("region", { name: /Initiatives following it/ });
    fireEvent.click(within(section).getByRole("button", { name: "Link workspaces and initiatives…" }));
    const dialog = await screen.findByRole("dialog", { name: `Link to ${DEEP.source.name}` });
    fireEvent.click(await within(dialog).findByRole("checkbox", { name: /init-survey-1/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Link 1" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/Nothing was linked: .*No initiative 'init-x'/);
    expect(useToastStore.getState().toasts.map((t) => t.title)).toContain("Nothing was linked");
  });

  it("points to the Initiatives page when there are none", async () => {
    mockApi.initiatives.mockResolvedValue([]);
    renderAt(`/memory/library/${DEEP.source.id}`);
    const section = await screen.findByRole("region", { name: /Initiatives following it/ });
    fireEvent.click(within(section).getByRole("button", { name: "Link workspaces and initiatives…" }));
    const dialog = await screen.findByRole("dialog", { name: `Link to ${DEEP.source.name}` });
    expect(await within(dialog).findByRole("link", { name: "Start one on the Initiatives page" })).toHaveAttribute(
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
    next: { source_id: "q1", name: "retry-kit", ordinal: 1, mode: "initial", waiting: "", can_start_now: true },
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
    mockApi.reviewSourceNow.mockResolvedValue({ started: true, ordinal: 1, carrier: "lg-survey-9", waiting: "", refused: "" , workspaces_added: [], initiatives_linked: [] });
    renderAt("/memory/library?view=queue");
    const status = await screen.findByRole("status", { name: "Review queue" });
    expect(status).toHaveTextContent("Next review retry-kit, pass 1 (1 more after it) — starts at the next check, within 2 minutes.");
    fireEvent.click(within(status).getByRole("button", { name: "Review now" }));
    await waitFor(() => expect(mockApi.reviewSourceNow).toHaveBeenCalledWith("q1", NOTHING_MORE));
    await waitFor(() => expect(useToastStore.getState().toasts[0]?.title).toBe("Reviewing retry-kit"));
  });

  it("says what holds the next review, and offers no button that cannot help", async () => {
    const waiting = "no open ticket in loregarden is tagged `reference-survey-carrier`; tag the feature survey passes should be filed under";
    mockApi.referenceLibrary.mockResolvedValue({
      ...listOf([queued("q1", "retry-kit")]),
      schedule: schedule({ next: { source_id: "q1", name: "retry-kit", ordinal: 1, mode: "initial", waiting, can_start_now: false } }),
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
      started: false, ordinal: 1, carrier: null, waiting: "1 pass(es) running, at the limit of 1", refused: "", workspaces_added: [], initiatives_linked: [],
    });  // prettier-ignore
    renderAt("/memory/library/q2?view=queue");
    const detail = await screen.findByRole("region", { name: "later-kit" });
    expect(await within(detail).findByText(/Pass 1 is 2nd in line\./)).toBeInTheDocument();
    fireEvent.click(within(detail).getByRole("button", { name: "Review now…" }));
    const dialog = await screen.findByRole("dialog", { name: "Review later-kit now" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Review now" }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts[0]).toMatchObject({
        title: "later-kit is first in line but has not started",
        message: "Pass 1 is waiting: 1 pass(es) running, at the limit of 1.",
      }),
    );
  });

  it("reviews a surveyed source again against a question it can edit", async () => {
    mockApi.reviewSourceNow.mockResolvedValue({ started: true, ordinal: 25, carrier: "lg-survey-10", waiting: "", refused: "" , workspaces_added: [], initiatives_linked: [] });
    renderAt(`/memory/library/${DEEP.source.id}`);
    fireEvent.click(await screen.findByRole("button", { name: "Review again now…" }));
    const question = screen.getByRole("textbox", { name: /What should this review answer\?/ });
    fireEvent.change(question, { target: { value: "What changed in their verdict ledger?" } });
    fireEvent.click(screen.getByRole("button", { name: "Review now" }));
    await waitFor(() =>
      expect(mockApi.reviewSourceNow).toHaveBeenCalledWith(DEEP.source.id, {
        question: "What changed in their verdict ledger?",
        workspace_slugs: [],
        initiative_ids: [],
      }),
    );
  });
});

describe("adding a reference", () => {
  function fill(form: HTMLElement) {
    fireEvent.change(within(form).getByRole("textbox", { name: "URL" }), { target: { value: "https://github.com/acme/retry-kit" } });
    fireEvent.change(within(form).getByRole("textbox", { name: /What should the survey answer from it\?/ }), {
      target: { value: "How do they retry?" },
    });
    fireEvent.change(within(form).getByRole("textbox", { name: /Why this one\?/ }), { target: { value: "A retry ledger" } });
  }

  it("adds a repository straight to the review queue and opens it", async () => {
    const added = row({ id: "new1", name: "retry-kit", state: "queued", approval_id: null });
    mockApi.addReferenceSource.mockResolvedValue({ source: added, attached: ["loregarden"], scoped_pass: null, initiatives_linked: [] });
    renderAt("/memory/library?view=queue");
    const toggle = await screen.findByRole("button", { name: "Add a reference" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(await screen.findByRole("dialog", { name: "Add a reference" })).toBeInTheDocument();
    const form = screen.getByRole("form", { name: "Add a reference" });
    const submit = within(form).getByRole("button", { name: "Add to queue" });
    expect(submit).toBeDisabled();
    fill(form);
    fireEvent.click(submit);
    await waitFor(() =>
      expect(mockApi.addReferenceSource).toHaveBeenCalledWith({
        workspace_slugs: ["loregarden"], kind: "repo", url: "https://github.com/acme/retry-kit",
        question: "How do they retry?", queued_reason: "A retry ledger", initiative_ids: [],
      }),
    );  // prettier-ignore
    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent("/memory/library/new1"));
    // A person adding it is its admission: nothing waits in the inbox.
    expect(useUiStore.getState().inboxOpen).toBe(false);
    expect(useToastStore.getState().toasts[0]?.title).toBe("Added retry-kit to the review queue");
    expect(screen.queryByRole("form", { name: "Add a reference" })).not.toBeInTheDocument();
  });

  it("keeps the form and says why when the URL is refused", async () => {
    mockApi.addReferenceSource.mockRejectedValue(new ApiError(422, "Not a repository URL this survey can clone"));
    renderAt("/memory/library?view=queue");
    fireEvent.click(await screen.findByRole("button", { name: "Add a reference" }));
    const form = screen.getByRole("form", { name: "Add a reference" });
    fill(form);
    fireEvent.click(within(form).getByRole("button", { name: "Add to queue" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(/Not added: .*Not a repository URL/);
    fireEvent.keyDown(within(form).getByRole("textbox", { name: "URL" }), { key: "Escape" });
    expect(screen.queryByRole("form", { name: "Add a reference" })).not.toBeInTheDocument();
  });

  it("names a source already refused briefly and links to it", async () => {
    const message =
      "https://github.com/acme/refused is already in the library, refused: GPL-3.0. Open it to read the full reason, or to review it again.";
    mockApi.addReferenceSource.mockRejectedValue(new ApiError(409, message, { message, source_id: "src_refused" }));
    renderAt("/memory/library?view=queue");
    fireEvent.click(await screen.findByRole("button", { name: "Add a reference" }));
    const form = screen.getByRole("form", { name: "Add a reference" });
    fill(form);
    fireEvent.click(within(form).getByRole("button", { name: "Add to queue" }));
    const alert = await within(form).findByRole("alert");
    expect(alert).toHaveTextContent(/Not added: .*refused: GPL-3\.0\./);
    const open = within(alert).getByRole("link", { name: "Open it in the library" });
    expect(open).toHaveAttribute("href", expect.stringContaining("/memory/library/src_refused"));
    fireEvent.click(open);
    expect(screen.queryByRole("form", { name: "Add a reference" })).not.toBeInTheDocument();
  });

  it("adds several pasted URLs at once and keeps only the failed ones to fix", async () => {
    const proposed = row({ id: "n1", name: "one", state: "awaiting_approval", approval_id: "card-1" });
    mockApi.addReferenceSources.mockResolvedValue([
      { url: "https://github.com/acme/one", outcome: "queued", kind: "repo", source: proposed, detail: "", initiatives_linked: [] },
      { url: "https://docs.example.dev", outcome: "already", kind: "site", source: proposed, detail: "", initiatives_linked: [] },
      { url: "ftp://nope", outcome: "failed", kind: "site", source: null, detail: "A site is named by an https URL", initiatives_linked: [] },
    ]);
    renderAt("/memory/library?view=queue");
    fireEvent.click(await screen.findByRole("button", { name: "Add a reference" }));
    const form = screen.getByRole("form", { name: "Add a reference" });
    fireEvent.click(within(form).getByRole("button", { name: "Several" }));
    expect(within(form).getByRole("button", { name: "Several" })).toHaveAttribute("aria-pressed", "true");
    const box = within(form).getByRole("textbox", { name: "URLs" });
    fireEvent.change(box, {
      target: { value: "https://github.com/acme/one,https://docs.example.dev\tftp://nope\n\nhttps://github.com/acme/one\n" },
    });
    expect(within(form).getByText(/3 URLs\./)).toBeInTheDocument();
    fireEvent.change(within(form).getByRole("textbox", { name: /What should the survey answer from them\?/ }), {
      target: { value: "How do they retry?" },
    });
    fireEvent.change(within(form).getByRole("textbox", { name: /Why these\?/ }), { target: { value: "Retry ledgers" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add 3 to queue" }));
    await waitFor(() =>
      expect(mockApi.addReferenceSources).toHaveBeenCalledWith({
        workspace_slugs: ["loregarden"], kind: null, question: "How do they retry?", queued_reason: "Retry ledgers", initiative_ids: [],
        urls: ["https://github.com/acme/one", "https://docs.example.dev", "ftp://nope"],
      }),
    );  // prettier-ignore
    const results = await within(form).findByRole("list", { name: "What each URL did" });
    expect(within(results).getAllByRole("listitem")).toHaveLength(3);
    expect(within(results).getByText(/not added: A site is named by an https URL/)).toBeInTheDocument();
    expect(box).toHaveValue("ftp://nope");
    expect(within(results).getByRole("link", { name: "https://docs.example.dev" })).toHaveAttribute(
      "href",
      "/memory/library/n1?view=queue",
    );
    expect(useUiStore.getState().inboxOpen).toBe(false);
    expect(useToastStore.getState().toasts[0]).toMatchObject({
      title: "Added 1 of 3 to the review queue",
      message: "1 already in the library; 1 could not be added and is left in the box, the reason beside each.",
    });
  });

  it("keeps only the URLs of a pasted article, switching to Several from the one-URL field", async () => {
    renderAt("/memory/library?view=queue");
    fireEvent.click(await screen.findByRole("button", { name: "Add a reference" }));
    const form = screen.getByRole("form", { name: "Add a reference" });
    const article = "1. Twenty – a CRM\n• GitHub: https://github.com/twentyhq/twenty\n2. Harper – grammar\n• GitHub: https://github.com/Automattic/harper.";
    fireEvent.paste(within(form).getByRole("textbox", { name: "URL" }), { clipboardData: { getData: () => article } });
    expect(within(form).getByRole("button", { name: "Several" })).toHaveAttribute("aria-pressed", "true");
    const box = within(form).getByRole("textbox", { name: "URLs" });
    expect(box).toHaveValue("https://github.com/twentyhq/twenty\nhttps://github.com/Automattic/harper");
    expect(within(form).getByText(/Kept the 2 URLs in what you pasted/)).toBeInTheDocument();
    fireEvent.paste(box, { clipboardData: { getData: () => "Also see https://docs.example.dev today" } });
    expect(within(form).getByRole("button", { name: "Add 3 to queue" })).toBeEnabled();
  });

  it("refuses more URLs than one bulk add takes, before sending", async () => {
    renderAt("/memory/library?view=queue");
    fireEvent.click(await screen.findByRole("button", { name: "Add a reference" }));
    const form = screen.getByRole("form", { name: "Add a reference" });
    fireEvent.click(within(form).getByRole("button", { name: "Several" }));
    const many = Array.from({ length: 51 }, (_, i) => `https://github.com/a/r${i}`).join("\n");
    fireEvent.change(within(form).getByRole("textbox", { name: "URLs" }), { target: { value: many } });
    expect(within(form).getByText("51 URLs; at most 50 at a time.")).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Add 51 to queue" })).toBeDisabled();
  });

  it("queues a listed candidate from its own page, URL fixed", async () => {
    const candidate = row({ id: "c1", name: "agent-mux", state: "candidate", url: "https://github.com/openwork/agent-mux" });
    mockApi.referenceLibrary.mockResolvedValue(listOf([candidate]));
    mockApi.referenceSource.mockResolvedValue({ ...DEEP, source: candidate, passes: [], tickets: [], unresolved_refs: [], record: "" });
    renderAt("/memory/library/c1");
    fireEvent.click(await screen.findByRole("button", { name: "Keep, with workspaces or a question…" }));
    const form = screen.getByRole("form", { name: "Add a reference" });
    expect(within(form).getByRole("textbox", { name: "URL" })).toHaveAttribute("readonly");
    expect(within(form).getByRole("textbox", { name: "URL" })).toHaveValue("https://github.com/openwork/agent-mux");
  });
});

describe("who a review is for", () => {
  it("adds a reference for another workspace too and feeds an initiative, hiding archived workspaces", async () => {
    const added = row({ id: "new1", name: "retry-kit", state: "queued", approval_id: null });
    mockApi.addReferenceSource.mockResolvedValue({
      source: added, attached: ["loregarden", "blobert"], scoped_pass: null, initiatives_linked: ["init-survey-1"],
    });  // prettier-ignore
    renderAt("/memory/library?view=queue");
    fireEvent.click(await screen.findByRole("button", { name: "Add a reference" }));
    const form = screen.getByRole("form", { name: "Add a reference" });
    const readFor = within(form).getByRole("group", { name: "Read it for" });
    expect(await within(readFor).findByRole("checkbox", { name: /loregarden/ })).toBeDisabled();
    expect(within(readFor).queryByRole("checkbox", { name: /old-one/ })).not.toBeInTheDocument();
    fireEvent.click(within(readFor).getByRole("checkbox", { name: "blobert" }));
    const feed = within(form).getByRole("group", { name: "Feed its tickets into" });
    fireEvent.click(await within(feed).findByRole("checkbox", { name: /init-survey-1/ }));
    fireEvent.change(within(form).getByRole("textbox", { name: "URL" }), { target: { value: "https://github.com/acme/retry-kit" } });
    fireEvent.change(within(form).getByRole("textbox", { name: /What should the survey answer from it\?/ }), { target: { value: "q" } });
    fireEvent.change(within(form).getByRole("textbox", { name: /Why this one\?/ }), { target: { value: "r" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add to queue" }));
    await waitFor(() =>
      expect(mockApi.addReferenceSource).toHaveBeenCalledWith({
        workspace_slugs: ["loregarden", "blobert"], kind: "repo", url: "https://github.com/acme/retry-kit",
        question: "q", queued_reason: "r", initiative_ids: [INITIATIVE.id],
      }),
    );  // prettier-ignore
  });

  it("reviews a surveyed source for another workspace and an initiative, with what it already serves fixed", async () => {
    mockApi.reviewSourceNow.mockResolvedValue({
      started: true, ordinal: 25, carrier: "lg-survey-10", waiting: "", refused: "",
      workspaces_added: ["blobert"], initiatives_linked: ["init-survey-1"],
    });  // prettier-ignore
    renderAt(`/memory/library/${DEEP.source.id}`);
    fireEvent.click(await screen.findByRole("button", { name: "Review again now…" }));
    const form = screen.getByRole("form", { name: `Review ${DEEP.source.name} now` });
    expect(await within(form).findByRole("checkbox", { name: /loregarden/ })).toBeChecked();
    fireEvent.click(within(form).getByRole("checkbox", { name: "blobert" }));
    fireEvent.click(await within(form).findByRole("checkbox", { name: /init-survey-1/ }));
    fireEvent.change(within(form).getByRole("textbox", { name: /What should this review answer\?/ }), {
      target: { value: "What changed?" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Review now" }));
    await waitFor(() =>
      expect(mockApi.reviewSourceNow).toHaveBeenCalledWith(DEEP.source.id, {
        question: "What changed?", workspace_slugs: ["blobert"], initiative_ids: [INITIATIVE.id],
      }),
    );  // prettier-ignore
    await waitFor(() =>
      expect(useToastStore.getState().toasts[0]?.message).toMatch(
        /it now reads for blobert too; init-survey-1 now follow it\./,
      ),
    );
  });

  it("says when workspaces could not be listed instead of offering none", async () => {
    mockApi.workspaces.mockRejectedValue(new ApiError(500, "boom"));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <ReferenceTargetsPicker
            fixedWorkspaces={["loregarden"]}
            workspaces={[]}
            onWorkspaces={jest.fn()}
            initiatives={[]}
            onInitiatives={jest.fn()}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/Could not list workspaces .*it reads for loregarden only\./)).toBeInTheDocument();
  });
});

describe("linking is enough to start a review", () => {
  it("queues a reference with only a URL, the question and reason left empty", async () => {
    const added = row({ id: "new2", name: "bare", state: "queued", approval_id: null });
    mockApi.addReferenceSource.mockResolvedValue({ source: added, attached: ["loregarden"], scoped_pass: null, initiatives_linked: [] });
    renderAt("/memory/library?view=queue");
    fireEvent.click(await screen.findByRole("button", { name: "Add a reference" }));
    const form = screen.getByRole("form", { name: "Add a reference" });
    fireEvent.change(within(form).getByRole("textbox", { name: "URL" }), { target: { value: "https://github.com/acme/bare" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add to queue" }));
    await waitFor(() =>
      expect(mockApi.addReferenceSource).toHaveBeenCalledWith(expect.objectContaining({ question: "", queued_reason: "" })),
    );
  });

  it("starts a review of a surveyed source without a question", async () => {
    mockApi.reviewSourceNow.mockResolvedValue({
      started: true, ordinal: 25, carrier: "lg-survey-10", waiting: "", refused: "", workspaces_added: [], initiatives_linked: [],
    });  // prettier-ignore
    mockApi.referenceSource.mockResolvedValue({ ...DEEP, source: { ...DEEP.source, question: "" }, passes: DEEP.passes.map((p) => ({ ...p, question: "" })) });
    renderAt(`/memory/library/${DEEP.source.id}`);
    fireEvent.click(await screen.findByRole("button", { name: "Review again now…" }));
    const form = screen.getByRole("form", { name: `Review ${DEEP.source.name} now` });
    fireEvent.click(within(form).getByRole("button", { name: "Review now" }));
    await waitFor(() => expect(mockApi.reviewSourceNow).toHaveBeenCalledWith(DEEP.source.id, NOTHING_MORE));
  });
});

describe("what a source is, and a refused one read again", () => {
  it("says what each source is in the list and in full on its page, with what GitHub says about it", async () => {
    const described = LIBRARY.sources.find((r) => r.description && r.language && r.stars) as LibraryRow;
    mockApi.referenceSource.mockResolvedValue({ ...DEEP, source: described });
    renderAt(`/memory/library/${described.id}?view=all`);
    const detail = await screen.findByRole("region", { name: described.name });
    expect(within(detail).getByText(described.description as string)).toBeInTheDocument();
    expect(within(detail).getByText(new RegExp(`^${described.language} · ★`))).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getAllByTitle(/: a toolkit that /).length).toBeGreaterThan(50);
    expect(findUsabilityProblems(document.body)).toEqual([]);
  });

  it("says a source is not described yet, or why its page could not be read", async () => {
    const pending = row({ id: "p1", name: "pending", description: null, describe_error: "" });
    mockApi.referenceLibrary.mockResolvedValue(listOf([pending]));
    mockApi.referenceSource.mockResolvedValue({ ...DEEP, source: pending });
    renderAt("/memory/library/p1");
    expect(await screen.findByText(/Not described yet; the background refresh reads it within the hour/)).toBeInTheDocument();
  });

  it("says why a page could not be read, and when a source describes itself in no words", async () => {
    const dead = row({ id: "d1", name: "dead", description: null, describe_error: "HTTP 404" });
    mockApi.referenceLibrary.mockResolvedValue(listOf([dead]));
    mockApi.referenceSource.mockResolvedValue({ ...DEEP, source: dead });
    const { unmount } = renderAt("/memory/library/d1");
    expect(await screen.findByText("Could not read what it is: HTTP 404.")).toBeInTheDocument();
    unmount();
    const silent = row({ id: "s1", name: "silent", description: "" });
    mockApi.referenceLibrary.mockResolvedValue(listOf([silent]));
    mockApi.referenceSource.mockResolvedValue({ ...DEEP, source: silent });
    renderAt("/memory/library/s1");
    expect(await screen.findByText("It describes itself in no words.")).toBeInTheDocument();
  });

  it("reviews a refused source again only with a reason, and sends it", async () => {
    const refused = row({ id: "r1", name: "gpl-kit", state: "refused" });
    mockApi.referenceLibrary.mockResolvedValue(listOf([refused]));
    mockApi.referenceSource.mockResolvedValue({ ...DEEP, source: refused, refused_reason: "Licence gate: GPL-3.0 excludes it." });
    mockApi.reviewSourceNow.mockResolvedValue({
      started: false, ordinal: 1, carrier: null, waiting: "", refused: "Licence gate: GPL-3.0 excludes it.",
      workspaces_added: [], initiatives_linked: [],
    });  // prettier-ignore
    renderAt("/memory/library/r1");
    fireEvent.click(await screen.findByRole("button", { name: "Review again…" }));
    const dialog = await screen.findByRole("dialog", { name: "Review gpl-kit now" });
    expect(within(dialog).getByText("It was refused: Licence gate: GPL-3.0 excludes it.")).toBeInTheDocument();
    const submit = within(dialog).getByRole("button", { name: "Review now" });
    expect(submit).toBeDisabled();
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Why should it be read after all?" }), {
      target: { value: "They relicensed to MIT in v2." },
    });
    fireEvent.click(submit);
    await waitFor(() =>
      expect(mockApi.reviewSourceNow).toHaveBeenCalledWith("r1", expect.objectContaining({ reason: "They relicensed to MIT in v2." })),
    );
    // Refused again by the licence gate: said plainly, not as a silent no-op.
    await waitFor(() => expect(useToastStore.getState().toasts[0]?.title).toBe("gpl-kit was not reviewed"));
  });
});

describe("quick-add topics and finding sources", () => {
  it("fills what to look for from a topic, and clears only what it filled", async () => {
    renderAt("/memory/library?view=queue");
    fireEvent.click(await screen.findByRole("button", { name: "Add a reference" }));
    const form = screen.getByRole("form", { name: "Add a reference" });
    const topics = await within(form).findByRole("group", { name: "What to look for" });
    const memory = within(topics).getByRole("button", { name: "Improved memory layer" });
    fireEvent.click(memory);
    expect(memory).toHaveAttribute("aria-pressed", "true");
    const question = within(form).getByRole("textbox", { name: /What should the survey answer from it\?/ });
    expect(question).toHaveValue(TOPICS[0].question);
    expect(within(form).getByRole("textbox", { name: /Why this one\?/ })).toHaveValue(TOPICS[0].why);
    fireEvent.change(question, { target: { value: "My own question" } });
    fireEvent.click(memory);
    expect(question).toHaveValue("My own question");
    expect(within(form).getByRole("textbox", { name: /Why this one\?/ })).toHaveValue("");
  });

  it("starts a search for a topic, narrowed and sized, for more workspaces", async () => {
    mockApi.startDiscovery.mockResolvedValue({
      id: "d1", topic: "memory_layer", label: "Improved memory layer", focus: "graph-backed", wanted: 10,
      workspaces: ["loregarden", "blobert"], carrier: "lg-find-1", carrier_state: "in_progress", found: 0,
      created_at: "2026-10-23T00:00:00",
    });  // prettier-ignore
    renderAt("/memory/library?view=queue");
    fireEvent.click(await screen.findByRole("button", { name: "Find sources…" }));
    const dialog = await screen.findByRole("dialog", { name: "Find sources" });
    const submit = within(dialog).getByRole("button", { name: "Find sources" });
    expect(submit).toBeDisabled();
    fireEvent.click(await within(dialog).findByRole("button", { name: "Improved memory layer" }));
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Narrow it (optional)" }), { target: { value: "graph-backed" } });
    fireEvent.change(within(dialog).getByLabelText("How many"), { target: { value: "10" } });
    fireEvent.click(await within(dialog).findByRole("checkbox", { name: "blobert" }));
    fireEvent.click(submit);
    await waitFor(() =>
      expect(mockApi.startDiscovery).toHaveBeenCalledWith({
        topic: "memory_layer", workspace_slugs: ["loregarden", "blobert"], initiative_ids: [], focus: "graph-backed", wanted: 10,
      }),
    );  // prettier-ignore
    await waitFor(() => expect(useToastStore.getState().toasts[0]?.title).toBe("Searching for Improved memory layer references"));
  });

  it("says why a search did not start, and keeps the dialog", async () => {
    mockApi.startDiscovery.mockRejectedValue(new ApiError(409, "No open ticket in loregarden is tagged `reference-survey-carrier`"));
    renderAt("/memory/library?view=queue");
    fireEvent.click(await screen.findByRole("button", { name: "Find sources…" }));
    const dialog = await screen.findByRole("dialog", { name: "Find sources" });
    fireEvent.click(await within(dialog).findByRole("button", { name: "Similar tools" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Find sources" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/Did not start: .*reference-survey-carrier/);
  });

  it("shows a running search in the queue bar and lets each find be kept or dropped from the list", async () => {
    const find = row({ id: "f1", name: "mem0", state: "awaiting_approval", approval_id: "card-f1", discovered_by: "Improved memory layer" });
    mockApi.referenceLibrary.mockResolvedValue({
      ...listOf([find]),
      discoveries: [{
        id: "d1", topic: "memory_layer", label: "Improved memory layer", focus: "", wanted: 5, workspaces: ["loregarden"],
        carrier: "lg-find-1", carrier_state: "in_progress", found: 1, created_at: "2026-10-23T00:00:00",
      }],
    });  // prettier-ignore
    mockApi.keepSource.mockResolvedValue({ ...find, state: "queued" });
    mockApi.dropSource.mockResolvedValue({ ...find, state: "refused" });
    renderAt("/memory/library?view=queue");
    const status = await screen.findByRole("status", { name: "Review queue" });
    expect(status).toHaveTextContent("Finding Improved memory layer: 1 of up to 5 found so far by lg-find-1. Keep or drop each in Queue.");
    const table = screen.getByRole("table");
    expect(within(table).getByText("found for Improved memory layer")).toBeInTheDocument();
    const decide = within(table).getByRole("group", { name: "Keep or drop mem0" });
    fireEvent.click(within(decide).getByRole("button", { name: "Keep" }));
    await waitFor(() => expect(mockApi.keepSource).toHaveBeenCalledWith("f1"));
    fireEvent.click(within(decide).getByRole("button", { name: "Drop" }));
    await waitFor(() => expect(mockApi.dropSource).toHaveBeenCalledWith("f1", ""));
  });
});
