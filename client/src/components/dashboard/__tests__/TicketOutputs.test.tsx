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
  fireEvent.change(screen.getByRole("searchbox", { name: "Search outputs" }), { target: { value: "pytest" } });
  expect(titles()).toEqual(["Rollup suite green"]);
});

it("offers a way out of a filter that matches nothing", async () => {
  renderOutputs();
  await screen.findByRole("table");

  fireEvent.change(screen.getByRole("searchbox", { name: "Search outputs" }), { target: { value: "no such thing" } });
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
