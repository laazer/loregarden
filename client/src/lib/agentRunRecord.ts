/**
 * Reading an agent's run record (`GET /api/studio/agent-runs/{id}`): the totals
 * its page header shows and the words for why a run failed.
 */

import type { AgentRunRecord, RunFailureKind } from "../api/types";

export interface RecordTotals {
  runs: number;
  failed: number;
  /** 0..1, or null before any finished run. */
  rate: number | null;
  lastRunAt: string | null;
}

export function recordTotals(record: AgentRunRecord): RecordTotals {
  const runs = record.stages.reduce((sum, stage) => sum + stage.runs, 0);
  const failed = record.stages.reduce((sum, stage) => sum + stage.failed, 0);
  return {
    runs,
    failed,
    rate: runs ? failed / runs : null,
    lastRunAt: record.recent[0]?.created_at ?? null,
  };
}

/** Who could have unblocked the failure, in the operator's words. */
const KIND_LABEL: Record<RunFailureKind, string> = {
  harness: "harness",
  work: "work",
  decision: "needed a decision",
  human_action: "needed a person",
  tickets: "waited on tickets",
  conflict: "conflict",
};

export function failureKindLabel(kind: RunFailureKind | null): string {
  return kind ? KIND_LABEL[kind] : "unclassified";
}
