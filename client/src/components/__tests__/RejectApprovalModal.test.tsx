import { fireEvent, render, screen } from "@testing-library/react";

import type { Approval } from "../../api/client";
import { RejectApprovalModal } from "../RejectApprovalModal";

const GATE_APPROVAL: Approval = {
  id: "appr_1",
  title: "Approve Dash movement and cooldown",
  level: "medium",
  workspace_slug: "blobert-tdd",
  stage_key: "playtest",
  stage_name: "Playtest",
  impact: "Stage 'Playtest' requires human sign-off before completion.",
  checklist: [],
  route_options: [
    { key: "implementation", name: "Implementation" },
    { key: "test_design", name: "Test Design" },
  ],
  ticket_id: "ticket_1",
  ticket_external_id: "01-blobert-dash",
  kind: "workflow_gate",
  status: "pending",
  run_id: "",
  tool_name: "",
  tool_input_json: "{}",
  cli_adapter: "",
};

describe("RejectApprovalModal", () => {
  it("renders nothing when closed or without an approval", () => {
    const { rerender } = render(
      <RejectApprovalModal
        open={false}
        approval={GATE_APPROVAL}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    rerender(
      <RejectApprovalModal open onClose={() => {}} onConfirm={() => {}} approval={null} />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("disables Reject until a reason is entered, then submits reason and route", () => {
    const onConfirm = jest.fn();
    render(
      <RejectApprovalModal
        open
        approval={GATE_APPROVAL}
        onClose={() => {}}
        onConfirm={onConfirm}
      />,
    );

    const rejectButton = screen.getByRole("button", { name: "Reject" });
    expect(rejectButton).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText(/what needs to change/i), {
      target: { value: "Movement felt broken in the final area" },
    });
    expect(rejectButton).toBeEnabled();

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "test_design" } });
    fireEvent.click(rejectButton);

    expect(onConfirm).toHaveBeenCalledWith({
      response: "Movement felt broken in the final area",
      route_to_stage_key: "test_design",
    });
  });

  it("submits without a route override when left on the default option", () => {
    const onConfirm = jest.fn();
    render(
      <RejectApprovalModal
        open
        approval={GATE_APPROVAL}
        onClose={() => {}}
        onConfirm={onConfirm}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText(/what needs to change/i), {
      target: { value: "Needs another pass" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    expect(onConfirm).toHaveBeenCalledWith({
      response: "Needs another pass",
      route_to_stage_key: undefined,
    });
  });

  it("hides the route picker when the approval has no route options", () => {
    render(
      <RejectApprovalModal
        open
        approval={{ ...GATE_APPROVAL, route_options: [] }}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("cancel closes without submitting", () => {
    const onClose = jest.fn();
    const onConfirm = jest.fn();
    render(
      <RejectApprovalModal
        open
        approval={GATE_APPROVAL}
        onClose={onClose}
        onConfirm={onConfirm}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("resets the reason and route when reopened for a different approval", () => {
    const { rerender } = render(
      <RejectApprovalModal
        open
        approval={GATE_APPROVAL}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText(/what needs to change/i), {
      target: { value: "Some reason" },
    });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "test_design" } });

    rerender(
      <RejectApprovalModal
        open
        approval={{ ...GATE_APPROVAL, id: "appr_2" }}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );

    expect(screen.getByPlaceholderText(/what needs to change/i)).toHaveValue("");
    expect(screen.getByRole("combobox")).toHaveValue("");
  });
});
