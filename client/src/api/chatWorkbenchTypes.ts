/**
 * What a Baxter chat snapshot carries for the workbench's side cards: the
 * thread's focus (Scope card), its feature loop (Feature card), and its
 * branch (Changes card). Mirrors `services/baxter_chat_focus.focus_view`,
 * `chat_feature_loop.feature_loop_view` and `baxter_chat_branch.chat_branch_view`.
 */

export interface ChatFocusWorkspace {
  id: string;
  slug: string;
  /** The thread's own workspace: always in scope, so not un-tickable. */
  fixed: boolean;
}

export interface ChatFocusInitiative {
  id: string;
  external_id: string;
  title: string;
  /** The initiative the thread's archive is focused on. */
  fixed: boolean;
}

export interface ChatFocus {
  /** True when the thread answers across every workspace. */
  all_workspaces: boolean;
  workspaces: ChatFocusWorkspace[];
  initiatives: ChatFocusInitiative[];
}

export type FeatureCriterionStatus = "proposed" | "queued" | "in_round" | "met";
export type FeatureLoopStatus = "idle" | "running" | "blocked";

export interface FeatureCriterion {
  id: string;
  text: string;
  status: FeatureCriterionStatus;
  /** The round that carried it onto the ticket; 0 until one has. */
  round: number;
}

export interface FeatureLoopRun {
  id: string;
  run_code: string;
  agent_id: string;
  stage_key: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface FeatureLoop {
  ticket: {
    id: string;
    external_id: string;
    title: string;
    state: string;
    stage_key: string;
    workspace_slug: string | null;
    criteria_count: number;
  };
  status: FeatureLoopStatus;
  /** Why a blocked loop is not moving; "" otherwise. */
  reason: string;
  round: number;
  max_criteria: number;
  criteria: FeatureCriterion[];
  runs: FeatureLoopRun[];
}

export interface ChatBranch {
  branch: string;
  parent_branch: string;
  worktree_path: string;
  has_conflicts: boolean;
}

export interface FeatureLoopStartRequest {
  existing_ticket?: string;
  title?: string;
  workspace_slug?: string;
  parent_ticket_id?: string;
}
