/**
 * Agent actions on the workspace and reference-repo surfaces: each is offered
 * only while its control is, runs the same mutation the control runs, and
 * refuses — by name, writing nothing — a target the operator is not looking at.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { uiActionRegistry } from "../../lib/agentActions/registry";
import { DashboardWorkspacesPane } from "../dashboard/DashboardWorkspacesPane";
import { ReferenceRepoPicker } from "../studio/ReferenceRepos";
import { WorkspacesTab } from "../workspaces/WorkspacesTab";

const api = {
  workspaces: jest.fn(),
  archiveWorkspace: jest.fn(),
  restoreWorkspace: jest.fn(),
  setWorkspaceTemplate: jest.fn(),
  referenceRepos: jest.fn(),
  addReferenceRepo: jest.fn(),
  syncReferenceRepo: jest.fn(),
};

jest.mock("../../api/client", () => ({
  api: new Proxy({}, { get: (_target, name: string) => (...args: unknown[]) => api[name as keyof typeof api](...args) }),
}));
jest.mock("../../api/localInstancesApi", () => ({
  localInstancesApi: {
    workspaceTemplates: async () => [
      { slug: "alpha", name: "Alpha" },
      { slug: "beta", name: "Beta" },
    ],
  },
}));
// The cards' own contents are not under test; the tab's archive mutation is.
jest.mock("../instances/WorkspaceSetupCard", () => ({ WorkspaceSetupCard: () => null }));
jest.mock("../workspaces/AddWorkspaceFlow", () => ({ AddWorkspaceFlow: () => null }));

const summary = (slug: string, archived = false) => ({
  id: `ws-${slug}`,
  slug,
  name: slug,
  archived_at: archived ? "2026-10-01T00:00:00Z" : null,
  workflow_template_slug: "tdd",
});

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  api.workspaces.mockResolvedValue([summary("alpha"), summary("beta", true)]);
  api.archiveWorkspace.mockImplementation(async (slug: string) => summary(slug, true));
  api.restoreWorkspace.mockImplementation(async (slug: string) => summary(slug, false));
  api.setWorkspaceTemplate.mockResolvedValue({});
  api.referenceRepos.mockResolvedValue([{ id: "repo-1", url: "https://example.com/r.git", name: "r" }]);
  api.addReferenceRepo.mockResolvedValue({ id: "repo-2", url: "https://example.com/new.git" });
  api.syncReferenceRepo.mockResolvedValue({ id: "repo-1", url: "https://example.com/r.git" });
});

afterEach(() => expect(uiActionRegistry.available()).toEqual([]));

describe("workspace.archive / workspace.restore", () => {
  it("archives an active workspace and restores an archived one, through the tab's mutation", async () => {
    const view = wrap(<WorkspacesTab />);
    await waitFor(() => expect(api.workspaces).toHaveBeenCalled());

    await waitFor(async () =>
      expect(await uiActionRegistry.run("workspace.archive", { workspace_slug: "alpha" })).toEqual({
        workspace_slug: "alpha",
        archived: true,
      }),
    );
    expect(api.archiveWorkspace).toHaveBeenCalledWith("alpha");

    await act(() => uiActionRegistry.run("workspace.restore", { workspace_slug: "beta" }));
    expect(api.restoreWorkspace).toHaveBeenCalledWith("beta");
    view.unmount();
  });

  it("refuses an unknown workspace, or one already in the asked-for state", async () => {
    const view = wrap(<WorkspacesTab />);
    await waitFor(() => expect(api.workspaces).toHaveBeenCalled());
    await waitFor(() =>
      expect(uiActionRegistry.run("workspace.archive", { workspace_slug: "nope" })).rejects.toThrow(
        "no workspace named nope",
      ),
    );
    await expect(uiActionRegistry.run("workspace.archive", { workspace_slug: "beta" })).rejects.toThrow(
      "beta is already archived",
    );
    expect(api.archiveWorkspace).not.toHaveBeenCalled();
    view.unmount();
  });
});

describe("workspace.set_workflow", () => {
  const pane = (selected: string) => (
    <DashboardWorkspacesPane
      workspaces={[summary("alpha")] as never}
      archivedCount={0}
      selected={selected}
      allTicketCount={0}
      workflowTemplates={[{ slug: "tdd", name: "TDD", stage_count: 5 }, { slug: "lite", name: "Lite", stage_count: 2 }] as never}
      fill={false}
      hideDisabled={false}
      onSelect={() => {}}
      onAdd={() => {}}
      onHide={() => {}}
    />
  );

  it("is offered only while a workspace is selected, as the selector is", () => {
    const view = wrap(pane("all"));
    expect(uiActionRegistry.available()).not.toContain("workspace.set_workflow");
    view.unmount();
  });

  it("sets the selected workspace's template, and refuses another workspace or an unknown template", async () => {
    const view = wrap(pane("alpha"));
    await act(() => uiActionRegistry.run("workspace.set_workflow", { workspace_slug: "alpha", template: "lite" }));
    expect(api.setWorkspaceTemplate).toHaveBeenCalledWith("alpha", "lite");

    await expect(
      uiActionRegistry.run("workspace.set_workflow", { workspace_slug: "beta", template: "lite" }),
    ).rejects.toThrow("beta is not the selected workspace (alpha); select it first");
    await expect(
      uiActionRegistry.run("workspace.set_workflow", { workspace_slug: "alpha", template: "nope" }),
    ).rejects.toThrow("no workflow template named nope");
    expect(api.setWorkspaceTemplate).toHaveBeenCalledTimes(1);
    view.unmount();
  });
});

describe("reference_repo.add / reference_repo.sync", () => {
  it("adds to the open picker's workspace, and syncs a repo it lists", async () => {
    const onChange = jest.fn();
    const view = wrap(<ReferenceRepoPicker workspaceSlug="alpha" selectedIds={[]} onChange={onChange} />);
    await waitFor(() => expect(api.referenceRepos).toHaveBeenCalled());

    await act(() =>
      uiActionRegistry.run("reference_repo.add", { workspace_slug: "alpha", url: " https://example.com/new.git " }),
    );
    expect(api.addReferenceRepo).toHaveBeenCalledWith({
      workspace_slug: "alpha",
      url: "https://example.com/new.git",
      notes: "",
    });
    expect(onChange).toHaveBeenCalledWith(["repo-2"]);

    await waitFor(async () =>
      expect(await uiActionRegistry.run("reference_repo.sync", { reference_repo_id: "repo-1" })).toEqual({
        reference_repo_id: "repo-1",
        url: "https://example.com/r.git",
      }),
    );
    view.unmount();
  });

  it("refuses another workspace, a repo it does not list, and everything while disabled", async () => {
    const view = wrap(<ReferenceRepoPicker workspaceSlug="alpha" selectedIds={[]} onChange={() => {}} />);
    await waitFor(() => expect(api.referenceRepos).toHaveBeenCalled());
    await expect(
      uiActionRegistry.run("reference_repo.add", { workspace_slug: "beta", url: "https://x" }),
    ).rejects.toThrow("this picker is for alpha, not beta");
    await expect(uiActionRegistry.run("reference_repo.sync", { reference_repo_id: "repo-9" })).rejects.toThrow(
      "no reference repo repo-9",
    );
    expect(api.addReferenceRepo).not.toHaveBeenCalled();
    expect(api.syncReferenceRepo).not.toHaveBeenCalled();
    view.unmount();

    const disabled = wrap(<ReferenceRepoPicker workspaceSlug="alpha" selectedIds={[]} onChange={() => {}} disabled />);
    expect(uiActionRegistry.available()).toEqual([]);
    disabled.unmount();
  });
});
