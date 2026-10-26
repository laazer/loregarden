import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api, type Approval, type TicketSummary } from "../../api/client";
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

  function approval(overrides: Partial<Approval> = {}): Approval {
    return {
      id: "a1",
      title: "Resolve Playtest exit actions",
      level: "ask",
      workspace_slug: "loregarden",
      stage_key: "impl",
      stage_name: "Implement",
      impact: "",
      ticket_id: "t1",
      ticket_external_id: "blob-core-1",
      kind: "human_action",
      status: "pending",
      run_id: "r1",
      tool_name: "",
      tool_input_json: "{}",
      cli_adapter: "claude",
      ...overrides,
    };
  }

  const TODAY = new Date().toISOString();

  describe("the header", () => {
    it("carries four small numbers, not the board's eight tiles", async () => {
      mockedApi.ticketStatusSummary.mockResolvedValue({
        ...EMPTY_STATUS,
        backlog: 1386,
        in_progress: 66,
        running: 2,
      });
      mockedApi.approvals.mockResolvedValue([approval(), approval({ id: "a2" })]);
      mockedApi.tickets.mockResolvedValue([ticketFixture({ id: "t9", state: "parked" })]);
      mockedApi.runs.mockResolvedValue([
        {
          id: "r-f",
          run_code: "R",
          status: "failed",
          command: "",
          agent_id: "test_designer",
          stage_key: "test-design",
          ticket_id: "t5",
          ticket_title: "Harness",
          finished_at: TODAY,
        },
      ]);

      renderHome();

      const glance = screen.getByLabelText("At a glance");
      const kpi = (label: string) =>
        within(glance).getByText(label).parentElement?.querySelector("dd") as HTMLElement;
      // Two approvals and one parked ticket need you; one run failed today.
      await waitFor(() => expect(kpi("need you")).toHaveTextContent("3"));
      await waitFor(() => expect(kpi("failed today")).toHaveTextContent("1"));
      await waitFor(() => expect(kpi("running")).toHaveTextContent("2"));
      expect(within(kpi("in progress")).getByRole("link", { name: "66" })).toHaveAttribute(
        "href",
        "/work-items",
      );
      // The backlog is no longer a number on Home.
      expect(screen.queryByText("1386")).not.toBeInTheDocument();
    });
  });

  describe("needs you", () => {
    it("groups identical approvals into one row with a count", async () => {
      mockedApi.approvals.mockResolvedValue(
        Array.from({ length: 14 }, (_, n) => approval({ id: `a${n}` })),
      );

      renderHome();

      const section = await screen.findByRole("region", { name: "Needs you" });
      const row = await within(section).findByRole("button", {
        name: /Resolve Playtest exit actions/,
      });
      expect(row).toHaveTextContent("×14");
      expect(within(section).getAllByRole("button", { name: /Resolve Playtest/ })).toHaveLength(1);
      expect(within(section).getByText("14 approvals")).toBeInTheDocument();
    });

    it("lists a run that failed today and a ticket waiting on a person, and not one blocked on tickets", async () => {
      mockedApi.runs.mockResolvedValue([
        {
          id: "r1",
          run_code: "R1",
          status: "failed",
          command: "",
          agent_id: "test_designer",
          stage_key: "test-design",
          ticket_id: "t5",
          ticket_title: "directory_release reports the truth",
          finished_at: TODAY,
        },
        {
          id: "r2",
          run_code: "R2",
          status: "failed",
          command: "",
          agent_id: "test_designer",
          stage_key: "test-design",
          ticket_id: "t5",
          ticket_title: "directory_release reports the truth",
          finished_at: TODAY,
        },
      ]);
      mockedApi.tickets.mockResolvedValue([
        ticketFixture({
          id: "t6",
          title: "Prompt assembly drops content",
          state: "blocked",
          blocked_on: "person",
        }),
        ticketFixture({
          id: "t7",
          title: "Skills into the database",
          state: "blocked",
          blocked_on: "tickets",
        }),
      ]);

      renderHome();

      const section = await screen.findByRole("region", { name: "Needs you" });
      const failed = await within(section).findByRole("button", { name: /directory_release/ });
      expect(failed).toHaveTextContent("Failed");
      expect(failed).toHaveTextContent("×2");
      expect(within(section).getByRole("button", { name: /Prompt assembly/ })).toHaveTextContent(
        "Person",
      );
      expect(within(section).queryByText("Skills into the database")).not.toBeInTheDocument();
    });

    it("says what would be here when nothing needs you", async () => {
      renderHome();

      const section = await screen.findByRole("region", { name: "Needs you" });
      expect(await within(section).findByText(/Nothing needs you/)).toBeInTheDocument();
    });

    it("says which source failed rather than showing an empty list", async () => {
      mockedApi.approvals.mockRejectedValue(new Error("inbox down"));

      renderHome();

      const section = await screen.findByRole("region", { name: "Needs you" });
      expect(await within(section).findByText(/Couldn't load approvals/)).toBeInTheDocument();
      expect(within(section).queryByText(/Nothing needs you/)).not.toBeInTheDocument();
    });
  });

  describe("running now", () => {
    it("says how many lanes are free when nothing is running", async () => {
      renderHome();

      const section = await screen.findByRole("region", { name: "Running now" });
      expect(within(section).getByText(/Nothing running; 3 lanes free/)).toBeInTheDocument();
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
      const row = await within(activity).findByRole("button", { name: /Ship the home page/ });
      expect(row).toHaveTextContent("backend_implementer");
      expect(row).toHaveTextContent("Succeeded");
    });
  });

  it("keeps the hero composer and the way to the Console", () => {
    renderHome();

    expect(screen.getByLabelText("Ask Baxter")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("What should we ship today?")).toBeInTheDocument();
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
