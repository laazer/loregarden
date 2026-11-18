import { fireEvent, screen } from "@testing-library/react";

import type { Approval } from "../../api/client";
import { renderWithRouter } from "../../test/renderWithRouter";
import { ApprovalCard } from "../ApprovalCard";

const QUESTION = "Should the cache key include the workspace slug?";

const OPERATOR_QUESTION: Approval = {
  id: "appr_q",
  title: "Agent assumption: cache key",
  level: "medium",
  workspace_slug: "loregarden",
  stage_key: "implement",
  stage_name: "Implement",
  impact: "The implementer is proceeding on its default.",
  checklist: [],
  route_options: [],
  ticket_id: "ticket_1",
  ticket_external_id: "lg-code-knowledge-1055",
  kind: "operator_question",
  status: "pending",
  run_id: "",
  tool_name: "",
  tool_input_json: JSON.stringify({
    questions: [
      {
        question: QUESTION,
        header: "Assumption",
        multiSelect: false,
        options: [{ label: "Yes, per workspace" }, { label: "No, global" }],
      },
    ],
    operator_question: {
      default: "Yes, per workspace",
      confidence: "medium",
      reversibility: "costly",
      affects: "spec",
      asked_at_stage: "implement",
      context: "AC-3 names a cache but not its scope.",
    },
  }),
  cli_adapter: "",
};

function renderCard() {
  const onApprove = jest.fn();
  const onReject = jest.fn();
  const onIgnore = jest.fn();
  renderWithRouter(
    <ApprovalCard approval={OPERATOR_QUESTION} onApprove={onApprove} onReject={onReject} onIgnore={onIgnore} />,
  );
  return { onApprove, onReject };
}

describe("ApprovalCard for an operator question", () => {
  it("preselects the agent's default so confirming is one click", () => {
    const { onApprove } = renderCard();

    expect(screen.getByRole("radio", { name: /Yes, per workspace/ })).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    expect(onApprove).toHaveBeenCalledWith(
      expect.objectContaining({ answers: { [QUESTION]: "Yes, per workspace" } }),
    );
  });

  it("turns the primary action into an override when another option is picked", () => {
    const { onApprove } = renderCard();

    fireEvent.click(screen.getByRole("radio", { name: /No, global/ }));
    fireEvent.click(screen.getByRole("button", { name: "Override & send back" }));

    expect(onApprove).toHaveBeenCalledWith(expect.objectContaining({ answers: { [QUESTION]: "No, global" } }));
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
  });

  it("treats a typed custom answer as an override", () => {
    renderCard();

    fireEvent.change(screen.getByRole("textbox", { name: `Custom answer: ${QUESTION}` }), {
      target: { value: "Per ticket" },
    });

    expect(screen.getByRole("button", { name: "Override & send back" })).toBeEnabled();
  });

  it("offers no decline and no freeform reply, since neither has a meaning here", () => {
    renderCard();

    expect(screen.queryByRole("button", { name: "Decline" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /freeform reply/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ignore" })).toBeInTheDocument();
  });

  it("states the default, the stage an override re-runs, and why it asked", () => {
    renderCard();

    const details = screen.getByText("Agent proceeding on").closest("dl") as HTMLElement;
    expect(details).toHaveTextContent("Yes, per workspace");
    expect(details).toHaveTextContent("spec");
    expect(details).toHaveTextContent("AC-3 names a cache but not its scope.");
  });
});
