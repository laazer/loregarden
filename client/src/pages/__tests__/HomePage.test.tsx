import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api } from "../../api/client";
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
      studioWorkflows: jest.fn(),
      runs: jest.fn(),
    },
  };
});

const mockedApi = api as jest.Mocked<typeof api>;

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
    mockedApi.studioWorkflows.mockResolvedValue([]);
    mockedApi.runs.mockResolvedValue([]);
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
