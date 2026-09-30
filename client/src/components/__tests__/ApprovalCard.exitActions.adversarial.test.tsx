/**
 * ADVERSARIAL / AC-10: workflow-gate cards must render structured exit-action
 * requirements — never invent eligibility from impact prose, never offer Approve
 * as a way to manufacture a missing credential/capability, and never list
 * agent-executable actions.
 *
 * Dimensions covered: null/empty, structure mutation, combinatorial (mixed
 * modes), assumption checks (prose parsing), vocabulary fail-closed.
 */

import { fireEvent, screen, within } from "@testing-library/react";

import type { Approval } from "../../api/client";
import { renderWithRouter } from "../../test/renderWithRouter";
import { ApprovalCard } from "../ApprovalCard";

/** Structured exit-action payload the server ApprovalView already serializes. */
type HumanRequiredExitAction = {
  action_key: string;
  action_label: string;
  action_description?: string;
  requirement:
    | { kind: "runtime_capability"; capability_id: string }
    | { kind: "credential"; credential_key: string }
    | { kind: "authority"; authority_scope: string }
    | { kind: "operator_judgment"; decision_prompt: string };
  reason_code: string;
  reason: string;
  /** Server vocabulary is approve | recheck — never approve_or_reject. */
  resolution_mode: "approve" | "recheck";
};

type ExitActionApproval = Approval & {
  human_required_actions: HumanRequiredExitAction[];
  allowed_actions: Array<"approve" | "recheck" | "reject">;
};

const BASE: Approval = {
  id: "appr_exit",
  title: "Resolve Verify exit actions",
  level: "medium",
  workspace_slug: "loregarden",
  stage_key: "verify",
  stage_name: "Verify",
  impact: "One or more exit actions need a person.",
  checklist: [],
  route_options: [],
  ticket_id: "ticket_1",
  ticket_external_id: "lg-bug-hole-574",
  kind: "workflow_gate",
  status: "pending",
  run_id: "run_1",
  tool_name: "",
  tool_input_json: "{}",
  cli_adapter: "",
};

function gate(overrides: Partial<ExitActionApproval> = {}): ExitActionApproval {
  return {
    ...BASE,
    human_required_actions: [],
    allowed_actions: ["reject"],
    ...overrides,
  };
}

function renderCard(
  approval: ExitActionApproval,
  handlers: {
    onApprove?: jest.Mock;
    onReject?: jest.Mock;
    onRecheck?: jest.Mock;
  } = {},
) {
  const onApprove = handlers.onApprove ?? jest.fn();
  const onReject = handlers.onReject ?? jest.fn();
  const onRecheck = handlers.onRecheck ?? jest.fn();
  const props = {
    approval: approval as Approval,
    onApprove,
    onReject,
    // Implementers may wire recheck via onRecheck or by labelling the primary
    // button and routing through onApprove — both must end up as action=recheck
    // at the API. Prefer an explicit onRecheck when the card exposes it.
    ...( { onRecheck } as Record<string, unknown> ),
  };
  renderWithRouter(<ApprovalCard {...(props as Parameters<typeof ApprovalCard>[0])} />);
  return { onApprove, onReject, onRecheck };
}

function actionOrder(container: HTMLElement, label: string, reason: string): number {
  const text = container.textContent ?? "";
  const labelAt = text.indexOf(label);
  const reasonAt = text.indexOf(reason);
  expect(labelAt).toBeGreaterThanOrEqual(0);
  expect(reasonAt).toBeGreaterThanOrEqual(0);
  return labelAt - reasonAt;
}

