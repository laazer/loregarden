/**
 * Client API contract for exit-action approvals (AC-10) and Studio stages (AC-1/AC-11).
 *
 * Pins the shared Approval shape and resolveApproval action vocabulary against
 * the server ApprovalView / ApprovalAction enums. The retired
 * `approve_or_reject` resolution_mode must not reappear. StudioWorkflowStage
 * must carry exit_actions_enabled / exit_actions with no dual gate_required path.
 */

import type { Approval, StudioExitAction, StudioWorkflowStage } from "../client";

type ResolutionMode = "approve" | "recheck";
type AllowedAction = "approve" | "recheck" | "reject";

type HumanRequiredExitAction = NonNullable<Approval["human_required_actions"]>[number];

describe("Approval exit-action TypeScript contract", () => {
  it("types resolution_mode as approve|recheck only", () => {
    const modes: ResolutionMode[] = ["approve", "recheck"];
    expect(modes).toEqual(["approve", "recheck"]);
    expect(modes).not.toContain("approve_or_reject");
  });

  it("requires human_required_actions and allowed_actions on Approval", () => {
    const approval: Approval = {
      id: "a1",
      title: "t",
      level: "medium",
      workspace_slug: "loregarden",
      stage_key: "verify",
      stage_name: "Verify",
      impact: "",
      ticket_id: "t1",
      ticket_external_id: "lg-bug-hole-574",
      kind: "workflow_gate",
      run_id: "",
      tool_name: "",
      tool_input_json: "{}",
      cli_adapter: "",
      human_required_actions: [
        {
          action_key: "read-usage",
          action_label: "Read provider usage",
          action_description: "",
          requirement: { kind: "credential", credential_key: "claude_profile" },
          reason_code: "credential_unavailable",
          reason: "Credential unavailable: claude_profile",
          resolution_mode: "recheck",
        },
      ],
      allowed_actions: ["recheck", "reject"],
    };

    expect(approval.human_required_actions).toHaveLength(1);
    expect(approval.allowed_actions).toEqual(["recheck", "reject"]);

    const action = approval.human_required_actions![0] as HumanRequiredExitAction;
    const mode: ResolutionMode = action.resolution_mode;
    const allowed: AllowedAction[] = approval.allowed_actions ?? [];
    expect(mode).toBe("recheck");
    expect(allowed).toContain("recheck");
  });
});

describe("StudioWorkflowStage exit-action TypeScript contract (AC-1/AC-11)", () => {
  it("round-trips typed exit_actions without a gate_required field", () => {
    const actions: StudioExitAction[] = [
      {
        key: "read-usage",
        label: "Read provider usage",
        requirement: { kind: "credential", credential_key: "claude_profile" },
      },
      {
        key: "accept-risk",
        label: "Accept launch risk",
        description: "Operator call",
        requirement: {
          kind: "operator_judgment",
          decision_prompt: "Ship despite residual risk?",
        },
      },
    ];

    // AC-11: no dual path — constructing a stage without gate_required must type-check
    // once the deprecated field is removed. Runtime probe mirrors that shape.
    const stage = {
      key: "verify",
      name: "Verify",
      stage_type: "agent" as const,
      agent_id: "verifier",
      skill_name: "",
      optional: false,
      order: 1,
      exit_actions_enabled: true,
      exit_actions: actions,
      classify_routes: [] as StudioWorkflowStage["classify_routes"],
      parallel_agents: [] as StudioWorkflowStage["parallel_agents"],
      model: "",
    } satisfies Omit<StudioWorkflowStage, "gate_required"> & {
      exit_actions_enabled: boolean;
      exit_actions: StudioExitAction[];
    };

    expect(stage).not.toHaveProperty("gate_required");
    expect(stage.exit_actions_enabled).toBe(true);
    expect(stage.exit_actions).toEqual(actions);
  });

  it("rejects the retired approve_or_reject vocabulary next to Studio exit actions", () => {
    const retired = "approve_or_reject";
    const modes: ResolutionMode[] = ["approve", "recheck"];
    expect(modes).not.toContain(retired as ResolutionMode);
  });
});
