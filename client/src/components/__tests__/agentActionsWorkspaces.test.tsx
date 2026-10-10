/**
 * Agent actions that create workspaces and their repositories, and change a
 * branch's triage runtime: each makes the same call as its control, refuses a
 * target that is not on screen, and refuses work the control would not offer.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { uiActionRegistry } from "../../lib/agentActions/registry";
import { BranchTriageOverviewPanel } from "../BranchTriageOverviewPanel";
import { WorkspaceSetupCard } from "../instances/WorkspaceSetupCard";
import { WorkspacesTab } from "../workspaces/WorkspacesTab";

const api = {
  workspaces: jest.fn(),
  createWorkspace: jest.fn(),
  createWorkspaceRepository: jest.fn(),
  runtimeOptions: jest.fn(),
  setTriageRuntime: jest.fn(),
  setWorkspaceRuntime: jest.fn(),
};

jest.mock("../../api/client", () => ({
  api: new Proxy({}, { get: (_t, name: string) => (...args: unknown[]) => api[name as keyof typeof api](...args) }),
}));
jest.mock("../../api/localInstancesApi", () => ({
  localInstancesApi: { workspaceTemplates: async () => [{ slug: "alpha", name: "Alpha", entries: [] }] },
}));
// The card's panels are not under test; its repository action is.
jest.mock("../instances/WorkspaceTemplatesPanel", () => ({ WorkspaceTemplatesPanel: () => null }));
jest.mock("../instances/WorkspaceIntegrationPanel", () => ({ WorkspaceIntegrationPanel: () => null }));
jest.mock("../workspaces/WorkspaceGatePresetsPanel", () => ({ WorkspaceGatePresetsPanel: () => null }));
jest.mock("../workspaces/AddWorkspaceFlow", () => ({
  ...jest.requireActual("../workspaces/AddWorkspaceFlow"),
  AddWorkspaceFlow: () => null,
}));
jest.mock("../../lib/branchTriageApi", () => ({
  ...jest.requireActual("../../lib/branchTriageApi"),
  fetchBranchActivity: async () => ({ branch: "feature/x", upstream: null, commits: [] }),
}));
const TRIAGE_RUNTIME = { cli_adapter: "claude", claude_model: "sonnet", cursor_model: "", lmstudio_base_url: "", lmstudio_model: "" };
jest.mock("../../hooks/useBranchChatSession", () => ({
  useBranchChatSession: () => ({
    messages: [],
    isBusy: false,
    isLoading: false,
    loadError: false,
    error: null,
    send: jest.fn(),
    isFetching: false,
    snapshot: { linked_ticket_id: "lg-7", runtime: TRIAGE_RUNTIME },
  }),
}));

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const card = (slug: string, repoState: string) => (
  <WorkspaceSetupCard
    key={slug}
    workspace={{ slug, name: slug, entries: [] } as never}
    summary={{ slug, name: slug, repo_state: repoState, archived_at: null } as never}
    archiving={false}
    onArchive={() => {}}
  />
);

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  api.workspaces.mockResolvedValue([{ id: "w1", slug: "alpha", name: "Alpha", archived_at: null }]);
  api.createWorkspace.mockImplementation(async (body: { slug: string; name: string }) => ({ ...body, id: "w2" }));
  api.createWorkspaceRepository.mockImplementation(async (slug: string) => ({
    slug,
    name: slug,
    repo_root: `/repos/${slug}`,
    follow_up: "",
  }));
  api.runtimeOptions.mockResolvedValue({ cli_adapters: [], claude_models: [], cursor_models: [], codex_models: [], lmstudio_models: [], opencode_models: [] });
  api.setTriageRuntime.mockImplementation(async (_id: string, body: object) => body);
});

afterEach(() => expect(uiActionRegistry.available()).toEqual([]));

describe("workspace.create", () => {
  it("adds the workspace record, and refuses a slug already in use", async () => {
    const view = wrap(<WorkspacesTab />);
    await waitFor(() => expect(api.workspaces).toHaveBeenCalled());
    await waitFor(async () =>
      expect(await uiActionRegistry.run("workspace.create", { slug: "beta", name: "Beta" })).toEqual({
        workspace_slug: "beta",
      }),
    );
    expect(api.createWorkspace).toHaveBeenCalledWith({
      slug: "beta",
      name: "Beta",
      repo_path: undefined,
      workflow_template_slug: undefined,
    });
    await expect(uiActionRegistry.run("workspace.create", { slug: "alpha", name: "Again" })).rejects.toThrow(
      "a workspace named alpha already exists",
    );
    expect(api.createWorkspace).toHaveBeenCalledTimes(1);
    view.unmount();
  });
});

describe("workspace.create_repository", () => {
  it("reaches the card for the workspace it names, among several", async () => {
    const view = wrap(
      <ul>
        {card("alpha", "missing")}
        {card("beta", "missing")}
      </ul>,
    );
    await act(() => uiActionRegistry.run("workspace.create_repository", { workspace_slug: "alpha" }));
    expect(api.createWorkspaceRepository).toHaveBeenCalledWith("alpha");
    await expect(uiActionRegistry.run("workspace.create_repository", { workspace_slug: "gamma" })).rejects.toThrow(
      "no card for gamma is on screen",
    );
    view.unmount();
  });

  it("refuses a workspace whose card would not offer the button", async () => {
    const view = wrap(<ul>{card("alpha", "repository")}</ul>);
    await expect(uiActionRegistry.run("workspace.create_repository", { workspace_slug: "alpha" })).rejects.toThrow(
      "alpha's repository is repository; there is nothing to create",
    );
    expect(api.createWorkspaceRepository).not.toHaveBeenCalled();
    view.unmount();
  });
});

describe("triage.set_runtime", () => {
  it("changes the linked ticket's triage runtime, merged, and refuses another ticket", async () => {
    const view = wrap(
      <BranchTriageOverviewPanel
        workspaceSlug="demo"
        branch="feature/x"
        baseBranch="main"
        branchEntry={undefined}
        onReviewDiff={() => {}}
      />,
    );
    await act(() => uiActionRegistry.run("triage.set_runtime", { ticket_id: "lg-7", claude_model: "opus" }));
    expect(api.setTriageRuntime).toHaveBeenCalledWith("lg-7", { ...TRIAGE_RUNTIME, claude_model: "opus" });
    await expect(uiActionRegistry.run("triage.set_runtime", { ticket_id: "lg-8", claude_model: "x" })).rejects.toThrow(
      "this branch's triage is linked to lg-7, not lg-8",
    );
    view.unmount();
  });
});
