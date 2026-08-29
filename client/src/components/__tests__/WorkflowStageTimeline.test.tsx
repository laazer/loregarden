import { render, screen } from "@testing-library/react";

import type { WorkflowStageView } from "../../api/types";
import { WorkflowStageTimeline } from "../WorkflowStageTimeline";

function stage(overrides: Partial<WorkflowStageView>): WorkflowStageView {
  return {
    key: "playtest",
    name: "Playtest",
    status: "wont_do",
    order: 3,
    agent_id: "",
    skill_name: "",
    optional: true,
    note: "",
    stage_type: "agent",
    agents: [],
    model: "",
    ...overrides,
  };
}

describe("WorkflowStageTimeline", () => {
  it("shows why a stage was pruned", () => {
    render(
      <WorkflowStageTimeline
        stages={[stage({ note: "No player-facing change to play test" })]}
        currentStageKey="implement"
      />,
    );

    expect(screen.getByText("No player-facing change to play test")).toBeInTheDocument();
  });

  it("renders no note line when a stage has none", () => {
    const { container } = render(
      <WorkflowStageTimeline stages={[stage({})]} currentStageKey="implement" />,
    );

    expect(container.querySelector(".workflow-stage-note")).toBeNull();
  });
});
