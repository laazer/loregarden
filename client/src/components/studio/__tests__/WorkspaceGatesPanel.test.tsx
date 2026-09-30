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
      testWorkspaceGates: jest.fn(),
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
    gates_configured: true,
    gates_commands: ["lefthook run pre-commit --files-from-stdin"],
    gates_transition_script: "ci/scripts/run_workflow_transition_gates.py",
    gates_transition_script_resolved: "ci/scripts/run_workflow_transition_gates.py",
    gates_autofix_commands: [],
    gates_autofix_agent_fallback: true,
    gates_autofix_max_agent_attempts: 3,
    gates_placeholders: { workspace_root: "/repo/blobert", external_id: "SAMPLE-1" },
    gates_suggested_commands: [],
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

const LEFTHOOK = "lefthook run pre-commit --files-from-stdin";

describe("WorkspaceGatesPanel", () => {
  it("loads and displays the selected workspace's gates config", async () => {
    api.orchestrationProfile.mockResolvedValue(profile());
    renderPanel([workspace()]);

    await waitFor(() => expect(api.orchestrationProfile).toHaveBeenCalledWith("blobert"));
    expect(await screen.findByDisplayValue(LEFTHOOK)).toBeInTheDocument();
    expect(screen.getByDisplayValue("ci/scripts/run_workflow_transition_gates.py")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Gates on" })).toBeChecked();
    expect(screen.getByText("Gating transitions")).toBeInTheDocument();
  });

  it("shows the switch on, and warns, when gates are on but nothing would run", async () => {
    api.orchestrationProfile.mockResolvedValue(
      profile({
        gates_enabled: false,
        gates_configured: true,
        gates_commands: [],
        gates_transition_script: "",
        gates_transition_script_resolved: "",
      }),
    );
    renderPanel([workspace()]);

    expect(await screen.findByText("On, but nothing runs")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Gates on" })).toBeChecked();
    expect(screen.getByText(/No checks yet/)).toBeInTheDocument();
  });

  it("saves edited gates config, self-repair settings included", async () => {
    api.orchestrationProfile.mockResolvedValue(profile());
    api.updateWorkspaceGates.mockResolvedValue(profile({ gates_commands: ["ruff check ."] }));
    renderPanel([workspace()]);

    const saveButton = await screen.findByRole("button", { name: /save gates/i });
    expect(saveButton).toBeDisabled();
    fireEvent.change(screen.getByDisplayValue(LEFTHOOK), { target: { value: "ruff check ." } });
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    fireEvent.click(saveButton);

    await waitFor(() =>
      expect(api.updateWorkspaceGates).toHaveBeenCalledWith("blobert", {
        enabled: true,
        commands: ["ruff check ."],
        transition_script: "ci/scripts/run_workflow_transition_gates.py",
        autofix_commands: [],
        autofix_agent_fallback: true,
        autofix_max_agent_attempts: 3,
      }),
    );
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("reorders checks with the move buttons", async () => {
    api.orchestrationProfile.mockResolvedValue(profile({ gates_commands: ["first", "second"] }));
    api.updateWorkspaceGates.mockResolvedValue(profile());
    renderPanel([workspace()]);

    fireEvent.click(await screen.findByRole("button", { name: "Move check 1 down" }));
    fireEvent.click(screen.getByRole("button", { name: /save gates/i }));

    await waitFor(() =>
      expect(api.updateWorkspaceGates).toHaveBeenCalledWith(
        "blobert",
        expect.objectContaining({ commands: ["second", "first"] }),
      ),
    );
  });

  it("inserts a placeholder chip at the caret of the focused check", async () => {
    api.orchestrationProfile.mockResolvedValue(profile({ gates_commands: ["ruff check ."] }));
    renderPanel([workspace()]);

    const input = (await screen.findByDisplayValue("ruff check .")) as HTMLTextAreaElement;
    fireEvent.focus(input);
    input.setSelectionRange(11, 11);
    fireEvent.click(screen.getByRole("button", { name: "{workspace_root}" }));

    expect(await screen.findByDisplayValue("ruff check {workspace_root}.")).toBeInTheDocument();
  });

  it("refuses to save a check with an unclosed quote", async () => {
    api.orchestrationProfile.mockResolvedValue(profile());
    renderPanel([workspace()]);

    fireEvent.change(await screen.findByDisplayValue(LEFTHOOK), {
      target: { value: 'echo "oops' },
    });

    expect(screen.getByRole("alert")).toHaveTextContent(/never closed/);
    expect(screen.getByRole("button", { name: /save gates/i })).toBeDisabled();
  });

  it("tests the draft checks and shows each verdict", async () => {
    api.orchestrationProfile.mockResolvedValue(profile({ gates_commands: ["true", "false"] }));
    api.testWorkspaceGates.mockResolvedValue({
      repo_root: "/repo/blobert",
      transition: "implement_to_verify",
      results: [
        { template: "true", command: "true", outcome: "passed", message: "", stdout: "", stderr: "", duration_ms: 12 },
        { template: "false", command: "false", outcome: "failed", message: "exit code 1", stdout: "", stderr: "lint error", duration_ms: 1500 },
      ],
    });
    renderPanel([workspace()]);

    fireEvent.click(await screen.findByRole("button", { name: "Test checks" }));

    await waitFor(() =>
      expect(api.testWorkspaceGates).toHaveBeenCalledWith("blobert", { commands: ["true", "false"] }),
    );
    expect(await screen.findByText("Passed · 12 ms")).toBeInTheDocument();
    expect(screen.getByText("Failed · 1.5 s")).toBeInTheDocument();
    expect(screen.getByText("lint error")).toBeInTheDocument();
    expect(screen.getByText(/1 passed · 1 failed/)).toBeInTheDocument();
    expect(api.updateWorkspaceGates).not.toHaveBeenCalled();
  });

  it("adds a built-in guardrail in one click", async () => {
    api.orchestrationProfile.mockResolvedValue(
      profile({ gates_suggested_commands: ["node {loregarden_root}/ts_ux_states_check.cjs --repo x"] }),
    );
    renderPanel([workspace()]);

    fireEvent.click(await screen.findByRole("button", { name: "+ ts_ux_states_check.cjs" }));

    expect(
      screen.getByDisplayValue("node {loregarden_root}/ts_ux_states_check.cjs --repo x"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "+ ts_ux_states_check.cjs" })).not.toBeInTheDocument();
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

    await screen.findByDisplayValue(LEFTHOOK);
    fireEvent.change(screen.getByRole("combobox", { name: "Workspace" }), {
      target: { value: "loregarden" },
    });

    expect(await screen.findByDisplayValue("ruff check .")).toBeInTheDocument();
  });

  it("asks before discarding unsaved edits on a workspace switch", async () => {
    api.orchestrationProfile.mockImplementation((slug: string) =>
      Promise.resolve(
        slug === "blobert"
          ? profile()
          : profile({ slug: "loregarden", name: "Loregarden", gates_commands: ["ruff check ."] }),
      ),
    );
    renderPanel([workspace(), workspace({ slug: "loregarden", name: "Loregarden" })]);

    fireEvent.change(await screen.findByDisplayValue(LEFTHOOK), { target: { value: "edited" } });
    const picker = screen.getByRole("combobox", { name: "Workspace" });
    fireEvent.change(picker, { target: { value: "loregarden" } });

    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.getByDisplayValue("edited")).toBeInTheDocument();
    expect(api.orchestrationProfile).not.toHaveBeenCalledWith("loregarden");

    fireEvent.change(picker, { target: { value: "loregarden" } });
    fireEvent.click(screen.getByRole("button", { name: "Discard and switch" }));
    expect(await screen.findByDisplayValue("ruff check .")).toBeInTheDocument();
  });

  it("says what failed and offers a retry when the profile can't load", async () => {
    api.orchestrationProfile.mockRejectedValueOnce(new Error("boom"));
    renderPanel([workspace()]);

    expect(await screen.findByText(/Couldn't load the gate settings.*boom/)).toBeInTheDocument();
    api.orchestrationProfile.mockResolvedValue(profile());
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByDisplayValue(LEFTHOOK)).toBeInTheDocument();
  });

  it("opens on the app-wide active workspace rather than the first in the list", async () => {
    api.orchestrationProfile.mockResolvedValue(profile({ slug: "loregarden", name: "Loregarden" }));
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <WorkspaceGatesPanel
          workspaces={[workspace(), workspace({ slug: "loregarden", name: "Loregarden" })]}
          workspaceSlug="loregarden"
        />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(api.orchestrationProfile).toHaveBeenCalledWith("loregarden"));
    expect(api.orchestrationProfile).not.toHaveBeenCalledWith("blobert");
  });

  it("prompts to select a workspace when none exist", () => {
    renderPanel([]);
    expect(screen.getByText("No workspaces yet.")).toBeInTheDocument();
  });
});
