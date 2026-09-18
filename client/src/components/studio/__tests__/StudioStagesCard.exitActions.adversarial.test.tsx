/**
 * ADVERSARIAL / AC-1–AC-3 + Studio UI plan: stage authoring must use typed
 * exit_actions, not gate_required, and must not encode agentlessness as a
 * human decision.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import type { StudioWorkflowStage } from "../../../api/client";
import { StudioStagesCard } from "../StudioStagesCard";
import { emptyStage, type StudioWorkflowDraft } from "../studioWorkflowHelpers";

const EXIT_ACTION_REQUIREMENTS = {
  requirement_kinds: ["runtime_capability", "credential", "authority", "operator_judgment"],
  capability_ids: ["http_test_client"],
  credential_keys: ["claude_profile"],
  authority_scopes: ["release:publish"],
} as const;

const AGENTS = [
  { slug: "planner", name: "Planner", built_in: true, adapter: "claude" },
  { slug: "verifier", name: "Verifier", built_in: true, adapter: "claude" },
] as never[];

type ExitActionStage = StudioWorkflowStage & {
  exit_actions_enabled?: boolean;
  exit_actions?: Array<{
    key: string;
    label: string;
    description?: string;
    requirement:
      | { kind: "runtime_capability"; capability_id: string }
      | { kind: "credential"; credential_key: string }
      | { kind: "authority"; authority_scope: string }
      | { kind: "operator_judgment"; decision_prompt: string };
  }>;
};

function draftWith(...stages: StudioWorkflowDraft["stages"]): StudioWorkflowDraft {
  return { slug: "wf", name: "WF", description: "", stages, transitions: [] };
}

function renderCard(initial: StudioWorkflowDraft, readOnly = false) {
  const seen: StudioWorkflowDraft[] = [];
  function Host() {
    const [draft, setDraft] = useState(initial);
    seen.push(draft);
    return (
      <StudioStagesCard
        workflowDraft={draft}
        setWorkflowDraft={setDraft}
        isWorkflowReadOnly={readOnly}
        agentOptions={AGENTS.map((a: { slug: string; name: string }) => ({
          id: a.slug,
          label: a.name,
        }))}
        agents={AGENTS}
        skills={["plan", "verify"]}
        exitActionRequirements={EXIT_ACTION_REQUIREMENTS}
        runtimeOptions={undefined}
        skipConditions={["has_description"]}
        selectedWorkflow={null}
      />
    );
  }
  const rendered = render(<Host />);
  return { ...rendered, latest: () => seen[seen.length - 1] as StudioWorkflowDraft & { stages: ExitActionStage[] } };
}

describe("Studio exit-action authoring (AC-1/AC-2 UI)", () => {
  it("drops gate_required and defaults a new stage to exit_actions_enabled=false with no actions", () => {
    const stage = emptyStage(1) as ExitActionStage;
    expect(stage).not.toHaveProperty("gate_required");
    expect(stage.exit_actions_enabled).toBe(false);
    expect(stage.exit_actions).toEqual([]);
  });

  it("renames the empty-agent option to No agent runtime and does not claim human ownership", () => {
    renderCard(draftWith(emptyStage(1)));

    expect(screen.getByRole("option", { name: /No agent runtime/i })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /None \(human approval\)/i })).toBeNull();
  });

  it("offers Resolve exit actions before leaving this stage instead of Require gate approval", () => {
    renderCard(draftWith(emptyStage(1)));

    expect(
      screen.getByText(/Resolve exit actions before leaving this stage/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Require gate approval/i)).toBeNull();
  });

  it("authors a typed exit action through the list when exit actions are enabled", async () => {
    const user = userEvent.setup();
    const { latest } = renderCard(draftWith(emptyStage(1)));

    await user.click(
      screen.getByRole("checkbox", { name: /Resolve exit actions before leaving this stage/i }),
    );
    expect(latest().stages[0].exit_actions_enabled).toBe(true);

    await user.click(screen.getByRole("button", { name: /Add exit action/i }));

    const row = screen.getByRole("group", { name: /Exit action 1/i });
    const label = within(row).getByLabelText(/Action label/i);
    await user.clear(label);
    await user.type(label, "Read provider usage");

    const kind = within(row).getByLabelText(/Requirement/i);
    await user.selectOptions(kind, "credential");

    const credential = within(row).getByLabelText(/Credential/i);
    await user.selectOptions(credential, "claude_profile");

    const authored = latest().stages[0].exit_actions ?? [];
    expect(authored).toHaveLength(1);
    expect(authored[0]).toMatchObject({
      label: "Read provider usage",
      requirement: { kind: "credential", credential_key: "claude_profile" },
    });
    expect(authored[0].key).toMatch(/^[a-z0-9]+(?:-[a-z0-9]+)*$/);
  });

  it("derives selectable identifiers from the server-owned catalog", async () => {
    const user = userEvent.setup();
    const customCatalog = {
      ...EXIT_ACTION_REQUIREMENTS,
      capability_ids: ["custom_runtime_probe"],
      credential_keys: ["custom_usage_profile"],
      authority_scopes: ["custom:grant"],
    } as const;
    const seen: StudioWorkflowDraft[] = [];

    function Host() {
      const [draft, setDraft] = useState(draftWith(emptyStage(1)));
      seen.push(draft);
      return (
        <StudioStagesCard
          workflowDraft={draft}
          setWorkflowDraft={setDraft}
          isWorkflowReadOnly={false}
          agentOptions={AGENTS.map((a: { slug: string; name: string }) => ({
            id: a.slug,
            label: a.name,
          }))}
          agents={AGENTS}
          skills={["plan", "verify"]}
          exitActionRequirements={customCatalog}
          runtimeOptions={undefined}
          skipConditions={["has_description"]}
          selectedWorkflow={null}
        />
      );
    }

    render(<Host />);
    await user.click(
      screen.getByRole("checkbox", { name: /Resolve exit actions before leaving this stage/i }),
    );
    await user.click(screen.getByRole("button", { name: /Add exit action/i }));

    const row = screen.getByRole("group", { name: /Exit action 1/i });
    await user.selectOptions(within(row).getByLabelText(/Requirement/i), "credential");

    expect(within(row).queryByRole("option", { name: "claude_profile" })).toBeNull();
    expect(within(row).getByRole("option", { name: "custom_usage_profile" })).toBeInTheDocument();
    expect(seen.at(-1)?.stages[0].exit_actions?.[0]?.requirement).toEqual({
      kind: "credential",
      credential_key: "custom_usage_profile",
    });
  });

  it.each([
    {
      name: "runtime capability",
      kind: "runtime_capability",
      identifierLabel: /Capability/i,
      identifierValue: "http_test_client",
      expected: { kind: "runtime_capability", capability_id: "http_test_client" },
    },
    {
      name: "authority",
      kind: "authority",
      identifierLabel: /Authority/i,
      identifierValue: "release:publish",
      expected: { kind: "authority", authority_scope: "release:publish" },
    },
    {
      name: "operator judgment",
      kind: "operator_judgment",
      identifierLabel: /Decision prompt/i,
      identifierValue: "Ship despite residual risk?",
      expected: {
        kind: "operator_judgment",
        decision_prompt: "Ship despite residual risk?",
      },
      freeText: true,
    },
  ])("authors a $name requirement without inventing identifiers from prose", async ({
    kind,
    identifierLabel,
    identifierValue,
    expected,
    freeText,
  }) => {
    const user = userEvent.setup();
    const { latest } = renderCard(draftWith(emptyStage(1)));

    await user.click(
      screen.getByRole("checkbox", { name: /Resolve exit actions before leaving this stage/i }),
    );
    await user.click(screen.getByRole("button", { name: /Add exit action/i }));

    const row = screen.getByRole("group", { name: /Exit action 1/i });
    await user.clear(within(row).getByLabelText(/Action label/i));
    await user.type(within(row).getByLabelText(/Action label/i), `Action for ${kind}`);
    await user.selectOptions(within(row).getByLabelText(/Requirement/i), kind);

    if (freeText) {
      await user.clear(within(row).getByLabelText(identifierLabel));
      await user.type(within(row).getByLabelText(identifierLabel), identifierValue);
    } else {
      await user.selectOptions(within(row).getByLabelText(identifierLabel), identifierValue);
    }

    expect(latest().stages[0].exit_actions?.[0]?.requirement).toEqual(expected);
  });

  it("clears authored actions when exit actions are disabled again (AC-2 UI)", async () => {
    const user = userEvent.setup();
    const { latest } = renderCard(draftWith(emptyStage(1)));

    const toggle = screen.getByRole("checkbox", {
      name: /Resolve exit actions before leaving this stage/i,
    });
    await user.click(toggle);
    await user.click(screen.getByRole("button", { name: /Add exit action/i }));
    expect((latest().stages[0].exit_actions ?? []).length).toBeGreaterThan(0);

    await user.click(toggle);
    expect(latest().stages[0].exit_actions_enabled).toBe(false);
    expect(latest().stages[0].exit_actions).toEqual([]);
    expect(screen.queryByRole("button", { name: /Add exit action/i })).toBeNull();
  });

  it("shows Evaluated at run time rather than claiming the action will or will not gate", async () => {
    const user = userEvent.setup();
    renderCard(draftWith(emptyStage(1)));

    await user.click(
      screen.getByRole("checkbox", { name: /Resolve exit actions before leaving this stage/i }),
    );
    await user.click(screen.getByRole("button", { name: /Add exit action/i }));

    expect(screen.getByText(/Evaluated at run time/i)).toBeInTheDocument();
    expect(screen.queryByText(/will always pause/i)).toBeNull();
    expect(screen.queryByText(/will always gate/i)).toBeNull();
  });

  it("keeps exit-action rows readable when the workflow is read-only", () => {
    const stage = {
      ...emptyStage(1),
      exit_actions_enabled: true,
      exit_actions: [
        {
          key: "accept-risk",
          label: "Accept launch risk",
          requirement: {
            kind: "operator_judgment",
            decision_prompt: "Ship despite residual risk?",
          },
        },
      ],
    } as ExitActionStage;

    renderCard(draftWith(stage as StudioWorkflowStage), true);

    expect(screen.getByText("Accept launch risk")).toBeInTheDocument();
    expect(screen.getByText(/operator judgment/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add exit action/i })).toBeNull();
  });

  it("gives each exit-action remove control a stable accessible name", async () => {
    const user = userEvent.setup();
    renderCard(draftWith(emptyStage(1)));

    await user.click(
      screen.getByRole("checkbox", { name: /Resolve exit actions before leaving this stage/i }),
    );
    await user.click(screen.getByRole("button", { name: /Add exit action/i }));
    await user.click(screen.getByRole("button", { name: /Add exit action/i }));

    expect(screen.getByRole("button", { name: /Remove exit action 1/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Remove exit action 2/i })).toBeInTheDocument();
  });
});
