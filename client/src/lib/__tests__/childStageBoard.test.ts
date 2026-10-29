import type { TicketSummary, WorkflowStageView } from "../../api/types";
import { boardColumns, boardGroup, cellLabel, cellMark, stageCounts } from "../childStageBoard";

function stage(key: string, status: WorkflowStageView["status"] = "pending"): WorkflowStageView {
  return { key, name: key[0].toUpperCase() + key.slice(1), status, agent_id: "", skill_name: "", optional: false, note: "", stage_type: "agent", agents: [] };
}

function child(over: Partial<TicketSummary>): TicketSummary {
  return {
    id: "c",
    external_id: "lg-1",
    title: "Child",
    state: "in_progress",
    priority: 1,
    workspace_slug: "loregarden",
    workflow_stage_key: "spec",
    workflow_stage_status: "running",
    workflow_stage_name: "Spec",
    run_code: "",
    work_item_type: "task",
    parent_ticket_id: "p",
    milestone: "",
    branch: "",
    child_count: 0,
    current_stage_agent: "",
    stages: [],
    ...over,
  } as TicketSummary;
}

describe("the child stage board", () => {
  it("lays the stages out in workflow order, with a stage only one workflow has slotted in after its predecessor", () => {
    const a = child({ stages: [stage("triage"), stage("spec"), stage("implement"), stage("done")] });
    const b = child({ stages: [stage("triage"), stage("spec"), stage("review"), stage("done")] });
    expect(boardColumns([a, b]).map((c) => c.key)).toEqual(["triage", "spec", "review", "implement", "done"]);
  });

  it("falls back to the parent's workflow when no child has started one, and reads such a child as not reached", () => {
    const fresh = child({ stages: [] });
    expect(boardColumns([fresh], [stage("triage"), stage("spec")]).map((c) => c.key)).toEqual(["triage", "spec"]);
    expect(cellMark(fresh, "spec")).toBe("pending");
  });

  it("groups a row by where the child stands, a blocked one first", () => {
    expect(boardGroup(child({ state: "blocked" }))).toBe("blocked");
    expect(boardGroup(child({ state: "in_progress", activity: "awaiting" }))).toBe("you");
    expect(boardGroup(child({ state: "in_progress", activity: "running" }))).toBe("running");
    expect(boardGroup(child({ state: "in_progress", activity: "idle" }))).toBe("in_progress");
    expect(boardGroup(child({ state: "backlog" }))).toBe("not_started");
    expect(boardGroup(child({ state: "done" }))).toBe("done");
  });

  it("names each cell in words, including a stage the child's workflow lacks", () => {
    const c = child({ stages: [stage("spec", "running")] });
    expect(cellLabel({ key: "spec", name: "Spec" }, cellMark(c, "spec"))).toBe("Spec: running");
    expect(cellLabel({ key: "review", name: "Review" }, cellMark(c, "review"))).toBe("Review: not in this workflow");
  });

  it("counts each child at its current stage, and a done one at the last column", () => {
    const columns = [
      { key: "triage", name: "Triage" },
      { key: "spec", name: "Spec" },
      { key: "done", name: "Done" },
    ];
    const counts = stageCounts(
      [
        child({ workflow_stage_key: "triage", state: "backlog" }),
        child({ workflow_stage_key: "triage", state: "blocked" }),
        child({ workflow_stage_key: "spec" }),
        child({ workflow_stage_key: "done", state: "done" }),
        child({ workflow_stage_key: "spec", state: "wont_do" }),
      ],
      columns,
    );
    expect(Object.fromEntries(counts)).toEqual({ triage: 2, spec: 1, done: 1 });
  });
});
