import type { WorkflowStageView } from "../../../api/client";
import { agentStatusSnapshot, buildHiveWorld, STATION_POSITIONS } from "../worldModel";

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

describe("buildHiveWorld", () => {
  it("maps stages into stations and agents", () => {
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
        stage({
          key: "gate",
          name: "Gate",
          agent_id: "human_gatekeeper",
          status: "awaiting",
          stage_type: "gate",
        }),
      ],
      { skin: "dunder_mifflin" },
    );

    expect(model.idle).toBe(false);
    expect(model.floorTitle).toBe("Dunder Mifflin floor");
    expect(model.orchestratorLabel).toBe("Regional Manager");
    expect(model.agents).toHaveLength(2);
    expect(model.stations).toHaveLength(5);

    const coder = model.agents.find((a) => a.id === "backend_implementer");
    expect(coder?.station).toBe("coding");
    expect(coder?.motion).toBe("working");
    expect(coder?.target).toEqual(STATION_POSITIONS.coding);
    expect(coder?.showTool).toBe(true);

    const codingStation = model.stations.find((s) => s.id === "coding");
    expect(codingStation?.active).toBe(true);
    expect(codingStation?.label).toBe("Cubicle");
    expect(codingStation?.occupiedBy).toContain("backend_implementer");
  });

  it("returns idle when no agent stages exist", () => {
    const model = buildHiveWorld(
      [stage({ key: "gate", name: "Gate", agent_id: "human_review", status: "awaiting", stage_type: "gate" })],
      { skin: "warcraft" },
    );
    expect(model.idle).toBe(true);
    expect(model.agents).toHaveLength(0);
    expect(model.floorTitle).toBe("Battleground");
  });

  it("emits waiting and error events", () => {
    const model = buildHiveWorld(
      [
        stage({
          key: "impl",
          name: "Implementation",
          agent_id: "backend_implementer",
          status: "blocked",
          skill_name: "apply_patch",
        }),
      ],
      { skin: "cyberpunk", hasErrorArtifact: true },
    );

    expect(model.waitingProp.visible).toBe(true);
    expect(model.waitingProp.label).toBe("Smoking");
    expect(model.events.some((e) => e.kind === "waiting")).toBe(true);
    expect(model.events.some((e) => e.kind === "error" && e.label === "System Crash")).toBe(true);
  });

  it("emits context/diff flights on status transitions", () => {
    const stages = [
      stage({
        key: "impl",
        name: "Implementation",
        agent_id: "backend_implementer",
        status: "running",
        skill_name: "apply_patch",
      }),
    ];

    const first = buildHiveWorld(stages, {
      skin: "starcraft",
      previousStatuses: { backend_implementer: "pending" },
    });
    expect(first.flights).toHaveLength(1);
    expect(first.flights[0]?.kind).toBe("context");
    expect(first.flights[0]?.label).toBe("Data Crystal");

    const second = buildHiveWorld(
      [
        stage({
          key: "impl",
          name: "Implementation",
          agent_id: "backend_implementer",
          status: "done",
          skill_name: "apply_patch",
        }),
      ],
      {
        skin: "starcraft",
        previousStatuses: agentStatusSnapshot(first.agents),
      },
    );
    expect(second.flights.some((f) => f.kind === "diff" && f.label === "Hologram")).toBe(true);
  });
});
