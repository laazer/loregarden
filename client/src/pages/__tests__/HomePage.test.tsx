import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api, type Approval, type TicketSummary } from "../../api/client";
import { HOME_BAXTER_PROMPT_KEY } from "../../lib/homeBaxter";
import { peekTicket } from "../../state/ticketPeekStore";
import { useUiStore } from "../../state/uiStore";
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
      doneTicketShip: jest.fn(),
      monitorFindings: jest.fn(),
    },
  };
});

jest.mock("../../state/ticketPeekStore", () => ({
  ...jest.requireActual("../../state/ticketPeekStore"),
  peekTicket: jest.fn(),
}));

const mockedApi = api as jest.Mocked<typeof api> & { doneTicketShip: jest.Mock };
const mockedPeek = peekTicket as jest.MockedFunction<typeof peekTicket>;

const EMPTY_SHIPS = {
  probes: [
    {
      workspace_id: "ws",
      workspace_slug: "loregarden",
      probed_at: "2026-10-28T00:00:00Z",
      listing_state: "complete",
      error: null,
      violations: [],
    },
  ],
};

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
    mockedApi.doneTicketShip.mockResolvedValue(EMPTY_SHIPS);
    mockedPeek.mockClear();
    useUiStore.getState().setWorkspace("all");
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
      expect(
        await within(section).findByText(
          "Nothing needs you. Approvals, runs that failed today, tickets waiting on a person, and finished tickets whose ship is not green show up here.",
        ),
      ).toBeInTheDocument();
    });

    it("withholds the empty copy and shows … until the ship probe returns", async () => {
      mockedApi.doneTicketShip.mockReturnValue(new Promise(() => {}));
      renderHome();

      const section = await screen.findByRole("region", { name: "Needs you" });
      expect(within(section).getByText("…")).toBeInTheDocument();
      expect(within(section).queryByText(/Nothing needs you/)).not.toBeInTheDocument();
      expect(within(section).queryAllByRole("button", { name: /Ship/ })).toHaveLength(0);
    });

    it("lists a 1624-shaped failing ship and peeks the done ticket on the PR tab", async () => {
      mockedApi.doneTicketShip.mockResolvedValue({
        probes: [
          {
            ...EMPTY_SHIPS.probes[0],
            violations: [
              {
                ticket_id: "t-1624",
                external_id: "lg-living-map-1624",
                title: "Map the hive",
                reason: "failing",
                pr_number: 733,
                pr_url: "https://github.com/o/r/pull/733",
                check_names: ["Server (Python)", "Client (TypeScript)"],
                carrying_branch: "integration/lg-living-map-1618",
                ci_run_result: null,
              },
            ],
          },
        ],
      });

      renderHome();

      const section = await screen.findByRole("region", { name: "Needs you" });
      const row = await within(section).findByRole("button", {
        name: /Map the hive.*PR #733 failing/,
      });
      expect(row).toHaveTextContent("Ship");
      expect(row).toHaveTextContent("PR #733 failing · Server (Python), Client (TypeScript)");
      expect(row).toHaveTextContent("Open");
      expect(within(section).getByText("1 ship")).toBeInTheDocument();
      fireEvent.click(row);
      expect(mockedPeek).toHaveBeenCalledWith("t-1624", "pr");
    });

    it("adds unique ship-broken tickets to need you even when they group to one row", async () => {
      mockedApi.doneTicketShip.mockResolvedValue({
        probes: [
          {
            ...EMPTY_SHIPS.probes[0],
            violations: [
              {
                ticket_id: "id-z",
                external_id: "lg-ship-z",
                title: "Later",
                reason: "failing",
                pr_number: 733,
                pr_url: "https://github.com/o/r/pull/733",
                check_names: [],
                carrying_branch: "integration/x",
                ci_run_result: null,
              },
              {
                ticket_id: "id-a",
                external_id: "lg-ship-a",
                title: "Earlier",
                reason: "failing",
                pr_number: 733,
                pr_url: "https://github.com/o/r/pull/733",
                check_names: [],
                carrying_branch: "integration/x",
                ci_run_result: null,
              },
            ],
          },
        ],
      });

      renderHome();

      const glance = screen.getByLabelText("At a glance");
      const kpi = (label: string) =>
        within(glance).getByText(label).parentElement?.querySelector("dd") as HTMLElement;
      await waitFor(() => expect(kpi("need you")).toHaveTextContent("2"));
      const section = await screen.findByRole("region", { name: "Needs you" });
      const row = await within(section).findByRole("button", { name: /PR #733 failing/ });
      expect(row).toHaveTextContent("×2");
      expect(within(section).getByText("2 ships")).toBeInTheDocument();
      fireEvent.click(row);
      expect(mockedPeek).toHaveBeenCalledWith("id-a", "pr");
    });

    it("names a probe miss and keeps last-known ship rows instead of the empty copy", async () => {
      mockedApi.doneTicketShip.mockResolvedValue({
        probes: [
          {
            ...EMPTY_SHIPS.probes[0],
            listing_state: "unavailable",
            error: "gh: HTTP 401",
            violations: [
              {
                ticket_id: "t-1624",
                external_id: "lg-living-map-1624",
                title: "Map the hive",
                reason: "failing",
                pr_number: 733,
                pr_url: "https://github.com/o/r/pull/733",
                check_names: ["Server (Python)", "Client (TypeScript)"],
                carrying_branch: "integration/lg-living-map-1618",
                ci_run_result: null,
              },
            ],
          },
        ],
      });

      renderHome();

      const section = await screen.findByRole("region", { name: "Needs you" });
      expect(await within(section).findByText(/Couldn't load ships: gh: HTTP 401/)).toBeInTheDocument();
      expect(within(section).queryByText(/Nothing needs you/)).not.toBeInTheDocument();
      expect(
        within(section).getByRole("button", { name: /Map the hive.*PR #733 failing/ }),
      ).toBeInTheDocument();
    });

    it("says GitHub could not be asked once, however many workspaces could not be asked", async () => {
      // Every workspace's probe row is unavailable until the first reconcile
      // tick, which is the state on every server start. Five identical lines
      // (and five colliding React keys) is what that used to render.
      const spy = jest.spyOn(console, "error").mockImplementation(() => {});
      mockedApi.doneTicketShip.mockResolvedValue({
        probes: ["loregarden", "blobert", "lore-eden", "loremaker", "scratch"].map((slug) => ({
          ...EMPTY_SHIPS.probes[0],
          workspace_id: `ws-${slug}`,
          workspace_slug: slug,
          listing_state: "unavailable" as const,
          error: "Ship probe has not run yet",
          violations: [],
        })),
      });

      renderHome();

      const section = await screen.findByRole("region", { name: "Needs you" });
      await waitFor(() =>
        expect(
          within(section).getAllByText("Couldn't load ships: Ship probe has not run yet"),
        ).toHaveLength(1),
      );
      expect(within(section).queryByText(/Nothing needs you/)).not.toBeInTheDocument();
      expect(spy).not.toHaveBeenCalled();
      spy.mockRestore();
    });

    it("keeps a second workspace's different reason on the one ships line", async () => {
      mockedApi.doneTicketShip.mockResolvedValue({
        probes: [
          {
            ...EMPTY_SHIPS.probes[0],
            listing_state: "unavailable" as const,
            error: "gh: HTTP 401",
            violations: [],
          },
          {
            ...EMPTY_SHIPS.probes[0],
            workspace_id: "ws-2",
            workspace_slug: "blobert",
            listing_state: "unavailable" as const,
            error: "ls-remote failed",
            violations: [],
          },
        ],
      });

      renderHome();

      const section = await screen.findByRole("region", { name: "Needs you" });
      expect(
        await within(section).findByText("Couldn't load ships: gh: HTTP 401; ls-remote failed"),
      ).toBeInTheDocument();
    });

    it("says the ship list may be incomplete without hiding known reds", async () => {
      mockedApi.doneTicketShip.mockResolvedValue({
        probes: [
          {
            ...EMPTY_SHIPS.probes[0],
            listing_state: "incomplete",
            error: null,
            violations: [
              {
                ticket_id: "t-1624",
                external_id: "lg-living-map-1624",
                title: "Map the hive",
                reason: "failing",
                pr_number: 733,
                pr_url: "https://github.com/o/r/pull/733",
                check_names: [],
                carrying_branch: "integration/lg-living-map-1618",
                ci_run_result: null,
              },
            ],
          },
        ],
      });

      renderHome();

      const section = await screen.findByRole("region", { name: "Needs you" });
      expect(
        await within(section).findByText("Ship list may be incomplete — GitHub did not return every PR."),
      ).toBeInTheDocument();
      expect(within(section).queryByText(/Couldn't load ships/)).not.toBeInTheDocument();
      expect(within(section).getByRole("button", { name: /Map the hive/ })).toBeInTheDocument();
      expect(
        within(section).getByRole("status", {
          name: /Ship list may be incomplete — GitHub did not return every PR/,
        }),
      ).toBeInTheDocument();
    });

    it("treats a failed ships GET as unavailable and does not show the empty copy", async () => {
      mockedApi.doneTicketShip.mockRejectedValue(new Error("network down"));
      renderHome();

      const section = await screen.findByRole("region", { name: "Needs you" });
      expect(await within(section).findByText(/Couldn't load ships: network down/)).toBeInTheDocument();
      expect(within(section).queryByText(/Nothing needs you/)).not.toBeInTheDocument();
    });

    it("asks doneTicketShip with the Home workspace slug, and with nothing when all", async () => {
      const first = renderHome();
      await screen.findByRole("region", { name: "Needs you" });
      expect(mockedApi.doneTicketShip).toHaveBeenCalled();
      expect(mockedApi.doneTicketShip.mock.calls.some((call) => call[0] === "loregarden")).toBe(false);
      first.unmount();

      mockedApi.doneTicketShip.mockClear();
      useUiStore.getState().setWorkspace("loregarden");
      renderHome();
      await waitFor(() => expect(mockedApi.doneTicketShip).toHaveBeenCalledWith("loregarden"));
    });

    it("unions probes on all workspaces and still names one workspace's miss", async () => {
      mockedApi.doneTicketShip.mockResolvedValue({
        probes: [
          {
            workspace_id: "ws-a",
            workspace_slug: "loregarden",
            probed_at: "2026-10-28T00:00:00Z",
            listing_state: "complete",
            error: null,
            violations: [
              {
                ticket_id: "t-1624",
                external_id: "lg-living-map-1624",
                title: "Map the hive",
                reason: "failing",
                pr_number: 733,
                pr_url: "https://github.com/o/r/pull/733",
                check_names: ["Server (Python)", "Client (TypeScript)"],
                carrying_branch: "integration/lg-living-map-1618",
                ci_run_result: null,
              },
            ],
          },
          {
            workspace_id: "ws-b",
            workspace_slug: "blobert",
            probed_at: "2026-10-28T00:00:00Z",
            listing_state: "unavailable",
            error: "gh down",
            violations: [],
          },
        ],
      });

      renderHome();

      const section = await screen.findByRole("region", { name: "Needs you" });
      expect(
        await within(section).findByRole("button", { name: /Map the hive.*PR #733 failing/ }),
      ).toBeInTheDocument();
      expect(await within(section).findByText(/Couldn't load ships: gh down/)).toBeInTheDocument();
      expect(within(section).queryByText(/Nothing needs you/)).not.toBeInTheDocument();
    });

    it("does not fetch done tickets or monitor findings for the ship list", async () => {
      renderHome();
      await screen.findByRole("region", { name: "Needs you" });
      expect(mockedApi.doneTicketShip).toHaveBeenCalled();
      const ticketCalls = mockedApi.tickets.mock.calls as unknown as unknown[][];
      for (const args of ticketCalls) {
        const params = args[0] as { state?: string[] } | undefined;
        expect(params?.state ?? []).not.toContain("done");
      }
      expect(mockedApi.monitorFindings).not.toHaveBeenCalled();
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
