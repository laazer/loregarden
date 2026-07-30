import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { TicketDetail } from "../../api/client";
import * as apiClient from "../../api/client";
import type { LedgerAttempt, LedgerVisit, RunLog, TicketLedger } from "../../api/types";
import { useUiStore } from "../../state/uiStore";
import { LogsPanel } from "../LogsPanel";

jest.mock("../../api/client", () => jest.requireActual("../../test/apiClientMock"));

const api = apiClient.api as unknown as {
  ticketLedger: jest.Mock;
  runLog: jest.Mock;
};

// Requirements 1, 2, 4, 5, 7 — LogsPanel is the surface that owns which lane
// is selected, derives running lanes from the ledger, and falls back to the
// existing ticket-level feed. RunningLaneTabs' own rendering contract is
// covered in isolation by RunningLaneTabs.test.tsx; these tests exercise the
// integration behavior the acceptance criteria describe end to end.

function makeTicket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: "t1",
    external_id: "t1",
    stages: [],
    blocking_issues: "",
    artifacts: { logs: [{ time: "10:00:00", tag: "OUT", text: "ticket-level line" }], live: null },
    ...overrides,
  } as unknown as TicketDetail;
}

function attempt(overrides: Partial<LedgerAttempt> = {}): LedgerAttempt {
  return {
    run_id: "r1",
    run_code: "run_a",
    agent_id: "backend_implementer",
    skill_name: "",
    status: "running",
    started_at: null,
    finished_at: null,
    duration_seconds: null,
    ...overrides,
  };
}

function visit(overrides: Partial<LedgerVisit> = {}): LedgerVisit {
  return {
    stage_key: "implement",
    visit_number: 1,
    status: "running",
    is_parallel: true,
    attempts: [attempt()],
    ...overrides,
  };
}

function ledger(visits: LedgerVisit[]): TicketLedger {
  return { visits, total_runs: visits.length, reworked_stages: [], total_seconds: 0 };
}

function runLogFor(runId: string, text: string): RunLog {
  return {
    id: runId,
    run_code: `run_${runId}`,
    agent_id: "backend_implementer",
    skill_name: "",
    stage_key: "implement",
    status: "running",
    command: "",
    started_at: null,
    finished_at: null,
    lines: [{ time: "10:00:00", tag: "OUT", text }],
    live: null,
    stderr: "",
  };
}

function renderPanel(ticket: TicketDetail, queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <LogsPanel ticket={ticket} />
      </QueryClientProvider>,
    ),
  };
}

