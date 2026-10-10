import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import * as apiClient from "../../api/client";
import { RouterBridgeSync } from "../../components/RouterBridgeSync";
import { useUiStore } from "../../state/uiStore";
import { Dashboard } from "../Dashboard";

jest.mock("../../api/client", () => jest.requireActual("../../test/apiClientMock"));

/**
 * Archiving a workspace on the Workspaces page takes it out of the Console: the
 * workspace list, its items under "All workspaces", and the pickers for new work.
 */

const mkWorkspace = (slug: string, name: string, archived_at: string | null = null): apiClient.WorkspaceSummary => ({
  id: `ws-${slug}`,
  slug,
  name,
  repo_path: ".",
  repo_root: `/w/${slug}`,
  repo_exists: true,
  repo_state: "repository",
  ticket_count: 1,
  blocked_count: 0,
  workflow_template_slug: "",
  archived_at,
  cli_adapter: "",
  claude_model: "",
  cursor_model: "",
  lmstudio_base_url: "",
  lmstudio_model: "",
});

const mkNode = (id: string, title: string, workspace_slug: string): apiClient.TicketTreeNode => ({
  id,
  external_id: id,
  title,
  state: "backlog",
  priority: 3,
  work_item_type: "task",
  workspace_slug,
  workflow_stage_name: "",
  workflow_stage_status: "pending",
  child_count: 0,
  children: [],
});

describe("Dashboard — archived workspaces", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    useUiStore.setState({
      stateFilters: [],
      typeFilters: [],
      search: "",
      expandedTicketIds: [],
      workspace: "all",
      paneVisibility: { workspaces: true, tickets: true, workflow: true, artifacts: true },
    });
    jest.clearAllMocks();
    jest
      .mocked(apiClient.api.workspaces)
      .mockResolvedValue([mkWorkspace("shop", "Shop"), mkWorkspace("attic", "Attic", "2026-09-30T00:00:00Z")]);
    jest
      .mocked(apiClient.api.ticketTree)
      .mockResolvedValue([mkNode("shop-1", "Shop task", "shop"), mkNode("attic-1", "Attic task", "attic")]);
    jest.mocked(apiClient.api.ticket).mockRejectedValue(new Error("not needed"));
    jest.mocked(apiClient.api.runs).mockResolvedValue([]);
    jest.mocked(apiClient.api.approvals).mockResolvedValue([]);
    jest.mocked(apiClient.api.workflowTemplates).mockResolvedValue([]);
    jest.mocked(apiClient.api.tickets).mockResolvedValue([]);
  });

  const renderDashboard = () =>
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/console"]}>
          <RouterBridgeSync />
          <Routes>
            <Route path="/console" element={<Dashboard />} />
            <Route path="/tickets/:ticketId/*" element={<Dashboard />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

  it("lists only active workspaces, and says how many are archived and where", async () => {
    renderDashboard();
    const pane = (await screen.findByText("Every repo")).closest(".workspaces-pane") as HTMLElement;
    await waitFor(() => expect(within(pane).getByText("Shop")).toBeInTheDocument());
    expect(within(pane).queryByText("Attic")).not.toBeInTheDocument();
    expect(within(pane).getByRole("link", { name: /1 archived/ })).toHaveAttribute("href", "/workspaces");
  });

  it("leaves an archived workspace's items out of All workspaces", async () => {
    renderDashboard();
    expect(await screen.findByText("Shop task")).toBeInTheDocument();
    expect(screen.queryByText("Attic task")).not.toBeInTheDocument();
  });

  it("falls back to All workspaces when the selected one was archived", async () => {
    useUiStore.setState({ workspace: "attic" });
    renderDashboard();
    await waitFor(() => expect(useUiStore.getState().workspace).toBe("all"));
  });

  it("offers only active workspaces for new work", async () => {
    renderDashboard();
    await screen.findByText("Shop task");
    fireEvent.click(screen.getByRole("button", { name: /New/ }));
    const dialog = await screen.findByRole("dialog");
    const options = within(dialog)
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(options).toContain("Shop");
    expect(options).not.toContain("Attic");
  });
});
