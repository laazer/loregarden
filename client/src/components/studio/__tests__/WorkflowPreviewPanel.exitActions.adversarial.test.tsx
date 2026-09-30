/**
 * ADVERSARIAL / AC-11 + Studio plan: pipeline preview must not advertise
 * static "gate · human approval" from gate_required. Exit actions are
 * evaluated at run time; absence of an agent is not itself a human decision.
 */

import { render, screen } from "@testing-library/react";

import type { StudioWorkflowStage } from "../../../api/client";
import { WorkflowPreviewPanel } from "../WorkflowPreviewPanel";
import { emptyStage } from "../studioWorkflowHelpers";

function stage(overrides: Partial<StudioWorkflowStage> = {}): StudioWorkflowStage {
  return { ...emptyStage(1), ...overrides };
}

describe("WorkflowPreviewPanel exit-action preview (AC-3/AC-11)", () => {
  it("does not render the retired gate · human approval badge from gate_required", () => {
    // Legacy field on a stale payload; the type no longer has it, so it arrives
    // as an extra property — which the preview must ignore.
    const legacy = {
      ...stage({ name: "Verify", exit_actions_enabled: false, exit_actions: [] }),
      gate_required: true,
    };
    render(
      <WorkflowPreviewPanel
        name="Exit actions"
        slug="exit-actions"
        stages={[legacy]}
        agentLabel={(id) => id || "No agent runtime"}
      />,
    );

    expect(screen.queryByText(/gate\s*·\s*human approval/i)).not.toBeInTheDocument();
  });

  it("shows an evaluated-at-run-time exit-actions cue when exit_actions_enabled", () => {
    render(
      <WorkflowPreviewPanel
        name="Exit actions"
        slug="exit-actions"
        stages={[
          stage({
            name: "Verify",
            exit_actions_enabled: true,
            exit_actions: [
              {
                key: "accept-risk",
                label: "Accept launch risk",
                requirement: {
                  kind: "operator_judgment",
                  decision_prompt: "Ship?",
                },
              },
            ],
          } as StudioWorkflowStage),
        ]}
        agentLabel={(id) => id || "No agent runtime"}
      />,
    );

    expect(screen.getByText(/exit actions/i)).toBeInTheDocument();
    expect(screen.getByText(/evaluated at run time/i)).toBeInTheDocument();
    expect(screen.queryByText(/gate\s*·\s*human approval/i)).not.toBeInTheDocument();
  });

  it("labels an empty agent_id as No agent runtime rather than human approval", () => {
    render(
      <WorkflowPreviewPanel
        name="Agentless"
        slug="agentless"
        stages={[stage({ agent_id: "", skill_name: "", name: "Review" })]}
        agentLabel={(id) => (id ? id : "No agent runtime")}
      />,
    );

    expect(screen.getByText(/No agent runtime/i)).toBeInTheDocument();
    expect(screen.queryByText(/human approval/i)).not.toBeInTheDocument();
  });
});
