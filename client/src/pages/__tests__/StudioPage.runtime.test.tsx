/**
 * Agent Studio → Runtime & tools after a role no longer declares a CLI (R8, R9).
 *
 * The editor moved off `StudioPage` into `AgentEditor`, so this renders the
 * page on an agent's route and opens the Runtime tab the way an operator does.
 * Creation is `NewAgentDialog` now; its payload is covered by that dialog's own
 * test.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api, type StudioAgent } from "../../api/client";
import { createQueryClient } from "../../api/queryClient";
import { studioAgent } from "../../test/mcpFixtures";
import { useToastStore } from "../../state/toastStore";
import { TopbarPageSlot, TopbarPageSlotProvider } from "../../components/TopbarPageSlot";
import { StudioPage } from "../StudioPage";

jest.mock("../../api/client", () => {
  const actual = jest.requireActual("../../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      studioMcpToolGuides: jest.fn(),
      studioExitActionRequirements: jest.fn(),
      studioDefaults: jest.fn(),
      mcpServers: jest.fn(),
      studioAgents: jest.fn(),
      studioAgentRunRecord: jest.fn(),
      studioWorkflows: jest.fn(),
      skills: jest.fn(),
      workspaces: jest.fn(),
      runtimeOptions: jest.fn(),
      previewStudioAgent: jest.fn(),
      studioAgentVersions: jest.fn(),
      generateStudioAgent: jest.fn(),
      createStudioAgent: jest.fn(),
      updateStudioAgent: jest.fn(),
    },
  };
});

const mockedApi = api as jest.Mocked<typeof api>;

const CLAUDE_MODELS = [
  { id: "", label: "Default (Claude Code profile)" },
  { id: "claude-opus-5", label: "Claude Opus 5 (pinned)" },
  { id: "claude-sonnet-4-5", label: "Claude Sonnet 4.5" },
];

const CURSOR_MODELS = [
  { id: "", label: "Default (Cursor profile)" },
  { id: "gpt-5", label: "GPT-5" },
];

function emptyQueryClient() {
  const client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false }, mutations: { retry: false } });
  return client;
}

function mockStudioApis() {
  mockedApi.studioMcpToolGuides.mockResolvedValue([]);
  mockedApi.studioExitActionRequirements.mockResolvedValue({
    requirement_kinds: ["runtime_capability", "credential", "authority", "operator_judgment"],
    capability_ids: [],
    credential_keys: [],
    authority_scopes: [],
  });
  mockedApi.studioDefaults.mockResolvedValue({
    mcp_tools: [],
    gate_checks: [],
    handoff_checks: [],
    skip_conditions: [],
  });
  mockedApi.mcpServers.mockResolvedValue([]);
  mockedApi.studioAgents.mockResolvedValue([
    // Leftover `adapter: cursor` must not key the Default model list (R8.2).
    // Inject via unknown: StudioAgent has no adapter field (R1).
    { ...studioAgent({ slug: "planner", name: "Planner", built_in: true }), adapter: "cursor" },
    {
      ...studioAgent({ slug: "reviewer", name: "Reviewer", built_in: false, default_model: "" }),
      adapter: "cursor",
    },
  ] as unknown as StudioAgent[]);
  mockedApi.studioAgentRunRecord.mockResolvedValue({ agent_id: "reviewer", stages: [], recent: [] });
  mockedApi.studioWorkflows.mockResolvedValue([]);
  mockedApi.skills.mockResolvedValue(["plan", "implement"]);
  mockedApi.workspaces.mockResolvedValue([]);
  mockedApi.runtimeOptions.mockResolvedValue({
    cli_adapters: [],
    claude_models: CLAUDE_MODELS,
    cursor_models: CURSOR_MODELS,
    codex_models: [],
    lmstudio_models: [],
    opencode_models: [],
  });
  mockedApi.previewStudioAgent.mockResolvedValue({
    name: "Draft",
    markdown: "",
    sections: ["header", "role"],
    profile: {
      description: "",
      model: "",
      default_skill: "",
      timeout: 600,
      always_apply: null,
    },
  });
  mockedApi.studioAgentVersions.mockResolvedValue([]);
}

function renderStudio(path = "/studio/agents/reviewer") {
  const client = emptyQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <TopbarPageSlotProvider>
        <TopbarPageSlot />
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/studio" element={<StudioPage />} />
            <Route path="/studio/:studioSection/:resourceId/*" element={<StudioPage />} />
            <Route path="/studio/:studioSection/*" element={<StudioPage />} />
          </Routes>
        </MemoryRouter>
      </TopbarPageSlotProvider>
    </QueryClientProvider>,
  );
}

/** Open one agent's Runtime & tools tab, the way the operator reaches it. */
async function openRuntimeTab(path?: string) {
  const user = userEvent.setup();
  renderStudio(path);
  await user.click(await screen.findByRole("tab", { name: "Runtime & tools" }));
  return user;
}

