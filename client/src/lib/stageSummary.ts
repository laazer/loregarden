/**
 * One line per stage for the ticket page's Workflow card: how long the stage
 * has taken, and whether its latest visit is failing, read from the ledger the
 * Timeline already fetched.
 */

import type { WorkflowStageView } from "../api/types";
import { duration } from "./duration";
import { isParallelStage } from "./stageDisplay";
import type { TimelineVisit } from "./ticketTimeline";

export interface StageRunSummary {
  /** Wall-clock across every visit that has finished; null when none has. */
  seconds: number | null;
  /** The newest visit's attempts, and how many of them failed. */
  attempts: number;
  failedAttempts: number;
  /** The newest visit failed and nothing has recovered it, nor is still running. */
  failing: boolean;
}

/** Each stage key's summary, from the ticket's visits (oldest first). */
export function stageRunSummaries(visits: readonly TimelineVisit[]): Map<string, StageRunSummary> {
  const byStage = new Map<string, StageRunSummary>();
  for (const visit of visits) {
    const key = visit.visit.stage_key;
    const before = byStage.get(key);
    const seconds =
      visit.durationSeconds === null ? (before?.seconds ?? null) : (before?.seconds ?? 0) + visit.durationSeconds;
    byStage.set(key, {
      seconds,
      attempts: visit.visit.attempts.length,
      failedAttempts: visit.failedAttempts.length,
      failing: !visit.active && visit.failedAttempts.length > 0 && !visit.latestRunSucceeded,
    });
  }
  return byStage;
}

/**
 * The short text at the end of a stage's row: what is happening now, then
 * how many tries a failing stage has taken, then how long it took. Fan-out
 * stages that have not run say how many lanes they will open.
 */
export function stageRowMeta(stage: WorkflowStageView, summary: StageRunSummary | undefined): string {
  if (stage.status === "running") return "running";
  if (summary?.failing) return summary.attempts > 1 ? `${summary.attempts}×` : "failed";
  if (summary?.seconds != null) return duration(summary.seconds);
  if (isParallelStage(stage) && stage.agents.length > 1) return `${stage.agents.length} lanes`;
  if (stage.optional && stage.status === "pending") return "optional";
  return "";
}
