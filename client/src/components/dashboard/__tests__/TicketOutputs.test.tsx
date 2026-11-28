import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { api } from "../../../api/client";
import type { TicketArtifactItem } from "../../../api/types";
import { useReaderStore } from "../../../state/readerStore";
import { TicketOutputs } from "../TicketOutputs";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function item(overrides: Partial<TicketArtifactItem>): TicketArtifactItem {
  return {
    id: overrides.title ?? "x",
    kind: "plan",
    title: "",
    run_id: null,
    stage_key: null,
    system: false,
    evidence_kind: "",
    commit_sha: "",
    created_at: "2026-09-20T10:05:00",
    content_bytes: 10,
    content: {},
    ...overrides,
  };
}

const ITEMS = [
  item({ title: "Plan — rollout", kind: "plan", created_at: "2026-09-20T10:05:00", content_bytes: 9000 }),
  item({ title: "Rollup suite green", kind: "evidence", created_at: "2026-09-20T10:30:00", content: { command: "pytest -q" } }),
  item({ title: "Handoff not validated", kind: "error", created_at: "2026-09-20T10:20:00" }),
  item({ title: "stage-dispatch:gate", kind: "stage_dispatch", system: true, created_at: "2026-09-20T10:40:00" }),
  item({ title: "Run context", kind: "context", system: true, created_at: "2026-09-20T10:41:00" }),
];

function renderOutputs(items = ITEMS) {
  mockApi.ticketArtifacts.mockResolvedValue({ items, total: items.length });
  mockApi.ticketLedger.mockResolvedValue({
    visits: [
      {
        stage_key: "implement",
        visit_number: 1,
        status: "succeeded",
        is_parallel: false,
        attempts: [
          {
            run_id: "r1",
            run_code: "run_a",
            agent_id: "backend_implementer",
            skill_name: "",
            status: "succeeded",
            started_at: "2026-09-20T10:00:00+00:00",
            finished_at: "2026-09-20T10:45:00+00:00",
            duration_seconds: 2700,
            error_tail: "",
          },
        ],
      },
    ],
    total_runs: 1,
    reworked_stages: [],
    total_seconds: 2700,
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TicketOutputs ticketId="t1" isActive={false} />
    </QueryClientProvider>,
  );
}

function titles(): string[] {
  const [, body] = screen.getAllByRole("rowgroup");
  return within(body)
    .getAllByRole("row")
    .map((row) => within(row).getAllByRole("cell")[2].querySelector(".out-title-text")?.textContent ?? "");
}

beforeEach(() => {
  jest.clearAllMocks();
  useReaderStore.getState().close();
});

it("is a table of work outputs, newest first, with the platform's bookkeeping hidden", async () => {
  renderOutputs();

  await screen.findByRole("table");
  expect(screen.getAllByRole("columnheader").map((th) => th.textContent?.replace(/[↑↓]/, ""))).toEqual([
    "Time",
    "Kind",
    "Title",
    "Stage",
    "Size",
  ]);
  expect(titles()).toEqual(["Rollup suite green", "Handoff not validated", "Plan — rollout"]);
  expect(screen.getByRole("columnheader", { name: /Time/ })).toHaveAttribute("aria-sort", "descending");
});

it("shows the system records when asked", async () => {
  renderOutputs();

  fireEvent.click(await screen.findByRole("checkbox", { name: "Show 2 system records" }));
  expect(titles()).toContain("stage-dispatch:gate");
  expect(titles()).toHaveLength(5);
});

it("names the stage that was running when each row landed", async () => {
  renderOutputs();

  const row = (await screen.findByText("Plan — rollout")).closest("tr")!;
  expect(within(row).getAllByRole("cell")[3]).toHaveTextContent("implement");
});

it("filters by kind and by text inside the content", async () => {
  renderOutputs();
  await screen.findByRole("table");

  fireEvent.change(screen.getByRole("combobox", { name: "Filter by kind" }), { target: { value: "error" } });
  expect(titles()).toEqual(["Handoff not validated"]);

  fireEvent.change(screen.getByRole("combobox", { name: "Filter by kind" }), { target: { value: "all" } });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "pytest" } });
  expect(titles()).toEqual(["Rollup suite green"]);
});

it("offers a way out of a filter that matches nothing", async () => {
  renderOutputs();
  await screen.findByRole("table");

  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "no such thing" } });
  expect(screen.getByText("No outputs match these filters.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(titles()).toHaveLength(3);
});