beforeEach(() => {
  useToastStore.getState().clear();
  jest.clearAllMocks();
  mockStudioApis();
});

describe("Studio Agents Runtime (R8)", () => {
  it("has no Provider select; remaining controls are skill, model, timeout", async () => {
    await openRuntimeTab();
    await screen.findByText("Runtime");

    expect(screen.queryByLabelText("Provider")).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Provider" })).toBeNull();
    expect(screen.getByLabelText("Default skill")).toBeInTheDocument();
    expect(screen.getByLabelText("Default model")).toBeInTheDocument();
    expect(screen.getByLabelText("Timeout (s)")).toBeInTheDocument();
  });

  it("tabs from Default skill to Default model to Timeout", async () => {
    const user = await openRuntimeTab();
    const skill = await screen.findByLabelText("Default skill");
    const model = screen.getByLabelText("Default model");
    const timeout = screen.getByLabelText("Timeout (s)");

    skill.focus();
    expect(skill).toHaveFocus();
    await user.tab();
    expect(model).toHaveFocus();
    await user.tab();
    expect(timeout).toHaveFocus();
  });

  it("Default model is a Claude-family native select, not the catalog empty label", async () => {
    await openRuntimeTab();
    const model = await screen.findByLabelText("Default model");
    expect(model.tagName).toBe("SELECT");
    expect(await within(model).findByRole("option", { name: "Claude Opus 5 (pinned)" })).toBeInTheDocument();
    const options = within(model).getAllByRole("option");
    expect(options[0]).toHaveValue("");
    expect(options[0]).toHaveTextContent("No pin — run uses the selected harness default");
    expect(within(model).queryByRole("option", { name: "GPT-5" })).toBeNull();
    expect(within(model).queryByRole("option", { name: "Default (Claude Code profile)" })).toBeNull();
  });

  it("hints that the pin applies only when the run harness is Claude", async () => {
    await openRuntimeTab();
    const model = await screen.findByLabelText("Default model");
    const hint = await screen.findByText("Applies only when a run's harness is Claude.");
    expect(model).toHaveAttribute("aria-describedby", hint.id);
  });

  it("shows Runtime once, for the one agent the route names", async () => {
    await openRuntimeTab("/studio/agents/planner");
    expect(await screen.findByText("Runtime")).toBeInTheDocument();
    expect(screen.getAllByText("Runtime")).toHaveLength(1);
    expect(screen.getByLabelText("Default model").tagName).toBe("SELECT");
  });
});

