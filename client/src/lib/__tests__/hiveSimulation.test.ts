import type { WorkflowStageView } from "../api/client";
import { buildHiveSimulation } from "../hiveSimulation";

const stage = (
  overrides: Partial<WorkflowStageView> & Pick<WorkflowStageView, "key" | "name" | "agent_id">,
): WorkflowStageView => ({
  status: "pending",
  skill_name: "",
  optional: false,
  note: "",
  stage_type: "agent",
  agents: [],
  ...overrides,
});

describe("buildHiveSimulation", () => {
  it("maps workflow stages into hive desks", () => {
    const model = buildHiveSimulation([
      stage({ key: "plan", name: "Planning", agent_id: "planner", status: "done", skill_name: "plan" }),
      stage({
        key: "impl",
        name: "Implementation",
        agent_id: "backend_implementer",
        status: "running",
        skill_name: "apply_patch",
      }),
      stage({ key: "gate", name: "Gate", agent_id: "human_gatekeeper", status: "awaiting", stage_type: "gate" }),
    ]);

    expect(model.idle).toBe(false);
    expect(model.agents).toHaveLength(2);
    expect(model.agents[0]?.name).toBe("Planner");
    expect(model.agents[1]?.init).toBe("BI");
    expect(model.agents[1]?.showTool).toBe(true);
    expect(model.lines).toHaveLength(2);
    expect(model.orchestratorActive).toBe(true);
  });

  it("returns idle when no agent stages exist", () => {
    const model = buildHiveSimulation([
      stage({ key: "gate", name: "Gate", agent_id: "human_review", status: "awaiting", stage_type: "gate" }),
    ]);

    expect(model.idle).toBe(true);
    expect(model.agents).toHaveLength(0);
  });
});
