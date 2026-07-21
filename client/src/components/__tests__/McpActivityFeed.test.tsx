import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";

import { api } from "../../api/client";
import { McpActivityFeed } from "../mcp/McpActivityFeed";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function call(overrides = {}) {
  return {
    id: "c1",
    run_id: "run-1",
    ticket_id: "t-1",
    agent_id: "planner",
    tool_name: "mcp__github__create_issue",
    server_name: "github",
    decision: "auto_server",
    decision_ms: 0,
    created_at: "2026-07-20T10:00:00",
    ...overrides,
  };
}

function renderFeed() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <McpActivityFeed />
    </QueryClientProvider>,
  );
}

beforeEach(() => jest.clearAllMocks());

it("explains why an empty feed may not mean no activity", async () => {
  // Bypassed runs have no permission bridge, so they record nothing. Saying
  // "no calls" without that caveat would be a misleading zero.
  mockApi.mcpTelemetry.mockResolvedValue({ by_server: {}, by_decision: {}, recent: [] });

  renderFeed();
  expect(await screen.findByText(/runs with permissions bypassed do not appear/i)).toBeInTheDocument();
});

it("counts calls per server, busiest first", async () => {
  mockApi.mcpTelemetry.mockResolvedValue({
    by_server: { loregarden: 2, github: 7 },
    by_decision: {},
    recent: [],
  });

  renderFeed();
  const names = (await screen.findAllByText(/github|loregarden/)).map((n) => n.textContent);
  expect(names[0]).toBe("github");
});

it("says how each call was resolved, in words", async () => {
  mockApi.mcpTelemetry.mockResolvedValue({
    by_server: { github: 2 },
    by_decision: {},
    recent: [
      call({ id: "a", decision: "auto_server" }),
      call({ id: "b", decision: "rejected" }),
    ],
  });

  renderFeed();
  expect(await screen.findByText("trusted server")).toBeInTheDocument();
  expect(screen.getByText("you rejected")).toBeInTheDocument();
});

it("shows a wait only where a human actually waited", async () => {
  mockApi.mcpTelemetry.mockResolvedValue({
    by_server: {},
    by_decision: {},
    recent: [
      call({ id: "a", decision: "approved", decision_ms: 90000 }),
      call({ id: "b", decision: "auto_server", decision_ms: 0 }),
    ],
  });

  renderFeed();
  // An auto-approved call has no wait worth reporting; only the human one does.
  expect(await screen.findByText(/waited 1m 30s/)).toBeInTheDocument();
  expect(screen.queryAllByText(/waited/)).toHaveLength(1);
});

it("reports a failure rather than showing an empty feed", async () => {
  mockApi.mcpTelemetry.mockRejectedValue(new Error("boom"));

  renderFeed();
  expect(await screen.findByText(/could not load activity/i)).toBeInTheDocument();
});
