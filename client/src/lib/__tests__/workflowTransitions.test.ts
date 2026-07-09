import { stageRouteHints, stageRouteLabel } from "../workflowTransitions";
import type { WorkflowStageView, WorkflowTransition } from "../../api/types";

function stage(stageKey: string, name: string, order: number): WorkflowStageView {
  return {
    key: stageKey,
    name,
    order,
    status: "pending",
    agent_id: "",
    skill_name: "",
    optional: false,
    note: "",
    stage_type: "gate",
    agents: [],
  };
}

describe("workflowTransitions", () => {
  const transitions: WorkflowTransition[] = [
    { from: "script_review", to: "ac_gate", when: "pass" },
    { from: "script_review", to: "implementation", when: "reject" },
    { from: "ac_gate", to: "playtest", when: "pass" },
    { from: "ac_gate", to: "implementation", when: "reject", agent_id: "core_simulation" },
  ];

  const stages = [
    stage("implementation", "Implementation", 6),
    stage("script_review", "Script Review", 7),
    stage("ac_gate", "AC Gate", 8),
    stage("playtest", "Playtest", 9),
  ];

  it("collects pass and reject routes for a stage", () => {
    const hints = stageRouteHints("ac_gate", transitions, stages);
    expect(hints).toHaveLength(2);
    expect(hints[0].when).toBe("reject");
    expect(hints[0].targetKey).toBe("implementation");
    expect(hints[0].upstream).toBe(true);
    expect(hints[1].when).toBe("pass");
    expect(hints[1].targetKey).toBe("playtest");
  });

  it("formats upstream reject labels", () => {
    const [reject] = stageRouteHints("ac_gate", transitions, stages);
    expect(stageRouteLabel(reject)).toBe("↩ Implementation · core_simulation on reject");
  });

  it("formats forward pass labels", () => {
    const [, pass] = stageRouteHints("ac_gate", transitions, stages);
    expect(stageRouteLabel(pass)).toBe("→ Playtest on pass");
  });
});

describe("stageDisplay gate label", () => {
  it("mentions upstream routing for gate stages", async () => {
    const { stageKindLabel } = await import("../stageDisplay");
    expect(
      stageKindLabel({
        key: "ac_gate",
        name: "AC Gate",
        status: "pending",
        order: 8,
        agent_id: "ac_gatekeeper",
        skill_name: "ac_gate",
        optional: false,
        note: "",
        stage_type: "gate",
        agents: [],
      }),
    ).toContain("route upstream");
  });
});
