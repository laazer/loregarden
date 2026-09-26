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
  /** Other names recall and search find it by. */
  aliases: string[];
  confidence: LearningConfidence;
}

export interface MemoryNodeVersion {
  version: number;
  title: string;
  body: string;
  discredited: boolean;
  /** Null for versions recorded before aliases were versioned. */
  aliases: string[] | null;
  became_current_at: string;
  superseded_at: string;
  /** Null when the writer did not say — not the same as an empty string. */
  superseded_by: string | null;
  change_note: string | null;
}

/** The server's `MemoryRelationType`, in the order the vocabulary is documented. */
export const RELATION_TYPES = [
  "related",
  "supports",
  "contradicts",
  "extends",
  "part_of",
  "applies",
  "supersedes",
] as const;
export type RelationType = (typeof RELATION_TYPES)[number];

/** One edge touching a node. `direction` is "out" when the node is the edge's source. */
export interface MemoryRelation {
  id: string;
  /** Usually a `RelationType`; rows written before the vocabulary was closed may differ. */
  relation_type: string;
  direction: "out" | "in";
  node_id: string;
  title: string;
  discredited: boolean;
}

export interface NodeRef {
  id: string;
  title: string;
}

export interface MemoryNodeDetail extends MemoryNode {
  versions: MemoryNodeVersion[];
  ladder: Record<OutcomeRung, number>;
  relations: MemoryRelation[];
  /** Live nodes that supersede this one. Empty when it is current. */
  superseded_by: NodeRef[];
}

export type LineageStep = Omit<MemoryNode, "confidence"> & { versions: MemoryNodeVersion[] };

export interface MemoryLineage {
  node_id: string;
  /** The supersession chain, oldest first. One step means nothing replaced anything. */
  steps: LineageStep[];
}

/** Graph-health counts; see `services/memory_graph_health.py`. */
export interface GraphFigures {
  learnings: number;
  unlinked: number;
  never_surfaced: number;
  surfaced_unscored: number;
  stale: number;
  contested: number;
  superseded: number;
  discredited: number;
}

export const HEALTH_SHARES = [
  "unlinked",
  "never_surfaced",
  "contested",
  "stale",
  "surfaced_unscored",
] as const;
export type HealthShare = (typeof HEALTH_SHARES)[number];

export interface GraphHealthReading {
  workspace_slug: string;
  measured_at: string;
  figures: GraphFigures;
  shares: Record<HealthShare, number>;
}

export interface GraphHealthReport {
  current: GraphHealthReading;
  previous: GraphHealthReading | null;
  moved: { metric: HealthShare; was: number; now: number }[];
  notes: string[];
  watch: HealthShare | null;
}

export const PROPOSAL_KINDS = ["generic_title", "near_duplicate", "contested", "cold"] as const;
export type ProposalKind = (typeof PROPOSAL_KINDS)[number];

export interface MemoryProposal {
  kind: ProposalKind;
  node_ids: string[];
  titles: string[];
  reason: string;
  suggested_title: string | null;
  similarity: number | null;
}

export interface MergeResult {
  survivor: MemoryNode;
  absorbed_id: string;
  aliases_added: string[];
  edges_moved: number;
  outcomes_moved: number;
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
  memoryLineage: (nodeId: string, workspaceSlug: string) =>
    request<MemoryLineage>(
      `/api/memory/nodes/${encodeURIComponent(nodeId)}/lineage?workspace_slug=${encodeURIComponent(workspaceSlug)}`,
    ),
  memoryGraphHealth: (workspaceSlug: string) =>
    request<GraphHealthReport>(
      `/api/memory/graph-health?workspace_slug=${encodeURIComponent(workspaceSlug)}`,
    ),
  recordMemoryGraphHealth: (workspaceSlug: string) =>
    request<GraphHealthReport>("/api/memory/graph-health/snapshots", {
      method: "POST",
      body: JSON.stringify({ workspace_slug: workspaceSlug }),
    }),
  memoryProposals: (workspaceSlug: string) =>
    request<MemoryProposal[]>(
      `/api/memory/proposals?workspace_slug=${encodeURIComponent(workspaceSlug)}`,
    ),
  retitleMemoryNode: (
    nodeId: string,
    body: { workspace_slug: string; title: string; aliases?: string[]; reason: string },
  ) =>
    request<MemoryNodeDetail>(`/api/memory/nodes/${encodeURIComponent(nodeId)}/title`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  mergeMemoryNodes: (
    survivorId: string,
    body: { workspace_slug: string; absorbed_id: string; reason: string },
  ) =>
    request<MergeResult>(`/api/memory/nodes/${encodeURIComponent(survivorId)}/merge`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  createMemoryRelation: (body: {
    workspace_slug: string;
    source_id: string;
    target_id: string;
    relation_type: RelationType;
  }) =>
    request<{ id: string; created: boolean }>("/api/memory/relations", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
