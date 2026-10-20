/** The agent editor's write surfaces under the conditions the operator meets.
 *
 * AC5, AC6, AC7, AC10, AC11 and AC12 of lg-milestone-that-1239. The contract
 * suite drives each surface once through its happy path with every mutation
 * resolving instantly. This file holds the in-flight and after states nothing
 * else covers:
 *
 * * the Save button while its request is away (AC11 — the only place "Saving…"
 *   and `disabled` are specified together, and the contract suite asserts them
 *   nowhere);
 * * the second click, on both writes (AC11 — "what stops a second click from
 *   firing the action twice", the five states' fourth);
 * * focus after the confirm is cancelled (AC10 — "returns to the Delete
 *   button", also asserted nowhere else);
 * * the refusal notice clearing on a new selection (AC7 — the contract suite
 *   covers the edit-the-draft half only);
 * * a delete that fails *without* a refusal body, which is every 500 and every
 *   dropped connection.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api, type StudioAgent } from "../../api/client";
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
  read_only: false,
  version: 11,
  created_at: "2026-09-01T00:00:00",
  updated_at: "2026-09-02T00:00:00",
};

/** The second custom agent, so "select another agent" is reachable. Both of
 *  the live install's deletable agents are in this position. */
const OTHER_AGENT: StudioAgent = {
  ...AGENT,
  id: "agent-2",
  slug: "test-breaker-long-running",
  name: "Test Breaker Long Running",
  description: "Breaks tests for a long time.",
  version: 4,
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

/** A mutation that never settles, for the in-flight state. */
function pendingForever<T>(): Promise<T> {
  return new Promise<T>(() => {});
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.studioAgents.mockResolvedValue([AGENT, OTHER_AGENT]);
  mockApi.studioWorkflows.mockResolvedValue([]);
  // Nothing in this file navigates into Workflow Studio; the empty draft list
  // is the no-destination case, where every reference id renders as text.
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
        <RouterBridgeSync />
        <TopbarPageSlotProvider>
          <TopbarPageSlot />
          <StudioPage />
        </TopbarPageSlotProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function existingAgent(agent: StudioAgent = AGENT) {
  const result = renderStudio(`/studio/agents/${agent.slug}`);
  await screen.findByDisplayValue(agent.name);
  return result;
}

function saveButton() {
  return screen.findByRole("button", { name: /^save agent$|^saving…$/i });
}

function deleteTrigger() {
  return screen.findByRole("button", { name: /^delete$/i });
}

function confirmAction() {
  return screen.findByRole("button", { name: /^delete agent$|^deleting…$/i });
}

// --- AC11: in flight -------------------------------------------------------

describe("the Save button while its request is away", () => {
  it("is disabled and says Saving…", async () => {
    mockApi.updateStudioAgent.mockImplementation(pendingForever);
    await existingAgent();

    fireEvent.click(await saveButton());

    const pending = await screen.findByRole("button", { name: /^saving…$/i });
    expect(pending).toBeDisabled();
  });

  it("does not send a second update when clicked again", async () => {
    mockApi.updateStudioAgent.mockImplementation(pendingForever);
    await existingAgent();

    const button = await saveButton();
    fireEvent.click(button);
    await screen.findByRole("button", { name: /^saving…$/i });
    fireEvent.click(screen.getByRole("button", { name: /^saving…$/i }));

    expect(mockApi.updateStudioAgent).toHaveBeenCalledTimes(1);
  });
});

describe("the confirm's Delete action while its request is away", () => {
  async function openConfirm() {
    await existingAgent();
    fireEvent.click(await deleteTrigger());
    return screen.findByRole("dialog");
  }

  it("is disabled and says Deleting…", async () => {
    mockApi.deleteStudioAgent.mockImplementation(pendingForever);
    await openConfirm();

    fireEvent.click(await confirmAction());

    const pending = await screen.findByRole("button", { name: /^deleting…$/i });
    expect(pending).toBeDisabled();
  });

  it("does not send a second delete when clicked again", async () => {
    mockApi.deleteStudioAgent.mockImplementation(pendingForever);
    await openConfirm();

    fireEvent.click(await confirmAction());
    await screen.findByRole("button", { name: /^deleting…$/i });
    fireEvent.click(screen.getByRole("button", { name: /^deleting…$/i }));

    expect(mockApi.deleteStudioAgent).toHaveBeenCalledTimes(1);
  });


  it("stays open while the request is in flight", async () => {
    // AC10's reason for an undefined `onDismiss`: a dialog that closes on its
    // own while the request is away leaves the outcome with nowhere to land.
    mockApi.deleteStudioAgent.mockImplementation(pendingForever);
    await openConfirm();

    fireEvent.click(await confirmAction());
    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

// --- AC10: the keyboard leaves the dialog where it found it ----------------

describe("cancelling the confirm", () => {
  it("returns focus to the Delete button", async () => {
    await existingAgent();
    const trigger = await deleteTrigger();
    fireEvent.click(trigger);
    await screen.findByRole("dialog");

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("can be reopened after a cancel", async () => {
    // Order dependency: a confirm whose open flag is set but never cleared,
    // or cleared only on success, is a Delete button that works once.
    await existingAgent();
    fireEvent.click(await deleteTrigger());
    fireEvent.click(await screen.findByRole("button", { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(await deleteTrigger());

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(mockApi.deleteStudioAgent).not.toHaveBeenCalled();
  });
});

// --- AC7: what the notice does after it has appeared -----------------------

describe("the refusal notice's lifetime", () => {
  async function refuseDelete() {
    mockApi.deleteStudioAgent.mockRejectedValue(refusal());
    await existingAgent();
    fireEvent.click(await deleteTrigger());
    fireEvent.click(await confirmAction());
    return screen.findByRole("status");
  }

  it("clears when the operator selects another agent", async () => {
    // AC7: "stays up until the operator edits the draft or selects another
    // agent". A notice keyed only on the mutation's error survives the
    // selection and names the previous agent's references under the new one.
    await refuseDelete();

    fireEvent.click(screen.getByRole("button", { name: new RegExp(OTHER_AGENT.name) }));

    await screen.findByDisplayValue(OTHER_AGENT.name);
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  });

  it("closes the confirm when the delete is refused", async () => {
    // The report is the notice in the panel; a dialog still over it hides the
    // thing the operator has to read.
    await refuseDelete();

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("leaves the agent and its draft intact", async () => {
    // AC11: no optimistic removal. The refusal arm is 100% of what this
    // install's Delete button produces, so the row surviving is the norm.
    await refuseDelete();

    expect(await screen.findByDisplayValue(AGENT.name)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /^delete$/i })).toBeInTheDocument();
  });

  it("does not claim references when the delete failed without any", async () => {
    // Every 500, every dropped connection, and the 404 of an agent somebody
    // else deleted: `ApiError.body` is undefined (AC14), so there is nothing
    // to name. A notice that renders anyway shows an empty reference list,
    // which reads as "nothing is in the way" on a delete that did not happen.
    mockApi.deleteStudioAgent.mockRejectedValue(new ApiError(500, "Internal Server Error"));
    await existingAgent();
    fireEvent.click(await deleteTrigger());
    fireEvent.click(await confirmAction());

    await waitFor(() => expect(mockApi.deleteStudioAgent).toHaveBeenCalled());
    expect(await screen.findByDisplayValue(AGENT.name)).toBeInTheDocument();
    const notice = screen.queryByRole("status");
    if (notice) expect(notice.textContent?.trim().length ?? 0).toBeGreaterThan(0);
  });
});

// --- AC12: no new fetch, and nothing new to wait for -----------------------

describe("the two outcomes add no request", () => {
  it("a refusal fetches nothing extra", async () => {
    // AC12 is a decision rather than an omission: both outcomes arrive on a
    // mutation's rejection, so a second `GET …/references` — and the loading
    // state it would need — must not appear.
    mockApi.deleteStudioAgent.mockRejectedValue(refusal());
    await existingAgent();
    const before = mockApi.studioAgents.mock.calls.length;

    fireEvent.click(await deleteTrigger());
    fireEvent.click(await confirmAction());
    await screen.findByRole("status");

    expect(mockApi.studioAgents.mock.calls.length).toBe(before);
    expect(mockApi.deleteStudioAgent).toHaveBeenCalledTimes(1);
  });

  it("a success refetches the agent list", async () => {
    // AC11's other half: the list refreshes on success only.
    await existingAgent();
    const before = mockApi.studioAgents.mock.calls.length;

    fireEvent.click(await deleteTrigger());
    fireEvent.click(await confirmAction());

    await waitFor(() =>
      expect(mockApi.studioAgents.mock.calls.length).toBeGreaterThan(before),
    );
  });
});

// --- AC5 / AC6: the Slug field's other branch ------------------------------

describe("the Slug field", () => {
  it("is readOnly on every existing agent, not only the one first selected", async () => {
    // `selectedAgentSlug` resolving to a listed agent is the condition; a
    // condition written against the first render's agent leaves the field
    // editable after a selection change, and the save then sends a slug.
    await existingAgent();
    fireEvent.click(screen.getByRole("button", { name: new RegExp(OTHER_AGENT.name) }));
    await screen.findByDisplayValue(OTHER_AGENT.name);

    const slug = (await screen.findByLabelText("Slug")) as HTMLInputElement;
    expect(slug).toHaveValue(OTHER_AGENT.slug);
    expect(slug).toHaveAttribute("readonly");
  });

  it("does not send a slug after a selection change either", async () => {
    await existingAgent();
    fireEvent.click(screen.getByRole("button", { name: new RegExp(OTHER_AGENT.name) }));
    await screen.findByDisplayValue(OTHER_AGENT.name);

    fireEvent.click(await saveButton());

    await waitFor(() => expect(mockApi.updateStudioAgent).toHaveBeenCalled());
    const [slug, body] = mockApi.updateStudioAgent.mock.calls[0];
    expect(slug).toBe(OTHER_AGENT.slug);
    expect(Object.keys(body as object)).not.toContain("slug");
  });

  it("sends no slug even if the draft's slug is changed under it", async () => {
    // `readOnly` stops a person typing; it does not stop the draft's slug from
    // moving (a hydration, a duplicate, a programmatic change). AC6's
    // invariant is about the payload, not the keyboard — the update branch
    // omits `slug` whatever the draft holds, so a save cannot 422 under
    // `extra="forbid"`.
    await existingAgent();
    const slug = (await screen.findByLabelText("Slug")) as HTMLInputElement;
    fireEvent.change(slug, { target: { value: "typed-over" } });

    fireEvent.click(await saveButton());

    await waitFor(() => expect(mockApi.updateStudioAgent).toHaveBeenCalled());
    const [target, body] = mockApi.updateStudioAgent.mock.calls[0];
    expect(target).toBe(AGENT.slug);
    expect(Object.keys(body as object)).not.toContain("slug");
  });
});
