import { fireEvent, screen } from "@testing-library/react";

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
  route_options: [],
  ticket_id: "ticket_1",
  ticket_external_id: "01-blobert-dash",
  kind: "workflow_gate",
  status: "pending",
  run_id: "",
  tool_name: "",
  tool_input_json: "{}",
  cli_adapter: "",
};

/**
 * jsdom lays nothing out, so the measured height is the only thing that decides
 * whether a card clamps — stub it rather than trying to produce real overflow.
 */
function stubContentHeight(pixels: number) {
  const descriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollHeight");
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
    configurable: true,
    get: () => pixels,
  });
  return () => {
    if (descriptor) Object.defineProperty(HTMLElement.prototype, "scrollHeight", descriptor);
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>).scrollHeight;
  };
}

describe("ApprovalCard collapsing in a narrow rail", () => {
  let restoreHeight: (() => void) | null = null;

  afterEach(() => {
    restoreHeight?.();
    restoreHeight = null;
  });

  it("clamps an over-tall body and offers the full view", () => {
    restoreHeight = stubContentHeight(900);
    const onExpand = jest.fn();
    const { container } = renderWithRouter(
      <ApprovalCard
        approval={GATE_APPROVAL}
        collapsible
        onExpand={onExpand}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    expect(container.querySelector(".approval-card-body--clamped")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /show the full approval/i }));
    expect(onExpand).toHaveBeenCalledTimes(1);
  });

  it("leaves a short body alone", () => {
    restoreHeight = stubContentHeight(120);
    const { container } = renderWithRouter(
      <ApprovalCard
        approval={GATE_APPROVAL}
        collapsible
        onExpand={() => {}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    expect(container.querySelector(".approval-card-body--clamped")).toBeNull();
    expect(screen.queryByRole("button", { name: /show the full approval/i })).toBeNull();
  });

  it("never clamps when the card is not collapsible", () => {
    restoreHeight = stubContentHeight(900);
    const { container } = renderWithRouter(
      <ApprovalCard approval={GATE_APPROVAL} onApprove={() => {}} onReject={() => {}} />,
    );

    expect(container.querySelector(".approval-card-body--clamped")).toBeNull();
  });

  it("routes a clamped question to the full view instead of a dead submit button", () => {
    restoreHeight = stubContentHeight(900);
    const onExpand = jest.fn();
    renderWithRouter(
      <ApprovalCard
        approval={{
          ...GATE_APPROVAL,
          kind: "cli_question",
          questions: [
            {
              question: "Which cooldown?",
              header: "Tuning",
              multiSelect: false,
              options: [{ label: "0.5s", description: "" }],
            },
          ],
        }}
        collapsible
        onExpand={onExpand}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    expect(screen.queryByRole("button", { name: "Submit answers" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Answer in full view" }));
    expect(onExpand).toHaveBeenCalledTimes(1);
  });
});
