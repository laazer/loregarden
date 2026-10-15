import { fireEvent, render, screen } from "@testing-library/react";

import type { TicketDetail, WorkflowStageView } from "../../api/types";
import { StageOverflowMenu } from "../StageOverflowMenu";

/**
 * AC19, from the outside. `buildStageMenuActions` deciding the item is disabled
 * is only half the guard: the flag has to reach it from `Dashboard`, through
 * `StageOverflowMenu`. A prop that is accepted and dropped passes every unit
 * test of the builder and still fires two `start_stage_execution` calls.
 */
const STAGE: WorkflowStageView = {
  key: "spec",
  name: "Specification",
  status: "pending",
  agent_id: "spec_writer",
  skill_name: "spec",
  optional: false,
  note: "",
  stage_type: "agent",
  agents: [],
};

const TICKET = {
  id: "t-1",
  external_id: "lg-milestone-that-917",
  title: "Hand a stage to a terminal",
  workflow_stage_key: "spec",
  workflow_template_slug: "loregarden-tdd",
  workflow_transitions: [],
  stages: [STAGE],
} as unknown as TicketDetail;

function open(props: Partial<Parameters<typeof StageOverflowMenu>[0]> = {}) {
  const onCopyTerminal = jest.fn();
  render(
    <StageOverflowMenu
      ticket={TICKET}
      stage={STAGE}
      runCheck={{ allowed: true, reason: "Run Specification" }}
      isRunning={false}
      workflowBusy={false}
      copyTerminalPending={false}
      onRun={jest.fn()}
      onCopyTerminal={onCopyTerminal}
      onSetCursor={jest.fn()}
      onRouteUpstream={jest.fn()}
      onStageStatus={jest.fn()}
      onEditState={jest.fn()}
      {...props}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Specification actions" }));
  return { onCopyTerminal };
}

describe("StageOverflowMenu — copy terminal command", () => {
  it("runs the handoff when nothing is in flight", () => {
    const { onCopyTerminal } = open();

    fireEvent.click(screen.getByRole("menuitem", { name: "Copy terminal command" }));

    expect(onCopyTerminal).toHaveBeenCalledTimes(1);
  });

  it("refuses a second click while the first handoff is still being prepared", () => {
    const { onCopyTerminal } = open({ copyTerminalPending: true });

    const item = screen.getByRole("menuitem", { name: "Copy terminal command" });
    expect(item).toBeDisabled();
    expect(item.getAttribute("title")).toBe(
      "Already preparing a terminal command for this ticket",
    );

    fireEvent.click(item);

    expect(onCopyTerminal).not.toHaveBeenCalled();
  });
});
