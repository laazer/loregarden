import { request } from "./http";

/**
 * Briefing health over a window of runs — mirrors the server's
 * `MemoryBriefingStats` (`services/memory_briefing_telemetry.py`).
 *
 * Denominated over `agent_runs`, so `runs_with_no_briefing_row` counts runs
 * where nothing was recorded at all: holes, not a sixth outcome. An empty
 * window is zeros with null timestamps, never an error.
 */
export interface MemoryBriefingStats {
  window_days: number;
  window_from: string;
  window_to: string;
  runs_in_window: number;
  runs_with_briefing_row: number;
  runs_with_no_briefing_row: number;
  rows_in_window: number;
  newest_run_at: string | null;
  last_row_at: string | null;
  built: number;
  empty: number;
  store_error: number;
  no_store: number;
  skipped: number;
}

/** The recorded outcome buckets, in the order the page lists them. */
export const BRIEFING_OUTCOMES = ["built", "empty", "store_error", "no_store", "skipped"] as const;
export type BriefingOutcome = (typeof BRIEFING_OUTCOMES)[number];

/** A learning's Beta posterior. `observations === 0` means never observed. */
export interface LearningConfidence {
  mean: number;
  lower_bound: number;
  observations: number;
  trusted: boolean;
}

/** The four-rung outcome ladder, best to worst — the server's `LearningOutcomeRung`. */
export const OUTCOME_RUNGS = ["clean_pass", "passed_after_autofix", "rerouted", "blocked"] as const;
export type OutcomeRung = (typeof OUTCOME_RUNGS)[number];

export interface MemoryNode {
  id: string;
  title: string;
  body: string;
  tags: string[];
  ticket_id: string;
  workspace_slug: string;
  node_type: string;
  created_at: string;
  updated_at: string;
  discredited: boolean;
  confidence: LearningConfidence;
}

export interface MemoryNodeVersion {
  version: number;
  title: string;
  body: string;
  discredited: boolean;
  became_current_at: string;
  superseded_at: string;
  /** Null when the writer did not say — not the same as an empty string. */
  superseded_by: string | null;
  change_note: string | null;
}

export interface MemoryNodeDetail extends MemoryNode {
  versions: MemoryNodeVersion[];
  ladder: Record<OutcomeRung, number>;
}

export interface MemoryNodeList {
  workspace_slug: string;
  include_discredited: boolean;
  nodes: MemoryNode[];
}

export interface DiscreditInput {
  workspace_slug: string;
  discredited: boolean;
  reason: string;
}

export const memoryApi = {
  memoryBriefings: (windowDays: number) =>
    request<MemoryBriefingStats>(`/api/memory/briefings?window_days=${windowDays}`),
  memoryNodes: (workspaceSlug: string, includeDiscredited: boolean) => {
    const q = new URLSearchParams({
      workspace_slug: workspaceSlug,
      include_discredited: String(includeDiscredited),
    });
    return request<MemoryNodeList>(`/api/memory/nodes?${q}`);
  },
  memoryNode: (nodeId: string, workspaceSlug: string) =>
    request<MemoryNodeDetail>(
      `/api/memory/nodes/${encodeURIComponent(nodeId)}?workspace_slug=${encodeURIComponent(workspaceSlug)}`,
    ),
  setMemoryNodeDiscredited: (nodeId: string, body: DiscreditInput) =>
    request<MemoryNodeDetail>(`/api/memory/nodes/${encodeURIComponent(nodeId)}/discredited`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
};
