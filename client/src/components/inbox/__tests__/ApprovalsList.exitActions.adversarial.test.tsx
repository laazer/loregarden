/**
 * ADVERSARIAL / AC-10 consumer seam: the top-bar inbox ApprovalsList must wire
 * Re-check through to resolveApproval with action=recheck. Hard-coding
 * onApprove → "approve" is how a recheck-only credential gate gets illegally
 * approved from the inbox while ApprovalsView is fixed.
 */

import { QueryClient } from "@tanstack/react-query";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import { api, type Approval } from "../../../api/client";
import { renderWithRouter } from "../../../test/renderWithRouter";
import { ApprovalsList } from "../ApprovalsList";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function recheckGate(): Approval {
  return {
    id: "appr_inbox_recheck",
    title: "Resolve Verify exit actions",
    level: "medium",
    workspace_slug: "loregarden",
    stage_key: "verify",
    stage_name: "Verify",
    impact: "Credential missing.",
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
  };
}

describe("ApprovalsList exit-action recheck wiring", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockApi.approvals.mockResolvedValue([recheckGate()]);
    mockApi.resolveApproval.mockResolvedValue({});
  });

  it("posts action=recheck when the operator clicks Re-check on a recheck-only gate", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithRouter(
      <ApprovalsList workspaceSlug="loregarden" isActive />,
      { queryClient: client },
    );

    const recheck = await screen.findByRole("button", { name: /^Re-check$/i });
    fireEvent.click(recheck);

    await waitFor(() => {
      expect(mockApi.resolveApproval).toHaveBeenCalledWith(
        "appr_inbox_recheck",
        expect.objectContaining({ action: "recheck" }),
      );
    });
    expect(mockApi.resolveApproval).not.toHaveBeenCalledWith(
      "appr_inbox_recheck",
      expect.objectContaining({ action: "approve" }),
    );
  });
});
