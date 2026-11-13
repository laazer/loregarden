import type { WorkflowStageView } from "../../../../api/types";
import { stageProgressSegments } from "../TicketCardMeta";

function stage(key: string, status: WorkflowStageView["status"]): WorkflowStageView {
  return {
    key,
    name: key,
    status,
    agent_id: "",
    skill_name: "",
    optional: false,
    note: "",
    stage_type: "agent",
    agents: [],
  };
}

describe("stageProgressSegments", () => {
  it("tints done stages after the live one as rerunning, and leaves earlier ones quiet", () => {
    const { segments } = stageProgressSegments([
      stage("plan", "done"),
      stage("spec", "running"),
      stage("tests", "done"),
      stage("impl", "pending"),
    ]);

    expect(segments.map((segment) => segment.color)).toEqual([
      "var(--bd2)",
      expect.not.stringMatching(/rerun|bd/),
      "var(--rerun)",
      "var(--bd)",
    ]);
  });
});
