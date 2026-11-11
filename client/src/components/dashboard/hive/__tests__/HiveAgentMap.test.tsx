import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { api } from "../../../../api/client";
import type { RunLog, TicketDetail, TicketLedger } from "../../../../api/types";
import { HiveAgentMap } from "../HiveAgentMap";

jest.mock("../../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;
const ticket = { id: "t1" } as TicketDetail;

function ledger(attempts: TicketLedger["visits"][number]["attempts"]): TicketLedger {
  return {
    visits: [{ stage_key: "implement", visit_number: 1, status: "running", is_parallel: false, attempts }],
    total_runs: attempts.length,
    reworked_stages: [],
    total_seconds: 0,
  };
}

function runLog(lines: RunLog["lines"]): RunLog {
  return {
    id: "r1",
    run_code: "run_a",
    agent_id: "backend_implementer",
    skill_name: "",
    stage_key: "implement",
    status: "running",
    command: "",
    started_at: null,
    finished_at: null,
    lines,
    live: null,
    stderr: "",
    transport: "",
    attach_command: "",
  };
}

function renderMap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <HiveAgentMap ticket={ticket} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  // The run-log modal asks whether the run can be steered.
  mockApi.runMessages.mockResolvedValue({ messages: [], refusal: "", cancel_requested_at: null });
});

it("says how to get a run when the ticket has none", async () => {
  mockApi.ticketLedger.mockResolvedValue(ledger([]));
  renderMap();
  expect(await screen.findByText("No agent has run on this ticket yet")).toBeTruthy();
});

it("shows an error that does not look like the empty state", async () => {
  mockApi.ticketLedger.mockRejectedValue(new Error("down"));
  renderMap();
  expect((await screen.findByRole("alert")).textContent).toContain("Couldn't load this ticket's agent runs");
});

it("lists the running agent and its calls, and opens its log from either list", async () => {
  mockApi.ticketLedger.mockResolvedValue(
    ledger([
      {
        run_id: "r1",
        run_code: "run_a",
        agent_id: "backend_implementer",
        skill_name: "",
        status: "running",
        started_at: "2026-11-10T10:00:00Z",
        finished_at: null,
        duration_seconds: null,
        error_tail: "",
      },
    ]),
  );
  mockApi.runLog.mockResolvedValue(
    runLog([
      { tag: "TOOL", text: "call · Read · hive/cast.ts", time: "10:00:01" },
      { tag: "TOOL", text: "result\nfile body", time: "10:00:02" },
      { tag: "TOOL", text: "call · Bash · pytest: Run the hive tests", time: "10:00:03" },
    ]),
  );
  renderMap();

  await screen.findByText("● Shell — Run the hive tests");
  const agent = screen.getByRole("button", { name: /Open backend implementer's log/ });
  expect(agent.textContent).toContain("2 calls");
  const rows = within(screen.getByText("Latest calls").parentElement as HTMLElement).getAllByRole("button");
  expect(rows.map((r) => r.getAttribute("aria-label"))).toEqual([
    expect.stringContaining("Shell, Run the hive tests, running"),
    expect.stringContaining("Files, hive/cast.ts, done"),
  ]);
  expect(screen.getByRole("img").getAttribute("aria-label")).toContain("Files 1");

  fireEvent.click(rows[0]);
  expect(await screen.findByRole("dialog")).toBeTruthy();
});
