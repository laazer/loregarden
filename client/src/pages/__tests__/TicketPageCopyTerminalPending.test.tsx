import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import * as apiClient from "../../api/client";
import { RouterBridgeSync } from "../../components/RouterBridgeSync";
import * as terminalCommands from "../../lib/terminalCommands";
import { useUiStore } from "../../state/uiStore";
import { TicketPage } from '../TicketPage';

jest.mock("../../api/client", () => jest.requireActual("../../test/apiClientMock"));
jest.mock("../../lib/terminalCommands", () => ({
  ...jest.requireActual("../../lib/terminalCommands"),
  copyStageTerminalHandoffCommand: jest.fn(),
}));

/**
 * AC19, across the stage rows of one ticket. The in-flight guard in `Dashboard`
 * is ticket-wide — one `copyTerminalStageKey`, and the handler returns early
 * for any stage while it is set — so the `disabled` flag has to be ticket-wide
 * too. Stage-scoped (`=== s.key`), the second row's item renders enabled,
 * `onSelect` fires, the panel closes, and the handler returns without a toast:
 * the silent no-op CLAUDE.md forbids. Nothing else exercises *which* stage keys
 * receive the prop.
 */

const TICKET_ID = "00000000-0000-4000-8000-00000000917a";

const STAGES: apiClient.WorkflowStageView[] = [
  {
    key: "spec",
    name: "Specification",
    status: "pending",
    agent_id: "spec_writer",
    skill_name: "spec",
    optional: false,
    note: "",
    stage_type: "agent",
    agents: [],
  },
  {
    key: "implement",
    name: "Implement",
    status: "pending",
    agent_id: "frontend_implementer",
    skill_name: "",
    optional: false,
    note: "",
    stage_type: "agent",
    agents: [],
  },
];

const mkWorkspace = (): apiClient.WorkspaceSummary => ({
  id: "ws-1",
  slug: "loregarden",
  name: "Loregarden",
  repo_path: ".",
  repo_root: "/repo",
  repo_exists: true,
  repo_state: "repository",
  ticket_count: 1,
  blocked_count: 0,
  workflow_template_slug: "",
  archived_at: null,
  cli_adapter: "",
  claude_model: "",
  cursor_model: "",
  lmstudio_base_url: "",
  lmstudio_model: "",
});

const mkTreeNode = (): apiClient.TicketTreeNode => ({
  id: TICKET_ID,
  external_id: "lg-milestone-that-917",
  title: "Stale handoff tree",
  state: "in_progress",
  priority: 1,
  work_item_type: "task",
  workflow_stage_name: "Specification",
  workflow_stage_status: "pending",
  child_count: 0,
  children: [],
});

const mkTicket = (): apiClient.TicketDetail => ({
  id: TICKET_ID,
  external_id: "lg-milestone-that-917",
  title: "Stale handoff tree",
  description: "",
  acceptance_criteria: [],
  checked_acceptance_criteria: [],
  state: "in_progress",
  priority: 1,
  workspace_slug: "loregarden",
  workflow_stage_key: "spec",
  workflow_stage_status: "pending",
  workflow_stage_name: "Specification",
  run_code: "",
  work_item_type: "task",
  parent_ticket_id: null,
  milestone: "",
  branch: "",
  workflow_template_slug: "loregarden-tdd",
  workflow_template_name: "Loregarden TDD",
  child_count: 0,
  revision: 1,
  last_updated_by: "",
  current_stage_agent: "",
  next_status: "",
  blocking_issues: "",
  state_locked: false,
  stages: STAGES,
  artifacts: { diff: null, logs: [], tests: null, context: [], error: null, live: null },
});

function renderTicketPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/tickets/${TICKET_ID}`]}>
        <RouterBridgeSync />
        <Routes>
          <Route path="/" element={<TicketPage />} />
          <Route path="/tickets/:ticketId" element={<TicketPage />} />
          <Route path="/tickets/:ticketId/:artifactTab" element={<TicketPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Open one stage row's overflow panel and return its copy-terminal item. */
async function copyTerminalItem(stageName: string): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole("button", { name: `${stageName} actions` }));
  const panel = await screen.findByRole("menu");
  return within(panel).getByRole("menuitem", { name: /Copy terminal command/ });
}

beforeEach(() => {
  useUiStore.setState({
    stateFilters: [],
    typeFilters: [],
    search: "",
    expandedTicketIds: [],
    workspace: "all",
    paneVisibility: { workspaces: true, tickets: true },
  });
  useUiStore.persist?.clearStorage?.();
  jest.clearAllMocks();
  jest.mocked(apiClient.api.workspaces).mockResolvedValue([mkWorkspace()]);
  jest.mocked(apiClient.api.ticketTree).mockResolvedValue([mkTreeNode()]);
  jest.mocked(apiClient.api.ticket).mockResolvedValue(mkTicket());
  jest.mocked(apiClient.api.runs).mockResolvedValue([]);
  jest.mocked(apiClient.api.approvals).mockResolvedValue([]);
  jest.mocked(apiClient.api.ticketArtifacts).mockResolvedValue({ items: [], total: 0 });
  jest.mocked(apiClient.api.ticketLedger).mockResolvedValue({
    visits: [],
    total_runs: 0,
    reworked_stages: [],
    total_seconds: 0,
  });
});

describe("Dashboard — copy-terminal in-flight guard is ticket-wide", () => {
  it("disables the copy-terminal item on EVERY stage row of the ticket while one handoff is in flight", async () => {
    // A handoff that never settles, so the in-flight window stays open.
    let settle = () => {};
    jest
      .mocked(terminalCommands.copyStageTerminalHandoffCommand)
      .mockImplementation(() => new Promise<boolean>((resolve) => {
        settle = () => resolve(true);
      }));

    renderTicketPage();

    // Both rows are enabled before anything is in flight.
    expect(await copyTerminalItem("Specification")).toBeEnabled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(await copyTerminalItem("Implement")).toBeEnabled();
    fireEvent.keyDown(document, { key: "Escape" });

    // Start the Specification handoff.
    fireEvent.click(await copyTerminalItem("Specification"));
    await waitFor(() => {
      expect(terminalCommands.copyStageTerminalHandoffCommand).toHaveBeenCalledTimes(1);
    });

    // The stage whose handoff is in flight.
    const pendingOwn = await copyTerminalItem("Specification");
    expect(pendingOwn).toBeDisabled();
    expect(pendingOwn).toHaveAttribute(
      "title",
      "Already preparing a terminal command for this ticket",
    );
    fireEvent.keyDown(document, { key: "Escape" });

    // The other stage row of the SAME ticket: the regression. Enabled here is a
    // click that reaches `onSelect`, closes the panel, and does nothing.
    const pendingSibling = await copyTerminalItem("Implement");
    expect(pendingSibling).toBeDisabled();
    expect(pendingSibling).toHaveAttribute(
      "title",
      "Already preparing a terminal command for this ticket",
    );
    fireEvent.keyDown(document, { key: "Escape" });

    // No second request, whatever was clicked.
    expect(terminalCommands.copyStageTerminalHandoffCommand).toHaveBeenCalledTimes(1);

    // Once it settles, both rows are usable again.
    settle();
    await waitFor(async () => {
      expect(await copyTerminalItem("Implement")).toBeEnabled();
    });
  });
});
