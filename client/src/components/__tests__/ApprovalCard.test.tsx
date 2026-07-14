import { fireEvent, render, screen, within } from "@testing-library/react";

import type { Approval } from "../../api/client";
import { ApprovalCard } from "../ApprovalCard";

const GATE_APPROVAL: Approval = {
  id: "appr_1",
  title: "Approve Dash movement and cooldown",
  level: "medium",
  workspace_slug: "blobert-tdd",
  stage_key: "playtest",
  stage_name: "Playtest",
  impact: "Stage 'Playtest' requires human sign-off before completion.",
  checklist: [],
  route_options: [{ key: "implementation", name: "Implementation" }],
  ticket_id: "ticket_1",
  ticket_external_id: "01-blobert-dash",
  kind: "workflow_gate",
  status: "pending",
  run_id: "",
  tool_name: "",
  tool_input_json: "{}",
  cli_adapter: "",
};

const PERMISSION_APPROVAL: Approval = {
  ...GATE_APPROVAL,
  id: "appr_2",
  kind: "cli_permission",
  tool_name: "Bash",
};

describe("ApprovalCard reject flow", () => {
  it("opens the reject modal for a workflow-gate approval instead of rejecting immediately", () => {
    const onReject = jest.fn();
    render(
      <ApprovalCard
        approval={GATE_APPROVAL}
        onApprove={() => {}}
        onReject={onReject}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(onReject).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog", { name: /reject sign-off/i });

    fireEvent.change(within(dialog).getByPlaceholderText(/what needs to change/i), {
      target: { value: "Landing still clips through the platform" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Reject" }));

    expect(onReject).toHaveBeenCalledWith({
      response: "Landing still clips through the platform",
      route_to_stage_key: undefined,
    });
    expect(screen.queryByRole("dialog", { name: /reject sign-off/i })).not.toBeInTheDocument();
  });

  it("denies a CLI permission approval immediately, without a modal", () => {
    const onReject = jest.fn();
    render(
      <ApprovalCard
        approval={PERMISSION_APPROVAL}
        onApprove={() => {}}
        onReject={onReject}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    expect(onReject).toHaveBeenCalledWith();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
