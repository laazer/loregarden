import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { OrchestrationProfileView, WorkspaceSummary } from "../../../api/types";
import { WorkspaceGatesPanel } from "../WorkspaceGatesPanel";

jest.mock("../../../api/client", () => {
  const originalClient = jest.requireActual("../../../api/client");
  return {
    ...originalClient,
    api: {
      ...originalClient.api,
      orchestrationProfile: jest.fn(),
      updateWorkspaceGates: jest.fn(),
    },
  };
});

const { api } = require("../../../api/client");

function workspace(overrides: Partial<WorkspaceSummary> = {}): WorkspaceSummary {
  return {
    id: "ws-1",
    slug: "blobert",
    name: "Blobert",
    repo_path: "/repo/blobert",
    repo_root: "/repo/blobert",
    repo_exists: true,
    ticket_count: 0,
    blocked_count: 0,
    workflow_template_slug: "blobert-tdd",
    cli_adapter: "claude",
    ...overrides,
  } as WorkspaceSummary;
}

function profile(overrides: Partial<OrchestrationProfileView> = {}): OrchestrationProfileView {
  return {
    slug: "blobert",
    name: "Blobert Godot TDD",
    driver: "builtin_autopilot",
    workflow_template: "blobert-tdd",
    orchestrator_skill: "autopilot",
    gates_enabled: true,
    gates_commands: ["lefthook run pre-commit --files-from-stdin"],
    gates_transition_script: "ci/scripts/run_workflow_transition_gates.py",
    max_stages_per_run: 0,
    ...overrides,
  };
}

function renderPanel(workspaces: WorkspaceSummary[]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkspaceGatesPanel workspaces={workspaces} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe("WorkspaceGatesPanel", () => {
  it("loads and displays the selected workspace's gates config", async () => {
    api.orchestrationProfile.mockResolvedValue(profile());
    renderPanel([workspace()]);

    await waitFor(() => expect(api.orchestrationProfile).toHaveBeenCalledWith("blobert"));
    expect(await screen.findByDisplayValue("lefthook run pre-commit --files-from-stdin")).toBeInTheDocument();
    expect(screen.getByDisplayValue("ci/scripts/run_workflow_transition_gates.py")).toBeInTheDocument();
    expect(screen.getByRole("checkbox")).toBeChecked();
  });

  it("saves edited gates config for the selected workspace", async () => {
    api.orchestrationProfile.mockResolvedValue(profile());
    api.updateWorkspaceGates.mockResolvedValue(profile({ gates_commands: ["ruff check ."] }));
    renderPanel([workspace()]);

    const commandInput = await screen.findByDisplayValue(
      "lefthook run pre-commit --files-from-stdin",
    );
    fireEvent.change(commandInput, { target: { value: "ruff check ." } });
    fireEvent.click(screen.getByRole("button", { name: /save gates/i }));

    await waitFor(() =>
      expect(api.updateWorkspaceGates).toHaveBeenCalledWith("blobert", {
        enabled: true,
        commands: ["ruff check ."],
        transition_script: "ci/scripts/run_workflow_transition_gates.py",
      }),
    );
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("reloads the form when switching workspaces", async () => {
    api.orchestrationProfile.mockImplementation((slug: string) =>
      Promise.resolve(
        slug === "blobert"
          ? profile()
          : profile({ slug: "loregarden", name: "Loregarden", gates_commands: ["ruff check ."] }),
      ),
    );
    renderPanel([workspace(), workspace({ slug: "loregarden", name: "Loregarden" })]);

    await screen.findByDisplayValue("lefthook run pre-commit --files-from-stdin");
    fireEvent.click(screen.getByRole("button", { name: "Loregarden" }));

    expect(await screen.findByDisplayValue("ruff check .")).toBeInTheDocument();
  });

  it("prompts to select a workspace when none exist", () => {
    renderPanel([]);
    expect(screen.getByText("No workspaces yet.")).toBeInTheDocument();
  });
});
