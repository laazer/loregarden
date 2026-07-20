import type { WorkflowStageView } from "../../../api/client";
import { OFFICEPLACE_STATIONS } from "../layouts/officeplaceLayout";
import { agentStatusSnapshot, buildHiveWorld } from "../worldModel";

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
      { skin: "officeplace" },
    );

    expect(model.idle).toBe(false);
    expect(model.floorTitle).toBe("Officeplace floor");
    expect(model.orchestratorLabel).toBe("Regional Manager");
    // Officeplace staffs each station with a crew, so the two agents put five
    // bodies on the floor: planner_hq's pair plus coding's trio.
    expect(model.agents).toHaveLength(5);
    expect(model.stations).toHaveLength(5);

    const coders = model.agents.filter((a) => a.agentId === "backend_implementer");
    expect(coders.map((a) => a.character)).toEqual(["jim", "pam", "dwight"]);
    expect(coders.filter((a) => a.lead)).toHaveLength(1);
    expect(new Set(coders.map((a) => a.id)).size).toBe(3);

    const coder = coders[0];
    expect(coder?.station).toBe("coding");
    expect(coder?.motion).toBe("working");
    expect(coder?.target).toEqual(OFFICEPLACE_STATIONS.coding);
    expect(coder?.showTool).toBe(true);

    const codingStation = model.stations.find((s) => s.id === "coding");
    expect(codingStation?.active).toBe(true);
    expect(codingStation?.label).toBe("Cubicle");
    expect(codingStation?.occupiedBy).toContain("backend_implementer");
  });

  it("returns idle when no agent stages exist", () => {
    const model = buildHiveWorld(
      [stage({ key: "gate", name: "Gate", agent_id: "human_review", status: "awaiting", stage_type: "gate" })],
      { skin: "runeplace" },
    );
    expect(model.idle).toBe(true);
    expect(model.agents).toHaveLength(0);
    expect(model.floorTitle).toBe("Officeplace floor");
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
      { skin: "netplace", hasErrorArtifact: true },
    );

    expect(model.waitingProp.visible).toBe(true);
    expect(model.waitingProp.label).toBe("Coffee Machine");
    expect(model.events.some((e) => e.kind === "waiting")).toBe(true);
    expect(model.events.some((e) => e.kind === "error" && e.label === "HR Meeting")).toBe(true);
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
      skin: "starplace",
      previousStatuses: { backend_implementer: "pending" },
    });
    expect(first.flights).toHaveLength(1);
    expect(first.flights[0]?.kind).toBe("context");
    expect(first.flights[0]?.label).toBe("Binder");

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
        skin: "starplace",
        previousStatuses: agentStatusSnapshot(first.agents),
      },
    );
    expect(second.flights.some((f) => f.kind === "diff" && f.label === "Stack of Papers")).toBe(true);
  });

  it("emits one flight per agent, not per crew member", () => {
    const running = [
      stage({
        key: "test",
        name: "Testing",
        agent_id: "static_qa",
        status: "running",
        skill_name: "run_tests",
      }),
    ];
    const first = buildHiveWorld(running, {
      skin: "officeplace",
      previousStatuses: { static_qa: "pending" },
    });

    // The whole MDR crew stands at testing, but they share one agent.
    expect(first.agents.filter((a) => a.agentId === "static_qa")).toHaveLength(4);
    expect(first.flights).toHaveLength(1);
  });

  it("stops placing bodies when the desk row runs out", () => {
    const many = Array.from({ length: 12 }, (_, i) =>
      stage({
        key: `impl-${i}`,
        name: `Implementation ${i}`,
        agent_id: `backend_implementer_${i}`,
        status: "running",
        skill_name: "apply_patch",
      }),
    );
    const model = buildHiveWorld(many, { skin: "officeplace" });
    const deskCount = model.layout.deskRow.length;

    expect(model.agents.length).toBeLessThanOrEqual(deskCount);
    // Every body gets its own desk rather than collapsing onto desk zero.
    const desks = model.agents.map((a) => `${a.desk.x},${a.desk.y}`);
    expect(new Set(desks).size).toBe(desks.length);
  });
});
