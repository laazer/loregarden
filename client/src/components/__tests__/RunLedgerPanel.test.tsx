import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";

import { api } from "../../api/client";
import type { LedgerVisit } from "../../api/types";
import { duration } from "../../lib/duration";
import { RunLedgerPanel } from "../RunLedgerPanel";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function attempt(overrides = {}) {
  return {
    run_id: "r1",
    run_code: "run_a",
    agent_id: "backend_implementer",
    skill_name: "",
    status: "succeeded",
    started_at: "2026-07-20T09:00:00",
    finished_at: "2026-07-20T09:00:30",
    duration_seconds: 30,
    ...overrides,
  };
}

function visit(overrides: Partial<LedgerVisit> = {}): LedgerVisit {
  return {
    stage_key: "implement",
    visit_number: 1,
    status: "succeeded",
    is_parallel: false,
    attempts: [attempt()],
    ...overrides,
  };
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RunLedgerPanel ticketId="t1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => jest.clearAllMocks());

describe("duration", () => {
  it("never renders sixty seconds", () => {
    // Real durations are fractional. Rounding the remainder rather than the
    // total rendered a genuine 119.7s run as "1m 60s" in the live app.
    expect(duration(119.7)).toBe("2m 0s");
    expect(duration(59.6)).toBe("1m 0s");
  });

  it("formats the ordinary cases", () => {
    expect(duration(0)).toBe("0s");
    expect(duration(45)).toBe("45s");
    expect(duration(90)).toBe("1m 30s");
    expect(duration(null)).toBe("");
  });
});

it("says so when nothing has run", async () => {
  mockApi.ticketLedger.mockResolvedValue({
    visits: [],
    total_runs: 0,
    reworked_stages: [],
    total_seconds: 0,
  });

  renderPanel();
  expect(await screen.findByText(/nothing has run for this ticket yet/i)).toBeInTheDocument();
});

it("marks a stage the pipeline came back to", async () => {
  // The signal the flat run list could never show: verify refused, and the
  // work went back to implement.
  mockApi.ticketLedger.mockResolvedValue({
    visits: [
      visit({ stage_key: "implement", visit_number: 1 }),
      visit({ stage_key: "verify", visit_number: 1, attempts: [attempt({ run_id: "r2" })] }),
      visit({ stage_key: "implement", visit_number: 2, attempts: [attempt({ run_id: "r3" })] }),
    ],
    total_runs: 3,
    reworked_stages: ["implement"],
    total_seconds: 90,
  });

  renderPanel();
  expect(await screen.findByText(/revisit #2/i)).toBeInTheDocument();
  expect(screen.getByText(/reworked: implement/i)).toBeInTheDocument();
});

it("distinguishes lanes from retries", async () => {
  mockApi.ticketLedger.mockResolvedValue({
    visits: [
      visit({
        stage_key: "plan",
        is_parallel: true,
        attempts: [
          attempt({ run_id: "p1", agent_id: "planner", skill_name: "plan-simplest" }),
          attempt({ run_id: "p2", agent_id: "planner", skill_name: "plan-risk" }),
          attempt({ run_id: "p3", agent_id: "planner", skill_name: "plan-seams" }),
        ],
      }),
      visit({
        stage_key: "gate",
        attempts: [
          attempt({ run_id: "g1", agent_id: "gatekeeper", status: "failed" }),
          attempt({ run_id: "g2", agent_id: "gatekeeper" }),
        ],
      }),
    ],
    total_runs: 5,
    reworked_stages: [],
    total_seconds: 150,
  });

  renderPanel();
  expect(await screen.findByText("3 lanes")).toBeInTheDocument();
  expect(screen.getByText("2 attempts")).toBeInTheDocument();
  // The lens each planner argued is what makes the lanes worth telling apart.
  expect(screen.getByText(/plan-risk/)).toBeInTheDocument();
});

it("reports a failure to load rather than showing an empty ledger", async () => {
  mockApi.ticketLedger.mockRejectedValue(new Error("boom"));

  renderPanel();
  expect(await screen.findByText(/could not load this ticket/i)).toBeInTheDocument();
});
