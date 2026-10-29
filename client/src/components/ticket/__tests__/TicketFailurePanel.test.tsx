import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api } from "../../../api/client";
import type { TicketDetail } from "../../../api/client";
import type { LedgerAttempt, LedgerVisit } from "../../../api/types";
import { TicketFailurePanel, type FailureRun } from "../TicketFailurePanel";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function attempt(overrides: Partial<LedgerAttempt> = {}): LedgerAttempt {
  return {
    run_id: "r1",
    run_code: "run_a",
    agent_id: "test_designer",
    skill_name: "",
    status: "failed",
    started_at: "2026-10-24T11:35:00+00:00",
    finished_at: "2026-10-24T11:35:02+00:00",
    duration_seconds: 2,
    error_tail: "",
    ...overrides,
  };
}

function visit(stage_key: string, attempts: LedgerAttempt[]): LedgerVisit {
  return { stage_key, visit_number: 1, status: attempts[attempts.length - 1].status, is_parallel: false, attempts };
}

function ticket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: "t1",
    state: "in_progress",
    stages: [
      { key: "spec", name: "Specification" },
      { key: "test_design", name: "Design tests" },
      { key: "gate", name: "Quality Gate" },
    ],
    blocking_issues: "",
    child_count: 0,
    artifacts: {},
    ...overrides,
  } as unknown as TicketDetail;
}

const LAUNCH = [
  "Run run_d4 failed",
  "Agent launch failed: command exceeded the system limit",
  "tmux: command too long",
].join("\n");

function renderPanel({
  detail = ticket(),
  visits = [] as LedgerVisit[],
  runs = [] as FailureRun[],
  onOpenRunLog = jest.fn(),
} = {}) {
  mockApi.ticketLedger.mockResolvedValue({ visits, total_runs: 0, reworked_stages: [], total_seconds: 0 });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <TicketFailurePanel ticket={detail} runs={runs} isActive={false} onOpenRunLog={onOpenRunLog} />
    </QueryClientProvider>,
  );
  return { ...view, onOpenRunLog };
}

beforeEach(() => jest.clearAllMocks());

it("leads with the failing stage, which attempt it was, and that run's own output", async () => {
  const { onOpenRunLog } = renderPanel({
    detail: ticket({ state: "blocked", block_message: "Agent launch failed" } as Partial<TicketDetail>),
    visits: [
      visit("spec", [attempt({ run_id: "s1", status: "succeeded" })]),
      visit("test_design", [
        attempt({ run_id: "d1", run_code: "run_d1", error_tail: LAUNCH }),
        attempt({ run_id: "d2", run_code: "run_d2", error_tail: LAUNCH }),
      ]),
    ],
  });

  const panel = await screen.findByRole("region", { name: "Blocking issue" });
  expect(within(panel).getByRole("heading", { name: "Design tests" })).toBeInTheDocument();
  expect(panel).toHaveTextContent(/Attempt 2 of 2 · test_designer · .* · 2s/);
  expect(panel).toHaveTextContent("run_d2");

  const lines = within(within(panel).getByRole("list", { name: "Failure output" })).getAllByRole("listitem");
  expect(lines.map((line) => line.textContent)).toEqual(LAUNCH.split("\n"));
  // The two lines that state the error are marked; the narration is not.
  expect(lines.map((line) => line.classList.contains("is-error"))).toEqual([false, true, true]);

  fireEvent.click(within(panel).getByRole("button", { name: "Full log" }));
  expect(onOpenRunLog).toHaveBeenCalledWith("d2");
  expect(within(panel).getByRole("button", { name: "Copy the failure output" })).toBeInTheDocument();
});

it("shows the ticket's message when the run recorded no output, opening the log by its run code", async () => {
  const { onOpenRunLog } = renderPanel({
    detail: ticket({
      blocking_issues: "Gate failed: 2 commands",
      artifacts: { error: { message: "Gate failed: 2 commands", run_code: "run_zz", agent_id: "gatekeeper", stage_key: "gate", command: "" } },
    } as Partial<TicketDetail>),
    runs: [{ id: "g1", run_code: "run_zz" }],
  });

  const panel = await screen.findByRole("region", { name: "Blocking issue" });
  expect(within(panel).getByRole("heading", { name: "Quality Gate" })).toBeInTheDocument();
  expect(panel).toHaveTextContent("What stopped it");
  expect(panel).toHaveTextContent("Gate failed: 2 commands");
  fireEvent.click(within(panel).getByRole("button", { name: "Full log" }));
  expect(onOpenRunLog).toHaveBeenCalledWith("g1");
});

it("shows a long block's full text, not the pointer back to the Timeline", async () => {
  renderPanel({
    detail: ticket({
      state: "blocked",
      blocking_issues: "Stage 'test-break' hit a blocking issue — see the Timeline tab for details.",
      block_message: "No failing test could be written: the module under test does not exist.",
    } as Partial<TicketDetail>),
  });

  const panel = await screen.findByRole("region", { name: "Blocking issue" });
  expect(panel).toHaveTextContent("the module under test does not exist");
  expect(panel).not.toHaveTextContent("see the Timeline tab");
});

it("never calls a ticket blocked on the strength of an error artifact alone", async () => {
  renderPanel({
    detail: ticket({
      artifacts: { error: { message: "Run exited 1", run_code: "run_zz", agent_id: "implementer", stage_key: "implement", command: "" } },
    } as Partial<TicketDetail>),
  });

  const panel = await screen.findByRole("region", { name: "Last run failed" });
  expect(panel).toHaveTextContent("Run exited 1");
  expect(screen.queryByRole("region", { name: "Blocking issue" })).toBeNull();
});

it("shows a blocked ticket as blocked even when no reason was recorded", async () => {
  renderPanel({ detail: ticket({ state: "blocked" } as Partial<TicketDetail>) });

  const panel = await screen.findByRole("region", { name: "Blocking issue" });
  expect(panel).toHaveTextContent(/No reason was recorded/);
  expect(within(panel).queryByRole("button", { name: "Full log" })).toBeNull();
});

it("explains a parent blocked by its children rather than pointing at a run", async () => {
  renderPanel({ detail: ticket({ state: "blocked", child_count: 4 } as Partial<TicketDetail>) });

  const panel = await screen.findByRole("region", { name: "Blocking issue" });
  expect(panel).toHaveTextContent(/work under it is blocked/);
});

it("renders nothing while nothing has stopped the ticket", async () => {
  const { container } = renderPanel({
    visits: [visit("test_design", [attempt({ run_id: "d1", error_tail: "boom" }), attempt({ run_id: "d2", status: "succeeded" })])],
  });

  await waitFor(() => expect(mockApi.ticketLedger).toHaveBeenCalledWith("t1"));
  await act(async () => {});
  expect(container).toBeEmptyDOMElement();
});
