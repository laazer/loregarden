import type { WorkflowStageView } from "../../api/client";
import { buildHiveReplayFrames } from "../replay";

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

describe("buildHiveReplayFrames", () => {
  it("returns empty when there are no replayable agents", () => {
    expect(
      buildHiveReplayFrames([
        stage({ key: "gate", name: "Gate", agent_id: "human", status: "awaiting", stage_type: "gate" }),
      ]),
    ).toEqual([]);
  });

  it("walks each agent through pending → running → done", () => {
    const frames = buildHiveReplayFrames([
      stage({ key: "plan", name: "Planning", agent_id: "planner", status: "done", skill_name: "plan", order: 1 }),
      stage({
        key: "impl",
        name: "Implementation",
        agent_id: "backend_implementer",
        status: "done",
        skill_name: "apply_patch",
        order: 2,
      }),
    ]);

    expect(frames.length).toBeGreaterThan(3);
    expect(frames[0]?.find((s) => s.key === "plan")?.status).toBe("pending");
    expect(frames[0]?.find((s) => s.key === "impl")?.status).toBe("pending");

    const plannerRunning = frames.find((frame) =>
      frame.some((s) => s.key === "plan" && s.status === "running"),
    );
    expect(plannerRunning).toBeTruthy();

    const last = frames[frames.length - 1]!;
    expect(last.find((s) => s.key === "plan")?.status).toBe("done");
    expect(last.find((s) => s.key === "impl")?.status).toBe("done");
  });

  it("inserts a blocked beat when the live stage was blocked", () => {
    const frames = buildHiveReplayFrames([
      stage({
        key: "impl",
        name: "Implementation",
        agent_id: "backend_implementer",
        status: "blocked",
        skill_name: "apply_patch",
      }),
    ]);
    expect(frames.some((frame) => frame.some((s) => s.status === "blocked"))).toBe(true);
    expect(frames[frames.length - 1]?.[0]?.status).toBe("done");
  });
});
