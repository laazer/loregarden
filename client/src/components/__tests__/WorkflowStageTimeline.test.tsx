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

  describe("as a vine", () => {
    const run = [
      stage({ key: "triage", name: "Triage", status: "done", optional: false }),
      stage({ key: "plan", name: "Plan", status: "wont_do" }),
      stage({ key: "spec", name: "Spec", status: "running", optional: false }),
      stage({ key: "tests", name: "Tests", status: "pending", optional: false }),
    ];

    function nodes(container: HTMLElement) {
      return [...container.querySelectorAll<HTMLElement>(".vine-node")].map((node) => ({
        mark: node.dataset.mark,
        side: node.classList.contains("vine-node--left") ? "left" : "right",
        stem: node.querySelector(".vine-stem path")?.getAttribute("d") ?? null,
        grown: node.querySelector(".vine-stem")?.classList.contains("is-grown") ?? false,
      }));
    }

    it("draws a leaf, a stub, the open bud and a closed bud, with the stem grown past the run", () => {
      const { container } = render(<WorkflowStageTimeline stages={run} currentStageKey="spec" />);

      expect(nodes(container).map((n) => [n.mark, n.grown])).toEqual([
        ["leaf", true],
        ["pruned", true],
        ["bud-open", false],
        ["bud-closed", false],
      ]);
      // The last stage ends the vine.
      expect(nodes(container).at(-1)?.stem).toBeNull();
      // Decoration: the status pill says it in words.
      expect(container.querySelector(".vine-node")).toHaveAttribute("aria-hidden", "true");
      expect(screen.getByText("Running")).toBeInTheDocument();
    });

    it("grows downward when a stage is appended, and no row above moves, flips or changes", () => {
      const { container, rerender } = render(<WorkflowStageTimeline stages={run} currentStageKey="spec" />);
      const before = nodes(container);

      rerender(
        <WorkflowStageTimeline
          stages={[...run, stage({ key: "tests-2", name: "Tests (again)", status: "pending", optional: false })]}
          currentStageKey="spec"
        />,
      );
      const after = nodes(container);

      expect(after).toHaveLength(5);
      // Every earlier row is unchanged, except that the old last row now has
      // its stem down to the new one.
      expect(after.slice(0, 3)).toEqual(before.slice(0, 3));
      expect(after[3]).toEqual({ ...before[3], stem: expect.any(String) });
    });
  });
});
