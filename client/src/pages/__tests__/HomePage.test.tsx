import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api, type TicketSummary } from "../../api/client";
import { HOME_BAXTER_PROMPT_KEY } from "../../lib/homeBaxter";
import { HomePage } from "../HomePage";

jest.mock("../../api/client", () => {
  const actual = jest.requireActual("../../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      approvals: jest.fn(),
      tickets: jest.fn(),
      ticketStatusSummary: jest.fn(),
      studioWorkflows: jest.fn(),
      runs: jest.fn(),
    },
  };
});

const mockedApi = api as jest.Mocked<typeof api>;

const EMPTY_STATUS = {
  backlog: 0,
  in_progress: 0,
  blocked: 0,
  done: 0,
  wont_do: 0,
  running: 0,
  awaiting: 0,
  queued: 0,
  idle: 0,
};

function ticketFixture(overrides: Partial<TicketSummary> = {}): TicketSummary {
  return {
    id: "t1",
    external_id: "feat-1",
    title: "Ship Home",
    state: "in_progress",
    priority: 2,
    workspace_slug: "loregarden",
    workflow_stage_key: "impl",
    workflow_stage_status: "pending",
    workflow_stage_name: "Implement",
    run_code: "",
    work_item_type: "feature",
    parent_ticket_id: null,
    milestone: "",
    branch: "",
    child_count: 0,
    ...overrides,
  };
}

