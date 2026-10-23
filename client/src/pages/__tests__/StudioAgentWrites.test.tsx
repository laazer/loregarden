/** The agent editor's two write surfaces, from the operator's side.
 *
 * AC5, AC6, AC7, AC10 and AC11 of lg-milestone-that-1239:
 *
 * * S1, the Slug field — "what is this agent's fixed id, and how do I get a
 *   different one?" It is fixed after creation, and the answer to the second
 *   half is the Duplicate to custom button, so the field must say so and that
 *   button must exist where it says it.
 * * The save payload — today `saveAgent` sends `slug` on every save, so under
 *   `extra="forbid"` every Studio agent save would 422 while every backend test
 *   stayed green.
 * * S2, the refusal notice — "can I remove this agent, and if not, what is
 *   still using it that I must change first?"
 * * The confirm — this delete has never once succeeded, so AC1 arms a one-click
 *   irreversible destroy for the first time.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api, type StudioAgent, type StudioWorkflow } from "../../api/client";
import { ApiError } from "../../api/http";
import { RouterBridgeSync } from "../../components/RouterBridgeSync";
import { TopbarPageSlot, TopbarPageSlotProvider } from "../../components/TopbarPageSlot";
import { StudioPage } from "../StudioPage";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

const AGENT: StudioAgent = {
  id: "agent-1",
  slug: "godot-ddd-migrator",
  name: "Godot DDD Migrator",
  description: "Migrates Godot scenes to the DDD layout.",
  role_body: "You migrate scenes.",
  role_file: "",
  adapter: "claude",
  default_model: "",
  timeout: 600,
  default_skill: "",
  mcp_enabled: true,
  mcp_tools: ["loregarden_get_ticket"],
  gate_checks: [],
  handoff_checks: [],
  tool_grants: {
    posture: "inherit",
    allowed_tools: [],
    disallowed_tools: [],
    mcp_servers: [],
  },
  tool_grant_warnings: [],
  built_in: false,
  // Hardcoded `false` for every DB row server-side, which is why the Duplicate
  // button's old `isAgentReadOnly` condition rendered it for none of the 31
  // installed agents.
  read_only: false,
  version: 11,
  created_at: "2026-09-01T00:00:00",
  updated_at: "2026-09-02T00:00:00",
};

/** The live shape behind the refusal: the draft `blobert-ddd` publishes the
 *  template `studio-blobert-ddd`, so the two ids the scan reports for one and
 *  the same workflow differ by a `studio-` prefix. Workflow Studio addresses
 *  the draft; only the draft is a place the stage can be removed. */
const DRAFT: StudioWorkflow = {
  id: "wf-1",
  slug: "blobert-ddd",
  name: "Blobert DDD",
  description: "",
  stages: [],
  transitions: [],
  published_template_id: "tpl-1",
  published_template_slug: "studio-blobert-ddd",
  built_in: false,
  read_only: false,
  created_at: "2026-09-01T00:00:00",
  updated_at: "2026-09-02T00:00:00",
};

const REFUSAL_BODY = {
  detail:
    "Cannot delete agent 'godot-ddd-migrator': 1 workflow template(s) " +
    "(studio-blobert-ddd) and 1 draft workflow(s) (blobert-ddd) still name it.",
  references: {
    blocking: [
      { kind: "workflow_template", ids: ["studio-blobert-ddd"], count: 1 },
      { kind: "studio_workflow", ids: ["blobert-ddd"], count: 1 },
    ],
    historical: [{ kind: "agent_run", ids: [], count: 24 }],
  },
};

