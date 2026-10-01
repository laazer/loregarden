/**
 * AC-10 consumer seam: every ApprovalCard host that resolves a workflow gate
 * must be able to send action=recheck when allowed_actions says so. Hard-coding
 * onApprove → "approve" is how a recheck-only gate gets illegally approved.
 */

import { QueryClient } from "@tanstack/react-query";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import { api, type Approval, type TicketDetail } from "../../../api/client";
import { renderWithRouter } from "../../../test/renderWithRouter";
import { ApprovalsView } from "../ApprovalsView";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

type ExitActionApproval = Approval & {
  human_required_actions: Array<{
    action_key: string;
    action_label: string;
    requirement: { kind: "credential"; credential_key: string };
    reason_code: string;
    reason: string;
    resolution_mode: "approve" | "recheck";
  }>;
  allowed_actions: Array<"approve" | "recheck" | "reject">;
};

function recheckGate(): ExitActionApproval {
  return {
    id: "appr_recheck",
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

const TICKET = {
  id: "ticket_1",
  external_id: "lg-bug-hole-574",
  title: "Exit actions",
  acceptance_criteria: [],
} as unknown as TicketDetail;

describe("ApprovalsView exit-action recheck wiring", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockApi.approvals.mockResolvedValue([recheckGate() as Approval]);
    mockApi.resolveApproval.mockResolvedValue({});
  });

  it("posts action=recheck when the operator clicks Re-check on a recheck-only gate", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithRouter(<ApprovalsView ticket={TICKET} />, { queryClient: client });

    const recheck = await screen.findByRole("button", { name: /^Re-check$/i });
    fireEvent.click(recheck);

    await waitFor(() => {
      expect(mockApi.resolveApproval).toHaveBeenCalledWith(
        "appr_recheck",
        expect.objectContaining({ action: "recheck" }),
      );
    });
    expect(mockApi.resolveApproval).not.toHaveBeenCalledWith(
      "appr_recheck",
      expect.objectContaining({ action: "approve" }),
    );
  });

  it("never posts approve when allowed_actions is recheck-only even if Approve were clicked", async () => {
    // Mutation-style guard: ApprovalsView must not collapse recheck into approve
    // at the host boundary (onResolve typed as approve|reject is the failure mode).
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithRouter(<ApprovalsView ticket={TICKET} />, { queryClient: client });

    await screen.findByText(/Resolve Verify exit actions/i);
    const approve = screen.queryByRole("button", { name: /^Approve$/i });
    if (approve) {
      fireEvent.click(approve);
      await waitFor(() => {
        expect(mockApi.resolveApproval).toHaveBeenCalled();
      });
      expect(mockApi.resolveApproval).not.toHaveBeenCalledWith(
        "appr_recheck",
        expect.objectContaining({ action: "approve" }),
      );
    } else {
      expect(approve).toBeNull();
    }
  });
});