it("sorts by a column and says so", async () => {
  renderOutputs();
  await screen.findByRole("table");

  fireEvent.click(within(screen.getByRole("columnheader", { name: /Size/ })).getByRole("button"));
  expect(screen.getByRole("columnheader", { name: /Size/ })).toHaveAttribute("aria-sort", "descending");
  expect(screen.getByRole("columnheader", { name: /Time/ })).toHaveAttribute("aria-sort", "none");
  expect(titles()[0]).toBe("Plan — rollout");
});

it("opens a row in the reader from its title", async () => {
  renderOutputs();

  fireEvent.click(await screen.findByRole("button", { name: /Plan — rollout/ }));
  expect(useReaderStore.getState().document?.title).toBe("Plan — rollout");
});

it("says what will appear when there is nothing yet", async () => {
  renderOutputs([]);

  expect(await screen.findByText("No outputs yet")).toBeInTheDocument();
});

it("names the failure and retries it", async () => {
  mockApi.ticketArtifacts.mockRejectedValue(new Error("feed down"));
  mockApi.ticketLedger.mockResolvedValue({ visits: [], total_runs: 0, reworked_stages: [], total_seconds: 0 });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TicketOutputs ticketId="t1" isActive={false} />
    </QueryClientProvider>,
  );

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Could not load this ticket’s outputs");
  mockApi.ticketArtifacts.mockResolvedValue({ items: ITEMS, total: ITEMS.length });
  fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("table")).toBeInTheDocument();
});

it("opens a row placed: its stage and commit in the subtitle, its verdict as a badge", async () => {
  renderOutputs([
    item({
      title: "Stage report — gate",
      kind: "context",
      commit_sha: "4a115b9e9bff31ad",
      created_at: "2026-09-20T10:30:00",
      content: { stage_key: "gate", status: "pass", confidence: 0.91 },
    }),
  ]);

  fireEvent.click(await screen.findByRole("button", { name: /Stage report — gate/ }));
  const opened = useReaderStore.getState().document;
  expect(opened?.subtitle).toMatch(/^context · implement · .* · commit 4a115b9e · 10 B$/);
  expect(opened?.badge).toEqual({ text: "pass · 0.91", tone: "good" });
});

// --- the Size column's `partial` marker, and search honesty ----------------
//
// lg-milestone-that-1424 AC11/AC12. The feed is bounded to 16 KiB a row, so a
// row's `content` may be only the head of its log. The question every surface
// here has to answer, in the operator's words: "am I looking at the whole of
// this output, or only its beginning?"

const SHORTENED = item({
  title: "Land verification — failed — loregarden/lg-r-1",
  kind: "land_verification",
  created_at: "2026-09-20T10:50:00",
  content_bytes: 1_048_576,
  content_truncated: true,
  content_shown_bytes: 16_384,
  content: { status: "failed", stage_key: "done", exit_code: 1, output: "FAILED test_a.py\n" },
});

function sizeCell(title: string): HTMLElement {
  const row = screen.getByText(title).closest("tr")!;
  return within(row).getAllByRole("cell")[4];
}

it("marks a shortened row in the Size column, keeping the full stored size", async () => {
  renderOutputs([SHORTENED, ...ITEMS]);
  await screen.findByRole("table");

  const cell = sizeCell("Land verification — failed — loregarden/lg-r-1");
  // The cell keeps the FULL size, so the Size sort and the reader subtitle stay
  // truthful; the tag is what says only part of it is in this table.
  expect(cell).toHaveTextContent("1.0 MB");
  expect(
    within(cell).getByLabelText(
      "partial — only the first 16.0 KB is in this table; open the row to read all 1.0 MB",
    ),
  ).toBeInTheDocument();
});

it("marks only the rows that were shortened", async () => {
  renderOutputs([SHORTENED, ...ITEMS]);
  await screen.findByRole("table");

  expect(sizeCell("Plan — rollout")).toHaveTextContent("8.8 KB");
  expect(within(sizeCell("Plan — rollout")).queryByText("partial")).not.toBeInTheDocument();
  expect(screen.getAllByText("partial")).toHaveLength(1);
});

it("adds no second control to a shortened row — the title is still the way in", async () => {
  renderOutputs([SHORTENED]);
  await screen.findByRole("table");

  const [, body] = screen.getAllByRole("rowgroup");
  const row = within(body).getAllByRole("row")[0];
  expect(within(row).getAllByRole("button")).toHaveLength(1);
  expect(within(row).queryAllByRole("link")).toHaveLength(0);
});