function refusal(): ApiError {
  return Object.assign(new ApiError(409, REFUSAL_BODY.detail), { body: REFUSAL_BODY });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.studioAgents.mockResolvedValue([AGENT]);
  mockApi.studioWorkflows.mockResolvedValue([DRAFT]);
  // Never published, so the drift notice renders nothing — this file's only
  // `role="status"` is the refusal notice.
  mockApi.studioWorkflowDrift.mockResolvedValue({
    slug: DRAFT.slug,
    published_template_slug: "",
    published: false,
    drifted: false,
    stages_added: [],
    stages_removed: [],
    stages_changed: {},
    draft_transition_count: 0,
    template_transition_count: 0,
    template_version: 0,
    stranded: { count: 0, stage_keys: [], ticket_ids: [] },
  });
  mockApi.studioMcpToolGuides.mockResolvedValue([]);
  mockApi.studioExitActionRequirements.mockResolvedValue({
    requirement_kinds: [],
    capability_ids: [],
    credential_keys: [],
    authority_scopes: [],
  });
  mockApi.studioDefaults.mockResolvedValue({
    mcp_tools: [],
    gate_checks: [],
    handoff_checks: [],
  });
  mockApi.mcpServers.mockResolvedValue([]);
  mockApi.skills.mockResolvedValue([]);
  mockApi.workspaces.mockResolvedValue([]);
  mockApi.runtimeOptions.mockResolvedValue({
    cli_adapters: [],
    claude_models: [],
    cursor_models: [],
  });
  mockApi.previewStudioAgent.mockResolvedValue({
    name: AGENT.name,
    markdown: "# Godot DDD Migrator",
    sections: [],
    profile: {
      description: AGENT.description,
      model: "",
      provider: "claude",
      default_skill: "",
      timeout: 600,
      always_apply: null,
    },
  });
  mockApi.studioAgentVersions.mockResolvedValue([]);
  mockApi.updateStudioAgent.mockResolvedValue(AGENT);
  mockApi.createStudioAgent.mockResolvedValue(AGENT);
  mockApi.deleteStudioAgent.mockResolvedValue({ ok: true });
});

function renderStudio(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        {/* The studio's navigate* helpers go through the router bridge; without
            it every navigation in this page is a silent no-op. */}
        <RouterBridgeSync />
        <TopbarPageSlotProvider>
          <TopbarPageSlot />
          <StudioPage />
        </TopbarPageSlotProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Render the editor on an existing agent, hydrated.
 *
 * The draft is seeded from the agents query, so a test that reads a field
 * before that lands is reading the empty create draft.
 */
async function existingAgent() {
  const result = renderStudio(`/studio/agents/${AGENT.slug}`);
  await screen.findByDisplayValue(AGENT.name);
  return result;
}

async function slugField(): Promise<HTMLInputElement> {
  return (await screen.findByLabelText("Slug")) as HTMLInputElement;
}

// --- AC5: surface S1, the Slug field ---------------------------------------

describe("the Slug field on an existing agent", () => {
  it("is readOnly rather than disabled, so the slug stays selectable to copy", async () => {
    await existingAgent();

    const slug = await slugField();
    expect(slug).toHaveValue(AGENT.slug);
    expect(slug).toHaveAttribute("readonly");
    expect(slug).not.toBeDisabled();
  });

  it("keeps its label and names the alternative through aria-describedby", async () => {
    await existingAgent();

    const slug = await slugField();
    const describedBy = slug.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    const help = document.getElementById(describedBy!);
    expect(help).toHaveTextContent(/fixed after creation/i);
    expect(help).toHaveTextContent(/duplicate to custom/i);
  });

  it("offers the control its help text names", async () => {
    // `read_only` is false for every DB row, so the old
    // `isAgentReadOnly && selectedAgent` condition rendered this for none of
    // the 31 installed agents — the help text would have pointed at nothing.
    await existingAgent();

    expect(
      await screen.findByRole("button", { name: /duplicate to custom/i }),
    ).toBeInTheDocument();
  });

  it("stays editable on the create branch", async () => {
    renderStudio("/studio/agents/new");

    const slug = await slugField();
    expect(slug).not.toHaveAttribute("readonly");
    fireEvent.change(slug, { target: { value: "a-new-agent" } });
    expect(slug).toHaveValue("a-new-agent");
  });
});

// --- AC6: the client stops sending `slug` on PATCH -------------------------

describe("the save payload", () => {
  it("omits slug on the update branch", async () => {
    await existingAgent();
    await slugField();

    fireEvent.click(await screen.findByRole("button", { name: /^save agent$/i }));

    await waitFor(() => expect(mockApi.updateStudioAgent).toHaveBeenCalled());
    const [slug, body] = mockApi.updateStudioAgent.mock.calls[0];
    expect(slug).toBe(AGENT.slug);
    expect(Object.keys(body as object)).not.toContain("slug");
  });

  it("sends no view-only field either, so extra=forbid cannot 422 a save", async () => {
    await existingAgent();
    await slugField();

    fireEvent.click(await screen.findByRole("button", { name: /^save agent$/i }));

    await waitFor(() => expect(mockApi.updateStudioAgent).toHaveBeenCalled());
    const [, body] = mockApi.updateStudioAgent.mock.calls[0];
    for (const viewOnly of [
      "id",
      "version",
      "built_in",
      "read_only",
      "role_file",
      "tool_grant_warnings",
      "created_at",
      "updated_at",
    ]) {
      expect(Object.keys(body as object)).not.toContain(viewOnly);
    }
  });

  it("still sends slug on the create branch", async () => {
    renderStudio("/studio/agents/new");

    const slug = await slugField();
    fireEvent.change(slug, { target: { value: "a-new-agent" } });
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "A New Agent" },
    });
    fireEvent.click(await screen.findByRole("button", { name: /^create agent$/i }));

    await waitFor(() => expect(mockApi.createStudioAgent).toHaveBeenCalled());
    const [body] = mockApi.createStudioAgent.mock.calls[0];
    expect(body).toMatchObject({ slug: "a-new-agent" });
  });
});