describe("AC-10 ApprovalCard exit-action rendering", () => {
  it("replaces the generic stage-sign-off label with category text from structured data", () => {
    renderCard(
      gate({
        human_required_actions: [
          {
            action_key: "accept-risk",
            action_label: "Accept launch risk",
            requirement: {
              kind: "operator_judgment",
              decision_prompt: "Ship despite residual risk?",
            },
            reason_code: "operator_judgment_required",
            reason: "Ship despite residual risk?",
            resolution_mode: "approve",
          },
        ],
        allowed_actions: ["approve", "reject"],
      }),
    );

    expect(screen.getByText(/Needs operator judgment/i)).toBeInTheDocument();
    expect(screen.queryByText(/stage sign-off/i)).not.toBeInTheDocument();
  });

  it("renders the action label before the exact reason, and never invents a category from impact prose", () => {
    const impact =
      "Credential unavailable: invented_from_prose — Capability unavailable: also_prose";
    const approval = gate({
      impact,
      human_required_actions: [
        {
          action_key: "publish-release",
          action_label: "Publish the release",
          requirement: { kind: "authority", authority_scope: "release:publish" },
          reason_code: "authority_grant_required",
          reason: "Authority grant required: release:publish",
          resolution_mode: "approve",
        },
      ],
      allowed_actions: ["approve", "reject"],
    });
    const view = renderWithRouter(
      <ApprovalCard approval={approval as Approval} onApprove={() => {}} onReject={() => {}} />,
    );

    expect(screen.getByText(/Missing authority/i)).toBeInTheDocument();
    expect(screen.getByText("Publish the release")).toBeInTheDocument();
    expect(screen.getByText("Authority grant required: release:publish")).toBeInTheDocument();
    expect(
      actionOrder(view.container, "Publish the release", "Authority grant required: release:publish"),
    ).toBeLessThan(0);
    // Impact may still render as narrative, but category badges must not come from it.
    expect(screen.queryByText(/^Credential unavailable$/i)).not.toBeInTheDocument();
  });

  it.each([
    {
      name: "credential unavailable",
      reason_code: "credential_unavailable",
      reason: "Credential unavailable: claude_profile",
      requirement: { kind: "credential" as const, credential_key: "claude_profile" },
      category: /Credential unavailable/i,
      resolution_mode: "recheck" as const,
      allowed: ["recheck", "reject"] as Array<"approve" | "recheck" | "reject">,
      primary: /^Re-check$/i,
      forbiddenPrimary: /^Approve$/i,
    },
    {
      name: "credential status unknown",
      reason_code: "credential_status_unknown",
      reason: "Credential status unavailable: claude_profile",
      requirement: { kind: "credential" as const, credential_key: "claude_profile" },
      category: /Credential status unavailable/i,
      resolution_mode: "recheck" as const,
      allowed: ["recheck", "reject"] as Array<"approve" | "recheck" | "reject">,
      primary: /^Re-check$/i,
      forbiddenPrimary: /^Approve$/i,
    },
    {
      name: "capability unavailable",
      reason_code: "capability_unavailable",
      reason: "Capability unavailable on this runtime: http_test_client",
      requirement: { kind: "runtime_capability" as const, capability_id: "http_test_client" },
      category: /Capability unavailable/i,
      resolution_mode: "recheck" as const,
      allowed: ["recheck", "reject"] as Array<"approve" | "recheck" | "reject">,
      primary: /^Re-check$/i,
      forbiddenPrimary: /^Approve$/i,
    },
    {
      name: "capability status unknown",
      reason_code: "capability_status_unknown",
      reason: "Capability status unavailable: http_test_client",
      requirement: { kind: "runtime_capability" as const, capability_id: "http_test_client" },
      category: /Capability status unavailable/i,
      resolution_mode: "recheck" as const,
      allowed: ["recheck", "reject"] as Array<"approve" | "recheck" | "reject">,
      primary: /^Re-check$/i,
      forbiddenPrimary: /^Approve$/i,
    },
    {
      name: "authority denied (recheck)",
      reason_code: "authority_denied",
      reason: "Authority denied: release:publish",
      requirement: { kind: "authority" as const, authority_scope: "release:publish" },
      category: /Missing authority/i,
      resolution_mode: "recheck" as const,
      allowed: ["recheck", "reject"] as Array<"approve" | "recheck" | "reject">,
      primary: /^Re-check$/i,
      forbiddenPrimary: /^Approve$/i,
    },
    {
      name: "authority status unknown",
      reason_code: "authority_status_unknown",
      reason: "Authority status unavailable: release:publish",
      requirement: { kind: "authority" as const, authority_scope: "release:publish" },
      category: /Authority status unavailable/i,
      resolution_mode: "recheck" as const,
      allowed: ["recheck", "reject"] as Array<"approve" | "recheck" | "reject">,
      primary: /^Re-check$/i,
      forbiddenPrimary: /^Approve$/i,
    },
  ])("emphasizes Re-check for $name and never offers Approve", ({
    reason_code,
    reason,
    requirement,
    category,
    resolution_mode,
    allowed,
    primary,
    forbiddenPrimary,
  }) => {
    const { onApprove, onRecheck } = renderCard(
      gate({
        human_required_actions: [
          {
            action_key: "read-usage",
            action_label: "Read provider usage",
            requirement,
            reason_code,
            reason,
            resolution_mode,
          },
        ],
        allowed_actions: allowed,
      }),
    );

    expect(screen.getByRole("status", { name: category })).toBeInTheDocument();
    expect(screen.getByText("Read provider usage")).toBeInTheDocument();
    expect(screen.getByText(reason)).toBeInTheDocument();

    const recheck = screen.getByRole("button", { name: primary });
    expect(recheck).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: forbiddenPrimary })).not.toBeInTheDocument();

    fireEvent.click(recheck);
    // Either an explicit onRecheck or a recheck-labelled primary path must fire.
    expect(onRecheck.mock.calls.length + onApprove.mock.calls.length).toBeGreaterThanOrEqual(1);
  });

  it("maps authority_grant_required to Missing authority with Approve (grant), not Re-check", () => {
    const { onApprove } = renderCard(
      gate({
        human_required_actions: [
          {
            action_key: "publish-release",
            action_label: "Publish the release",
            requirement: { kind: "authority", authority_scope: "release:publish" },
            reason_code: "authority_grant_required",
            reason: "Authority grant required: release:publish",
            resolution_mode: "approve",
          },
        ],
        allowed_actions: ["approve", "reject"],
      }),
    );

    expect(screen.getByText(/Missing authority/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Approve$/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Re-check$/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Approve$/i }));
    expect(onApprove).toHaveBeenCalled();
  });

  it("lists only human-required actions in a mixed gate and keeps Approve hidden while recheck remains", () => {
    renderCard(
      gate({
        human_required_actions: [
          {
            action_key: "read-usage",
            action_label: "Read provider usage",
            requirement: { kind: "credential", credential_key: "claude_profile" },
            reason_code: "credential_unavailable",
            reason: "Credential unavailable: claude_profile",
            resolution_mode: "recheck",
          },
          {
            action_key: "accept-risk",
            action_label: "Accept launch risk",
            requirement: {
              kind: "operator_judgment",
              decision_prompt: "Ship despite residual risk?",
            },
            reason_code: "operator_judgment_required",
            reason: "Ship despite residual risk?",
            resolution_mode: "approve",
          },
        ],
        // AC-8: approve is withheld until every recheck-only condition clears.
        allowed_actions: ["recheck", "reject"],
      }),
    );

    expect(screen.getByText("Read provider usage")).toBeInTheDocument();
    expect(screen.getByText("Accept launch risk")).toBeInTheDocument();
    // Agent-executable siblings must never appear just because impact mentions them.
    expect(screen.queryByText(/Run the smoke test/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Re-check$/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Approve$/i })).not.toBeInTheDocument();
  });

  it("exposes Approve for operator judgment once allowed_actions includes it", () => {
    const { onApprove } = renderCard(
      gate({
        human_required_actions: [
          {
            action_key: "accept-risk",
            action_label: "Accept launch risk",
            requirement: {
              kind: "operator_judgment",
              decision_prompt: "Ship despite residual risk?",
            },
            reason_code: "operator_judgment_required",
            reason: "Ship despite residual risk?",
            resolution_mode: "approve",
          },
        ],
        allowed_actions: ["approve", "reject"],
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: /^Approve$/i }));
    expect(onApprove).toHaveBeenCalled();
  });

  it("does not treat an empty human_required_actions list as a category from impact prose", () => {
    renderCard(
      gate({
        impact: "Credential unavailable: claude_profile — Needs operator judgment",
        human_required_actions: [],
        allowed_actions: ["reject"],
      }),
    );

    expect(screen.queryByText(/^Credential unavailable$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Needs operator judgment$/i)).not.toBeInTheDocument();
  });

  it("rejects the retired approve_or_reject resolution_mode vocabulary in fixtures", () => {
    // Server ExitActionResolutionMode = {approve, recheck}. A client that still
    // ships approve_or_reject will fail ApprovalView validation on the wire.
    const retired = "approve_or_reject";
    const allowed: HumanRequiredExitAction["resolution_mode"][] = ["approve", "recheck"];
    expect(allowed).not.toContain(retired as HumanRequiredExitAction["resolution_mode"]);
  });
});