describe("Studio Agents Default model five states (R9)", () => {
  it("renders the Select immediately while runtimeOptions is still loading — no spinner, no free-text", async () => {
    mockedApi.runtimeOptions.mockReturnValue(new Promise(() => {}));
    await openRuntimeTab();
    const model = await screen.findByLabelText("Default model");
    expect(model.tagName).toBe("SELECT");
    expect(within(model).getByRole("option", { name: /No pin/ })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Model id")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("empty pin is the selected empty option, not an error", async () => {
    await openRuntimeTab();
    const model = await screen.findByLabelText("Default model");
    expect(model).toHaveValue("");
    expect(screen.queryByText(/missing provider/i)).toBeNull();
    expect(screen.queryByText(/required/i)).toBeNull();
  });

  it("keeps a stored value that is not yet in the Claude list", async () => {
    mockedApi.studioAgents.mockResolvedValue([
      studioAgent({
        slug: "reviewer",
        name: "Reviewer",
        built_in: false,
        default_model: "claude-opus-legacy",
      }),
    ]);
    await openRuntimeTab();
    const model = await screen.findByLabelText("Default model");
    expect(model).toHaveValue("claude-opus-legacy");
    expect(within(model).getByRole("option", { name: "claude-opus-legacy" })).toBeInTheDocument();
  });

  it("toasts Load runtime options on a cold failure and keeps the empty Select", async () => {
    mockedApi.runtimeOptions.mockRejectedValue(new Error("probe down"));
    await openRuntimeTab();
    const model = await screen.findByLabelText("Default model");
    expect(model.tagName).toBe("SELECT");
    expect(within(model).getByRole("option", { name: /No pin/ })).toBeInTheDocument();
    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some((t) => t.title === "Load runtime options failed"),
      ).toBe(true);
    });
  });

  it("disables Save while pending, labels it Saving…, and leaves Default model operable", async () => {
    mockedApi.updateStudioAgent.mockReturnValue(new Promise(() => {}));
    const user = await openRuntimeTab();
    const model = await screen.findByLabelText("Default model");
    await user.selectOptions(model, "claude-opus-5");
    await user.click(screen.getByRole("button", { name: "Save agent" }));
    expect(await screen.findByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(screen.getByLabelText("Default model")).not.toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Saving…" }));
    expect(mockedApi.updateStudioAgent).toHaveBeenCalledTimes(1);
  });

  it("saves the pin without an adapter beside it", async () => {
    mockedApi.updateStudioAgent.mockResolvedValue(
      studioAgent({ slug: "reviewer", name: "Reviewer", built_in: false }),
    );
    const user = await openRuntimeTab();
    await user.selectOptions(await screen.findByLabelText("Default model"), "claude-opus-5");
    await user.click(screen.getByRole("button", { name: "Save agent" }));
    await waitFor(() => expect(mockedApi.updateStudioAgent).toHaveBeenCalled());
    const payload = mockedApi.updateStudioAgent.mock.calls[0][1] as Record<string, unknown>;
    expect(payload.default_model).toBe("claude-opus-5");
    expect(payload).not.toHaveProperty("adapter");
  });

  it("wires the Default model label to the native select", async () => {
    await openRuntimeTab();
    const model = await screen.findByLabelText("Default model");
    expect(model.tagName).toBe("SELECT");
    expect(model).toHaveAttribute("id");
    const label = screen.getByText("Default model");
    expect(label.tagName).toBe("LABEL");
    expect(label).toHaveAttribute("for", model.getAttribute("id"));
  });

  it("keeps a stored value on the Select while runtimeOptions is still loading", async () => {
    mockedApi.runtimeOptions.mockReturnValue(new Promise(() => {}));
    mockedApi.studioAgents.mockResolvedValue([
      studioAgent({
        slug: "reviewer",
        name: "Reviewer",
        built_in: false,
        default_model: "claude-opus-legacy",
      }),
    ]);
    await openRuntimeTab();
    const model = await screen.findByLabelText("Default model");
    expect(model.tagName).toBe("SELECT");
    expect(model).toHaveValue("claude-opus-legacy");
    expect(within(model).getByRole("option", { name: "claude-opus-legacy" })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Model id")).toBeNull();
  });
});
