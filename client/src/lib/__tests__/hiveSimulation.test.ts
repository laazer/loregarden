import type { WorkflowStageView } from "../../api/client";
import { buildHiveWorld } from "../hive/worldModel";

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

describe("buildHiveWorld (legacy hiveSimulation entry)", () => {
  it("maps workflow stages into hive agents", () => {
    const model = buildHiveWorld(
      [
        stage({ key: "plan", name: "Planning", agent_id: "planner", status: "done", skill_name: "plan" }),
        stage({
          key: "impl",
          name: "Implementation",
          agent_id: "backend_implementer",
          status: "running",
          skill_name: "apply_patch",
        }),
        stage({ key: "gate", name: "Gate", agent_id: "human_gatekeeper", status: "awaiting", stage_type: "gate" }),
      ],
      { skin: "dunder_mifflin" },
    );

    expect(model.idle).toBe(false);
    expect(model.agents).toHaveLength(2);
    expect(model.agents[0]?.name).toBe("Planner");
    expect(model.agents[1]?.id).toBe("backend_implementer");
    expect(model.agents[1]?.showTool).toBe(true);
    expect(model.orchestratorActive).toBe(true);
  });

  it("returns idle when no agent stages exist", () => {
    const model = buildHiveWorld(
      [stage({ key: "gate", name: "Gate", agent_id: "human_review", status: "awaiting", stage_type: "gate" })],
      { skin: "dunder_mifflin" },
    );

    expect(model.idle).toBe(true);
    expect(model.agents).toHaveLength(0);
  });
});