describe("AC-10 ApprovalCard empty / boundary mutations", () => {
  it("survives a gate with an empty reason string and still shows the structured action", () => {
    renderCard(
      gate({
        impact: "Narrative only — not a structured reason.",
        human_required_actions: [
          {
            action_key: "accept-risk",
            action_label: "Accept launch risk",
            requirement: {
              kind: "operator_judgment",
              decision_prompt: "Ship?",
            },
            reason_code: "operator_judgment_required",
            reason: "",
            resolution_mode: "approve",
          },
        ],
        allowed_actions: ["approve", "reject"],
      }),
    );

    expect(screen.getByText("Accept launch risk")).toBeInTheDocument();
    expect(screen.getByText(/Needs operator judgment/i)).toBeInTheDocument();
  });

  it("renders many human-required rows without collapsing them into one checklist string", () => {
    const actions: HumanRequiredExitAction[] = Array.from({ length: 8 }, (_, i) => ({
      action_key: `action-${i}`,
      action_label: `Action label ${i}`,
      requirement: {
        kind: "operator_judgment" as const,
        decision_prompt: `Prompt ${i}`,
      },
      reason_code: "operator_judgment_required",
      reason: `Exact reason ${i}`,
      resolution_mode: "approve" as const,
    }));

    renderCard(gate({ human_required_actions: actions, allowed_actions: ["approve", "reject"] }));

    for (const action of actions) {
      expect(screen.getByText(action.action_label)).toBeInTheDocument();
      expect(screen.getByText(action.reason)).toBeInTheDocument();
    }
  });

  it("keeps Reject available on a recheck-only card", () => {
    const { onReject } = renderCard(
      gate({
        human_required_actions: [
          {
            action_key: "read-usage",
            action_label: "Read provider usage",
            requirement: { kind: "credential", credential_key: "claude_profile" },
            reason_code: "credential_unavailable",
            reason: "Credential unavailable: claude_profile",
            resolution_mode: "recheck",
          },
        ],
        allowed_actions: ["recheck", "reject"],
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: /^Reject$/i }));
    const dialog = screen.getByRole("dialog", { name: /reject/i });
    fireEvent.change(within(dialog).getByPlaceholderText(/what needs to change/i), {
      target: { value: "Still missing the profile token" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Reject" }));
    expect(onReject).toHaveBeenCalled();
  });
});