// --- AC10 / AC11: the delete confirms, and nothing fires twice -------------

describe("deleting an agent", () => {
  async function openConfirm() {
    await existingAgent();
    fireEvent.click(await screen.findByRole("button", { name: /^delete$/i }));
    return screen.findByRole("dialog");
  }

  it("asks before it destroys", async () => {
    const dialog = await openConfirm();

    expect(mockApi.deleteStudioAgent).not.toHaveBeenCalled();
    expect(dialog).toHaveTextContent(AGENT.slug);
    expect(dialog).toHaveTextContent(String(AGENT.version));
  });

  it("destroys only from the confirm's own action", async () => {
    await openConfirm();

    fireEvent.click(screen.getByRole("button", { name: /^delete agent$/i }));

    await waitFor(() => expect(mockApi.deleteStudioAgent).toHaveBeenCalledWith(AGENT.slug));
  });

  it("cancelling leaves the agent alone", async () => {
    await openConfirm();

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(mockApi.deleteStudioAgent).not.toHaveBeenCalled();
  });

  it("does not remove the agent from the list optimistically", async () => {
    // A refusal that had already removed the row is a row that vanishes and
    // comes back.
    mockApi.deleteStudioAgent.mockRejectedValue(refusal());
    await openConfirm();

    fireEvent.click(screen.getByRole("button", { name: /^delete agent$/i }));

    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    expect(await screen.findByDisplayValue(AGENT.name)).toBeInTheDocument();
  });
});

// --- AC7: surface S2, the refusal notice -----------------------------------

describe("when the delete is refused", () => {
  async function refuseDelete() {
    mockApi.deleteStudioAgent.mockRejectedValue(refusal());
    await existingAgent();
    fireEvent.click(await screen.findByRole("button", { name: /^delete$/i }));
    fireEvent.click(await screen.findByRole("button", { name: /^delete agent$/i }));
    return screen.findByRole("status");
  }

  it("reports what still uses the agent in the panel", async () => {
    const notice = await refuseDelete();

    expect(notice).toHaveTextContent("studio-blobert-ddd");
    expect(notice).toHaveTextContent("blobert-ddd");
  });

  it("links each referencing workflow to Studio → Workflows → that slug", async () => {
    await refuseDelete();

    fireEvent.click(screen.getByRole("button", { name: /^studio-blobert-ddd$/ }));

    // Studio → Workflows → that slug: the agent editor is left behind, which is
    // the point — the operator's next step is to remove the stage that names it.
    await waitFor(() => expect(screen.queryByDisplayValue(AGENT.name)).toBeNull());
    // And it lands on the draft that published the template, not on the
    // template id, which Workflow Studio does not address: that redirects to
    // the blank new-workflow form and 404s its drift request.
    expect(await screen.findByDisplayValue(DRAFT.name)).toBeInTheDocument();
  });

  it("reports a template with no draft behind it as text, not as a control", async () => {
    // A hand-authored template is editable nowhere in Workflow Studio. The id
    // is still what the operator has to go and find, so it is reported — but a
    // control for it loses the refusal and lands on an Untitled form.
    mockApi.studioWorkflows.mockResolvedValue([]);
    await refuseDelete();

    const notice = screen.getByRole("status");
    expect(notice).toHaveTextContent("studio-blobert-ddd");
    expect(screen.queryByRole("button", { name: /^studio-blobert-ddd$/ })).toBeNull();
  });

  it("clears when the operator edits the draft", async () => {
    await refuseDelete();

    fireEvent.change(await screen.findByLabelText("Description"), {
      target: { value: "Edited after the refusal." },
    });

    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  });

  it("renders no notice before a delete has been refused", async () => {
    await existingAgent();
    await slugField();

    expect(screen.queryByRole("status")).toBeNull();
  });
});
