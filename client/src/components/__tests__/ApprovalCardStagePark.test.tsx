import { fireEvent, screen } from "@testing-library/react";

import type { Approval } from "../../api/client";
import { renderWithRouter } from "../../test/renderWithRouter";
import { ApprovalCard } from "../ApprovalCard";

/**
 * A parked stage is not a sign-off. Approving one re-runs the stage with a
 * failing pre-dispatch check waived — where approving a gate marks the stage
 * done. Falling through to the generic gate card is how "Approve" came to mean
 * "skip this stage entirely", so the wording is the fix's user-facing half.
 */
const PARK_APPROVAL: Approval = {
  id: "appr_park",
  title: "Environment preflight failed on 01-blobert-dash",
  level: "high",
  workspace_slug: "blobert-tdd",
  stage_key: "implement",
  stage_name: "Implementation",
  impact:
    "Environment preflight failed before this stage could run.\n" +
    "  git_core_bare: core.bare=true in /repo, which has a working tree.\n" +
    "    fix: git config --local core.bare false",
  checklist: ["Dash cancels on wall contact"],
  route_options: [{ key: "plan", name: "Plan" }],
  ticket_id: "ticket_1",
  ticket_external_id: "01-blobert-dash",
  kind: "stage_park",
  status: "pending",
  run_id: "",
  tool_name: "",
  tool_input_json: "{}",
  cli_adapter: "",
};

function renderPark(overrides: Partial<Parameters<typeof ApprovalCard>[0]> = {}) {
  const onApprove = jest.fn();
  const onReject = jest.fn();
  const view = renderWithRouter(
    <ApprovalCard approval={PARK_APPROVAL} onApprove={onApprove} onReject={onReject} {...overrides} />,
  );
  return { ...view, onApprove, onReject };
}

describe("ApprovalCard for a parked stage", () => {
  it("offers to run the stage rather than to approve it", () => {
    renderPark();

    expect(screen.getByRole("button", { name: "Run anyway" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("names the other choice as leaving the stage parked", () => {
    renderPark();

    expect(screen.getByRole("button", { name: "Leave parked" })).toBeInTheDocument();
  });

  it("resolves without opening the gate's reject modal", () => {
    const { onReject } = renderPark();

    fireEvent.click(screen.getByRole("button", { name: "Leave parked" }));

    expect(onReject).toHaveBeenCalled();
  });

  it("shows the diagnosis and its remediation", () => {
    renderPark();

    expect(screen.getByText(/core\.bare=true/)).toBeInTheDocument();
    expect(screen.getByText(/git config --local core\.bare false/)).toBeInTheDocument();
  });

  it("offers no rework route — there is no earlier stage that fixes a checkout", () => {
    renderPark();

    expect(screen.queryByText(/route back/i)).not.toBeInTheDocument();
  });

  it("does not dress a machine problem up as an acceptance checklist", () => {
    renderPark();

    expect(screen.queryByText("Dash cancels on wall contact")).not.toBeInTheDocument();
  });

  it("disables both choices while one is in flight, so a second click cannot re-arm the waiver", () => {
    renderPark({ isSubmitting: true });

    expect(screen.getByRole("button", { name: "Run anyway" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Leave parked" })).toBeDisabled();
  });
});
