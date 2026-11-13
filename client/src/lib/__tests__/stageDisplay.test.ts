import type { WorkflowStageView } from "../../api/client";
import {
  isAgentStage,
  isClassifyStage,
  isHumanGateStage,
  isParallelStage,
  stageAgentSubtitle,
  stageKindLabel,
  stageRunButtonLabel,
  willRerunStageKeys,
} from "../stageDisplay";

function baseStage(overrides: Partial<WorkflowStageView> = {}): WorkflowStageView {
  return {
    key: "planning",
    name: "Planning",
    status: "pending",
    agent_id: "planner",
    skill_name: "plan",
    optional: false,
    note: "",
    stage_type: "agent",
    agents: [],
    ...overrides,
  };
}

describe("stageDisplay", () => {
  it("detects agent stages", () => {
    expect(isAgentStage(baseStage())).toBe(true);
    expect(isAgentStage(baseStage({ agent_id: "" }))).toBe(false);
    expect(isAgentStage(baseStage({ key: "done", agent_id: "" }))).toBe(false);
  });

  it("treats parallel stages as agent stages but not human gates", () => {
    const stage = baseStage({
      key: "script_review",
      name: "Script Review",
      agent_id: "",
      skill_name: "",
      stage_type: "parallel",
      agents: [
        { agent_id: "static_qa", skill_name: "static_qa" },
        { agent_id: "architecture_reviewer", skill_name: "review" },
      ],
    });
    expect(isParallelStage(stage)).toBe(true);
    expect(isHumanGateStage(stage)).toBe(false);
    expect(isAgentStage(stage)).toBe(true);
    expect(stageKindLabel(stage)).toBe("parallel review");
    expect(stageAgentSubtitle(stage)).toBe("static_qa · architecture_reviewer");
    expect(stageRunButtonLabel(stage, false)).toBe("Run reviews");
  });

  it("treats classify stages as routed implementation", () => {
    const stage = baseStage({
      key: "implementation",
      name: "Implementation",
      stage_type: "classify",
      agent_id: "core_simulation",
      agents: [
        { agent_id: "core_simulation", skill_name: "apply_patch" },
        { agent_id: "gameplay_systems", skill_name: "apply_patch" },
      ],
    });
    expect(isClassifyStage(stage)).toBe(true);
    expect(isHumanGateStage(stage)).toBe(false);
    expect(stageKindLabel(stage)).toBe("routes by ticket content · a set agent breaks ties");
  });

  it("keeps optional human gates distinct from parallel review", () => {
    const playtest = baseStage({
      key: "playtest",
      name: "Playtest",
      agent_id: "",
      skill_name: "",
      optional: true,
    });
    expect(isHumanGateStage(playtest)).toBe(true);
    expect(stageKindLabel(playtest)).toBe("human approval gate");
  });

  it("marks done stages after the active stage as rerunning", () => {
    const stages = [
      baseStage({ key: "plan", status: "done" }),
      baseStage({ key: "spec", status: "running" }),
      baseStage({ key: "test", status: "done" }),
      baseStage({ key: "impl", status: "pending" }),
      baseStage({ key: "review", status: "done" }),
      baseStage({ key: "docs", status: "wont_do" }),
    ];
    expect([...willRerunStageKeys(stages)]).toEqual(["test", "review"]);
  });

  it("marks nothing when no stage is active", () => {
    const stages = [
      baseStage({ key: "plan", status: "done" }),
      baseStage({ key: "spec", status: "pending" }),
      baseStage({ key: "test", status: "done" }),
    ];
    expect(willRerunStageKeys(stages).size).toBe(0);
  });

  it("counts from an awaiting or blocked stage too", () => {
    const awaiting = [baseStage({ key: "a", status: "awaiting" }), baseStage({ key: "b", status: "done" })];
    const blocked = [baseStage({ key: "a", status: "blocked" }), baseStage({ key: "b", status: "done" })];
    expect([...willRerunStageKeys(awaiting)]).toEqual(["b"]);
    expect([...willRerunStageKeys(blocked)]).toEqual(["b"]);
  });
});