async function refetchLedger(queryClient: QueryClient, ticketId: string) {
  await act(async () => {
    await queryClient.invalidateQueries({ queryKey: ["ticket-ledger", ticketId] });
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  useUiStore.setState({ autoFollowByRunId: {} });
  api.runLog.mockImplementation((runId: string) => Promise.resolve(runLogFor(runId, `content for ${runId}`)));
});

it("renders no strip and the existing ticket-level feed when there are no running lanes", async () => {
  api.ticketLedger.mockResolvedValue(ledger([visit({ attempts: [attempt({ status: "completed" })] })]));
  renderPanel(makeTicket());

  expect(await screen.findByText("ticket-level line")).toBeInTheDocument();
  expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
});

it("treats running and queued attempts inside a parallel visit as running lanes", async () => {
  api.ticketLedger.mockResolvedValue(
    ledger([
      visit({
        is_parallel: true,
        attempts: [
          attempt({ run_id: "r1", agent_id: "planner", status: "running" }),
          attempt({ run_id: "r2", agent_id: "gatekeeper", status: "queued" }),
        ],
      }),
    ]),
  );
  renderPanel(makeTicket());

  const tabs = await screen.findAllByRole("tab");
  expect(tabs).toHaveLength(2);
});

it("excludes an active attempt that belongs to a non-parallel visit", async () => {
  api.ticketLedger.mockResolvedValue(
    ledger([
      visit({
        stage_key: "plan",
        is_parallel: true,
        attempts: [attempt({ run_id: "r1", agent_id: "planner", status: "running" })],
      }),
      visit({
        stage_key: "implement",
        is_parallel: false,
        attempts: [attempt({ run_id: "r2", agent_id: "backend_implementer", status: "running" })],
      }),
    ]),
  );
  renderPanel(makeTicket());

  const tabs = await screen.findAllByRole("tab");
  expect(tabs).toHaveLength(1);
  expect(tabs[0]).toHaveTextContent("planner");
});

it("excludes completed and failed attempts even inside a parallel visit", async () => {
  api.ticketLedger.mockResolvedValue(
    ledger([
      visit({
        is_parallel: true,
        attempts: [
          attempt({ run_id: "r1", agent_id: "planner", status: "running" }),
          attempt({ run_id: "r2", agent_id: "gatekeeper", status: "completed" }),
          attempt({ run_id: "r3", agent_id: "static_qa", status: "failed" }),
        ],
      }),
    ]),
  );
  renderPanel(makeTicket());

  const tabs = await screen.findAllByRole("tab");
  expect(tabs).toHaveLength(1);
  expect(tabs[0]).toHaveTextContent("planner");
});

it("orders tabs by visit order, then attempt order within a visit", async () => {
  api.ticketLedger.mockResolvedValue(
    ledger([
      visit({
        stage_key: "plan",
        visit_number: 1,
        is_parallel: true,
        attempts: [
          attempt({ run_id: "r2", agent_id: "second", status: "queued" }),
          attempt({ run_id: "r1", agent_id: "first", status: "running" }),
        ],
      }),
      visit({
        stage_key: "review",
        visit_number: 1,
        is_parallel: true,
        attempts: [attempt({ run_id: "r3", agent_id: "third", status: "running" })],
      }),
    ]),
  );
  renderPanel(makeTicket());

  const tabs = await screen.findAllByRole("tab");
  expect(tabs.map((t) => t.textContent)).toEqual([
    expect.stringContaining("second"),
    expect.stringContaining("first"),
    expect.stringContaining("third"),
  ]);
});

it("shows the selected lane's log and only fetches that lane's run-log", async () => {
  api.ticketLedger.mockResolvedValue(
    ledger([
      visit({
        is_parallel: true,
        attempts: [
          attempt({ run_id: "r1", agent_id: "planner", status: "running" }),
          attempt({ run_id: "r2", agent_id: "gatekeeper", status: "queued" }),
        ],
      }),
    ]),
  );
  renderPanel(makeTicket());

  expect(await screen.findByText("content for r1")).toBeInTheDocument();
  expect(api.runLog).toHaveBeenCalledWith("r1");
  expect(api.runLog).not.toHaveBeenCalledWith("r2");

  fireEvent.click(screen.getByRole("tab", { name: /gatekeeper/ }));

  expect(await screen.findByText("content for r2")).toBeInTheDocument();
  expect(screen.queryByText("content for r1")).not.toBeInTheDocument();
  expect(api.runLog).toHaveBeenCalledWith("r2");
});

it("keeps a lane's auto-follow choice after switching away and back", async () => {
  api.ticketLedger.mockResolvedValue(
    ledger([
      visit({
        is_parallel: true,
        attempts: [
          attempt({ run_id: "r1", agent_id: "planner", status: "running" }),
          attempt({ run_id: "r2", agent_id: "gatekeeper", status: "queued" }),
        ],
      }),
    ]),
  );
  renderPanel(makeTicket());

  await screen.findByText("content for r1");
  const laneACheckbox = screen.getByLabelText(/auto-follow/i) as HTMLInputElement;
  expect(laneACheckbox.checked).toBe(true);
  fireEvent.click(laneACheckbox);
  expect(laneACheckbox.checked).toBe(false);

  fireEvent.click(screen.getByRole("tab", { name: /gatekeeper/ }));
  await screen.findByText("content for r2");
  expect((screen.getByLabelText(/auto-follow/i) as HTMLInputElement).checked).toBe(true);

  fireEvent.click(screen.getByRole("tab", { name: /planner/ }));
  await screen.findByText("content for r1");
  expect((screen.getByLabelText(/auto-follow/i) as HTMLInputElement).checked).toBe(false);
});

it("falls back to the next running lane when the selected lane drops out", async () => {
  api.ticketLedger.mockResolvedValueOnce(
    ledger([
      visit({
        is_parallel: true,
        attempts: [
          attempt({ run_id: "r1", agent_id: "planner", status: "running" }),
          attempt({ run_id: "r2", agent_id: "gatekeeper", status: "running" }),
        ],
      }),
    ]),
  );
  const { queryClient } = renderPanel(makeTicket());

  await screen.findByText("content for r1");
  expect(screen.getByRole("tab", { name: /planner/ })).toHaveAttribute("aria-selected", "true");

  api.ticketLedger.mockResolvedValue(
    ledger([
      visit({
        is_parallel: true,
        attempts: [
          attempt({ run_id: "r1", agent_id: "planner", status: "completed" }),
          attempt({ run_id: "r2", agent_id: "gatekeeper", status: "running" }),
        ],
      }),
    ]),
  );
  await refetchLedger(queryClient, "t1");

  await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(1));
  expect(screen.getByRole("tab", { name: /gatekeeper/ })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByText("content for r2")).toBeInTheDocument();
});

