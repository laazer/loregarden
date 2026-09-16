/**
 * Client API contract for exit-action approvals (AC-10).
 *
 * Pins the shared Approval shape and resolveApproval action vocabulary against
 * the server ApprovalView / ApprovalAction enums. The retired
 * `approve_or_reject` resolution_mode must not reappear.
 */

import type { Approval } from "../client";

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
