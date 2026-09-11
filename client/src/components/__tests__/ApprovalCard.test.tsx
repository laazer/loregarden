import { fireEvent, screen, within } from "@testing-library/react";

import type { Approval } from "../../api/client";
import { renderWithRouter } from "../../test/renderWithRouter";
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

// react-markdown is mocked in jest, so these assert that plan text reaches the
// markdown renderer rather than the preformatted path — what the rendered
// markdown then looks like is covered by normalizeChatMarkdown's own tests.
describe("ApprovalCard plan rendering", () => {
  it("sends a plan approval's body through the markdown renderer, not <pre>", () => {
    const plan = "## Rollout\n\n- Ship the reader\n- Wire the modal\n";
    const { container } = renderWithRouter(
      <ApprovalCard
        approval={{
          ...PERMISSION_APPROVAL,
          tool_name: "ExitPlanMode",
          tool_input_json: JSON.stringify({ plan }),
        }}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    expect(screen.getByText("Implementation plan")).toBeInTheDocument();
    expect(container.querySelector(".permission-details-markdown")).toBeInTheDocument();
    expect(container.querySelector(".permission-details-value")).toBeNull();
  });

  it("renders the impact text as markdown", () => {
    const { container } = renderWithRouter(
      <ApprovalCard
        approval={{
          ...GATE_APPROVAL,
          impact: "Acceptance criteria:\n- Dash has a cooldown",
        }}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    expect(container.querySelector(".approval-impact.markdown-preview")).toBeInTheDocument();
  });
});

describe("ApprovalCard reject flow", () => {
  it("opens the reject modal for a workflow-gate approval instead of rejecting immediately", () => {
    const onReject = jest.fn();
    renderWithRouter(
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
    renderWithRouter(
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

// A rework pause is a separate `kind` on the server purely so an auto_approve
// run cannot sign off the pause raised to stop it looping. On the card it must
// behave like a gate — the routing box is the whole reason the pause is
// actionable, and losing it puts the operator back at the dead-end card this
// replaced: two buttons, neither of which moved the ticket.
describe("ApprovalCard rework pause", () => {
  const PAUSE_APPROVAL: Approval = {
    ...GATE_APPROVAL,
    id: "appr_pause",
    kind: "rework_pause",
    title: "Rework paused — Procedural locomotion drivers",
    stage_key: "script_review",
    stage_name: "Script Review",
    impact: "Rework loop: 'implement' has been rerouted 4×.",
    route_options: [{ key: "implement", name: "Implementation" }],
  };

  it("offers the routing box, so a reject can name where the work goes", () => {
    renderWithRouter(
      <ApprovalCard approval={PAUSE_APPROVAL} onApprove={() => {}} onReject={() => {}} />,
    );

    expect(screen.getByText(/Routing · changes what Approve does/)).toBeInTheDocument();
    expect(screen.getByText(/rework loop paused/)).toBeInTheDocument();
  });

  it("routes an approval to a named stage the same way a gate does", () => {
    const onApprove = jest.fn();
    renderWithRouter(
      <ApprovalCard approval={PAUSE_APPROVAL} onApprove={onApprove} onReject={() => {}} />,
    );

    fireEvent.click(screen.getByRole("checkbox", { name: /send back through the workflow/i }));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "implement" } });
    // The label changes once routing is on, which is itself the signal that
    // Approve now does something different.
    fireEvent.click(screen.getByRole("button", { name: "Approve & route back" }));

    expect(onApprove).toHaveBeenCalledWith(
      expect.objectContaining({ route_to_stage_key: "implement" }),
    );
  });
});