function renderHome(initial = "/") {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/chat" element={<div>Baxter chat</div>} />
          <Route path="/console" element={<div>Console</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("HomePage", () => {
  beforeEach(() => {
    sessionStorage.clear();
    mockedApi.approvals.mockResolvedValue([]);
    mockedApi.tickets.mockResolvedValue([]);
    mockedApi.ticketStatusSummary.mockResolvedValue(EMPTY_STATUS);
    mockedApi.studioWorkflows.mockResolvedValue([]);
    mockedApi.runs.mockResolvedValue([]);
  });

  describe("board status card", () => {
    it("separates what is running from what is merely in progress", async () => {
      mockedApi.ticketStatusSummary.mockResolvedValue({
        ...EMPTY_STATUS,
        backlog: 12,
        in_progress: 20,
        blocked: 3,
        done: 40,
        running: 2,
        awaiting: 1,
        queued: 5,
        idle: 17,
      });

      renderHome();

      const card = await screen.findByRole("article", { name: /Board status/i });
      const stat = (key: string) =>
        card.querySelector(`[data-activity="${key}"], [data-state="${key}"]`) as HTMLElement;

      // The whole point: 20 in progress, but only 2 with an agent on them.
      await waitFor(() => expect(within(stat("running")).getByText("2")).toBeInTheDocument());
      expect(within(stat("idle")).getByText("17")).toBeInTheDocument();
      expect(within(stat("in_progress")).getByText("20")).toBeInTheDocument();
      expect(within(stat("queued")).getByText("5")).toBeInTheDocument();
      expect(within(stat("blocked")).getByText("3")).toBeInTheDocument();
      expect(within(card).getByText("35 open")).toBeInTheDocument();
    });

    it("leads the summary line with the running count", async () => {
      mockedApi.ticketStatusSummary.mockResolvedValue({
        ...EMPTY_STATUS,
        in_progress: 9,
        running: 1,
      });
      mockedApi.tickets.mockResolvedValue([ticketFixture()]);

      renderHome();

      await waitFor(() => {
        expect(screen.getByText(/1 running/)).toBeInTheDocument();
      });
    });

    it("says so rather than showing zeroes when the count cannot be fetched", async () => {
      mockedApi.ticketStatusSummary.mockRejectedValue(new Error("boom"));

      renderHome();

      expect(await screen.findByText("Status unavailable")).toBeInTheDocument();
    });
  });

  describe("recent activity", () => {
    it("shows the ticket name even when that ticket is no longer active", async () => {
      mockedApi.tickets.mockResolvedValue([]);
      mockedApi.runs.mockResolvedValue([
        {
          id: "run-done",
          run_code: "R9",
          status: "succeeded",
          command: "pytest -q",
          agent_id: "backend_implementer",
          stage_key: "impl",
          ticket_id: "t-done",
          ticket_title: "Ship the home page",
          ticket_external_id: "lor-home-12",
        },
      ]);

      renderHome();

      const activity = await screen.findByRole("region", { name: /Recent activity/i });
      expect(await within(activity).findByText("Ship the home page")).toBeInTheDocument();
      expect(within(activity).getByText("lor-home-12")).toBeInTheDocument();
      expect(within(activity).getByText("Succeeded")).toBeInTheDocument();
    });
  });

  it("badges an in-progress ticket with whether it is actually running", async () => {
    mockedApi.tickets.mockResolvedValue([
      ticketFixture({ id: "t-run", title: "Live one", activity: "running" }),
      ticketFixture({ id: "t-idle", title: "Parked one", activity: "idle" }),
    ]);

    renderHome();

    const live = (await screen.findByText("Live one")).closest("button");
    const parked = screen.getByText("Parked one").closest("button");
    expect(within(live as HTMLElement).getByText("Running")).toBeInTheDocument();
    expect(within(parked as HTMLElement).getByText("Idle")).toBeInTheDocument();
    // Both are "In Progress" — the state alone could not tell them apart.
    expect(within(live as HTMLElement).getByText("In Progress")).toBeInTheDocument();
    expect(within(parked as HTMLElement).getByText("In Progress")).toBeInTheDocument();
  });

  it("renders Baxter-First hero and live cards", async () => {
    mockedApi.approvals.mockResolvedValue([
      {
        id: "a1",
        title: "Approve tool",
        level: "ask",
        workspace_slug: "loregarden",
        stage_key: "impl",
        stage_name: "Implement",
        impact: "",
        ticket_id: "t1",
        ticket_external_id: "t-1",
        kind: "cli_permission",
        status: "pending",
        run_id: "r1",
        tool_name: "Bash",
        tool_input_json: "{}",
        cli_adapter: "claude",
      },
    ]);
    mockedApi.tickets.mockResolvedValue([
      {
        id: "t2",
        external_id: "feat-1",
        title: "Ship Home",
        state: "in_progress",
        priority: 2,
        workspace_slug: "loregarden",
        workflow_stage_key: "impl",
        workflow_stage_status: "running",
        workflow_stage_name: "Implement",
        run_code: "",
        work_item_type: "feature",
        parent_ticket_id: null,
        milestone: "",
        branch: "",
        child_count: 0,
      },
    ]);
    mockedApi.runs.mockResolvedValue([
      {
        id: "run-1",
        run_code: "R1",
        status: "succeeded",
        command: "pytest",
        agent_id: "backend_implementer",
        stage_key: "impl",
      },
    ]);

    renderHome();

    expect(screen.getByLabelText("Ask Baxter")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("What should we ship today?")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("Approve tool")).toBeInTheDocument();
      expect(screen.getByText("Ship Home")).toBeInTheDocument();
      expect(screen.getByText("backend_implementer")).toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: "Open Console" })).toHaveAttribute("href", "/console");
  });

  it("sends the hero prompt to Baxter chat", async () => {
    renderHome();
    const input = screen.getByPlaceholderText("What should we ship today?");
    fireEvent.change(input, { target: { value: "Triage stuck tickets" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask Baxter/i }));

    await waitFor(() => {
      expect(sessionStorage.getItem(HOME_BAXTER_PROMPT_KEY)).toBe("Triage stuck tickets");
      expect(screen.getByText("Baxter chat")).toBeInTheDocument();
    });
  });

  it("fills the composer from a quick-prompt chip without navigating away", () => {
    renderHome();
    fireEvent.click(screen.getByText("Review what's waiting on me"));

    expect(screen.getByPlaceholderText("What should we ship today?")).toHaveValue(
      "Review what's waiting on me",
    );
    expect(screen.queryByText("Baxter chat")).not.toBeInTheDocument();
    expect(sessionStorage.getItem(HOME_BAXTER_PROMPT_KEY)).toBeNull();
  });
});
