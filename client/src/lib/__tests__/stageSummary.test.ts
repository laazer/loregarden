import type { LedgerAttempt, LedgerVisit, WorkflowStageView } from "../../api/types";
import { stageRowMeta, stageRunSummaries } from "../stageSummary";
import { buildTicketTimeline } from "../ticketTimeline";

function attempt(run_id: string, status: string, minutes: number): LedgerAttempt {
  return {
    run_id,
    run_code: `run_${run_id}`,
    agent_id: "implementer",
    skill_name: "",
    status,
    started_at: "2026-10-24T11:00:00+00:00",
    finished_at: new Date(Date.parse("2026-10-24T11:00:00Z") + minutes * 60_000).toISOString(),
    duration_seconds: minutes * 60,
    error_tail: "",
  };
}

function summaries(...raw: [string, LedgerAttempt[]][]) {
  const ledger: LedgerVisit[] = raw.map(([stage_key, attempts], i) => ({
    stage_key,
    visit_number: i + 1,
    status: attempts[attempts.length - 1].status,
    is_parallel: false,
    attempts,
  }));
  return stageRunSummaries(buildTicketTimeline(ledger, []).visits);
}

const implement = { key: "implement", status: "pending", agents: [], optional: false, stage_type: "agent" } as unknown as WorkflowStageView;

it("adds every visit's time, and reads failure from the newest visit only", () => {
  const byStage = summaries(["implement", [attempt("a", "failed", 2), attempt("b", "failed", 2)]], ["implement", [attempt("c", "succeeded", 3)]]);
  const summary = byStage.get("implement");
  expect(summary).toMatchObject({ seconds: 5 * 60, attempts: 1, failing: false });
  expect(stageRowMeta({ ...implement, status: "done" }, summary)).toBe("5m 0s");
});

it("counts the tries of a stage that is still failing", () => {
  const summary = summaries(["implement", [attempt("a", "failed", 1), attempt("b", "failed", 1), attempt("c", "failed", 1)]]).get("implement");
  expect(stageRowMeta({ ...implement, status: "blocked" }, summary)).toBe("3×");
  const once = summaries(["implement", [attempt("a", "failed", 1)]]).get("implement");
  expect(stageRowMeta({ ...implement, status: "blocked" }, once)).toBe("failed");
});

it("says running over anything recorded, and lanes or optional for a stage not yet run", () => {
  expect(stageRowMeta({ ...implement, status: "running" }, undefined)).toBe("running");
  const review = { ...implement, stage_type: "parallel", agents: [{}, {}, {}] } as unknown as WorkflowStageView;
  expect(stageRowMeta(review, undefined)).toBe("3 lanes");
  expect(stageRowMeta({ ...implement, optional: true }, undefined)).toBe("optional");
  expect(stageRowMeta(implement, undefined)).toBe("");
});