it("falls back to the ticket-level feed rather than crashing when the ledger fetch errors", async () => {
  api.ticketLedger.mockRejectedValue(new Error("network down"));
  renderPanel(makeTicket());

  expect(await screen.findByText("ticket-level line")).toBeInTheDocument();
  expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
});

it("treats a parallel visit with an empty attempts array as zero lanes, not a crash", async () => {
  api.ticketLedger.mockResolvedValue(ledger([visit({ is_parallel: true, attempts: [] })]));
  renderPanel(makeTicket());

  expect(await screen.findByText("ticket-level line")).toBeInTheDocument();
  expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
});

it("does not steal the current selection when a new lane appears ahead of it in ledger order", async () => {
  api.ticketLedger.mockResolvedValueOnce(
    ledger([
      visit({
        is_parallel: true,
        attempts: [attempt({ run_id: "r1", agent_id: "planner", status: "running" })],
      }),
    ]),
  );
  const { queryClient } = renderPanel(makeTicket());
  await screen.findByText("content for r1");

  // A second lane appears ahead of r1 in visit/attempt order on the next ledger poll.
  api.ticketLedger.mockResolvedValue(
    ledger([
      visit({
        is_parallel: true,
        attempts: [
          attempt({ run_id: "r0", agent_id: "new_lane", status: "running" }),
          attempt({ run_id: "r1", agent_id: "planner", status: "running" }),
        ],
      }),
    ]),
  );
  await refetchLedger(queryClient, "t1");

  await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(2));
  expect(screen.getByRole("tab", { name: /planner/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: /new_lane/ })).toHaveAttribute("aria-selected", "false");
});

it("shows the just-selected lane's content, not a stale response from the lane switched away from moments earlier", async () => {
  api.ticketLedger.mockResolvedValue(
    ledger([
      visit({
        is_parallel: true,
        attempts: [
          attempt({ run_id: "r1", agent_id: "planner", status: "running" }),
          attempt({ run_id: "r2", agent_id: "gatekeeper", status: "running" }),
        ],
      }),
    ]),
  );

  let resolveR2: ((log: RunLog) => void) | undefined;
  const r2Pending = new Promise<RunLog>((resolve) => {
    resolveR2 = resolve;
  });
  api.runLog.mockImplementation((runId: string) =>
    runId === "r2" ? r2Pending : Promise.resolve(runLogFor(runId, `content for ${runId}`)),
  );

  renderPanel(makeTicket());
  await screen.findByText("content for r1");

  // Switch to the lane whose fetch is still in flight, then immediately switch back
  // before it resolves — the stale r2 response must not clobber the r1 view.
  fireEvent.click(screen.getByRole("tab", { name: /gatekeeper/ }));
  fireEvent.click(screen.getByRole("tab", { name: /planner/ }));

  await act(async () => {
    resolveR2?.(runLogFor("r2", "content for r2"));
    await Promise.resolve();
  });

  expect(screen.getByText("content for r1")).toBeInTheDocument();
  expect(screen.queryByText("content for r2")).not.toBeInTheDocument();
});

it("reverts to the ticket-level feed when the last running lane drops out", async () => {
  api.ticketLedger.mockResolvedValueOnce(
    ledger([visit({ is_parallel: true, attempts: [attempt({ run_id: "r1", agent_id: "planner", status: "running" })] })]),
  );
  const { queryClient } = renderPanel(makeTicket());

  await screen.findByText("content for r1");
  expect(screen.getByRole("tablist")).toBeInTheDocument();

  api.ticketLedger.mockResolvedValue(
    ledger([visit({ is_parallel: true, attempts: [attempt({ run_id: "r1", agent_id: "planner", status: "completed" })] })]),
  );
  await refetchLedger(queryClient, "t1");

  await waitFor(() => expect(screen.queryByRole("tablist")).not.toBeInTheDocument());
  expect(await screen.findByText("ticket-level line")).toBeInTheDocument();
});