it("opens a shortened row with the ticket it belongs to, so the rest can be fetched", async () => {
  renderOutputs([SHORTENED]);

  fireEvent.click(await screen.findByRole("button", { name: /Land verification/ }));

  const opened = useReaderStore.getState().document;
  expect(opened?.id).toBe(SHORTENED.id);
  expect(opened?.partial).toEqual({
    ticketId: "t1",
    artifactId: SHORTENED.id,
    shownBytes: 16_384,
    totalBytes: 1_048_576,
  });
});

it("stops promising a content search it no longer has", async () => {
  renderOutputs();
  await screen.findByRole("table");

  const search = screen.getByRole("searchbox");
  expect(search).toHaveAttribute("placeholder", "Search titles, kinds and stages");
  // Nothing in the toolbar may claim the whole content any more: the search
  // reaches only the part of a shortened row that is in this table.
  expect(search.getAttribute("aria-label") ?? "").not.toMatch(/content/i);
});

it("says how many searched rows it could only search part of", async () => {
  renderOutputs([SHORTENED, ...ITEMS]);
  await screen.findByRole("table");

  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "verification" } });

  // "it failed" and "there is nothing" must not collapse into one answer: the
  // operator pastes a failing test name, gets nothing, and has to be told why.
  expect(
    screen.getByText(
      "1 of these rows were searched only to the part of them shown in this table — open a row to search the whole of it.",
    ),
  ).toBeInTheDocument();
});

it("keeps the toolbar quiet when no query is active", async () => {
  renderOutputs([SHORTENED, ...ITEMS]);
  await screen.findByRole("table");

  expect(screen.queryByText(/searched only to the part/)).not.toBeInTheDocument();
});

it("says nothing about partial search when no visible row was shortened", async () => {
  renderOutputs();
  await screen.findByRole("table");

  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "plan" } });

  expect(screen.queryByText(/searched only to the part/)).not.toBeInTheDocument();
});

// --- adversarial cases for the search note (test-break stage) --------------
//
// AC12's note exists so that "it failed" and "there is nothing" do not collapse
// into one answer. A note that counts the wrong rows reintroduces the same
// problem with a number attached.

const SHORTENED_TWO = item({
  title: "Land verification — failed — loregarden/lg-r-2",
  kind: "land_verification",
  created_at: "2026-09-20T10:55:00",
  content_bytes: 2_097_152,
  content_truncated: true,
  content_shown_bytes: 16_384,
  content: { status: "failed", stage_key: "done", exit_code: 1, output: "FAILED test_b.py\n" },
});

it("stays quiet when a query matches nothing at all", async () => {
  renderOutputs([SHORTENED, ...ITEMS]);
  await screen.findByRole("table");

  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "no such thing" } });

  // Zero visible rows means zero rows were searched only in part. "0 of these
  // rows were searched only to the part of them shown" beside "No outputs
  // match these filters" is the empty state arguing with itself.
  expect(screen.getByText("No outputs match these filters.")).toBeInTheDocument();
  expect(screen.queryByText(/searched only to the part/)).not.toBeInTheDocument();
});

it("counts only the shortened rows the query actually left on screen", async () => {
  renderOutputs([SHORTENED, SHORTENED_TWO, ...ITEMS]);
  await screen.findByRole("table");

  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "lg-r-2" } });

  // One of the two shortened rows is filtered out: a note counting every
  // shortened row in the feed, rather than the visible ones, says 2 here.
  expect(titles()).toEqual(["Land verification — failed — loregarden/lg-r-2"]);
  expect(
    screen.getByText(
      "1 of these rows were searched only to the part of them shown in this table — open a row to search the whole of it.",
    ),
  ).toBeInTheDocument();
});

it("says nothing when the visible rows were all searched whole", async () => {
  renderOutputs([SHORTENED, ...ITEMS]);
  await screen.findByRole("table");

  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "rollout" } });

  // The one shortened row is filtered out, so the search that just ran reached
  // the whole of everything it looked at, and has nothing to apologise for.
  expect(titles()).toEqual(["Plan — rollout"]);
  expect(screen.queryByText(/searched only to the part/)).not.toBeInTheDocument();
});

it("marks a shortened row by its own shown size, not by a shared constant", async () => {
  renderOutputs([SHORTENED_TWO]);
  await screen.findByRole("table");

  // Two shortened rows show different amounts; the tag's accessible name is the
  // one place the per-row number is accurate, which is why AC12 keeps the byte
  // size out of the toolbar note.
  const cell = sizeCell("Land verification — failed — loregarden/lg-r-2");
  expect(cell).toHaveTextContent("2.0 MB");
  expect(
    within(cell).getByLabelText(
      "partial — only the first 16.0 KB is in this table; open the row to read all 2.0 MB",
    ),
  ).toBeInTheDocument();
});
