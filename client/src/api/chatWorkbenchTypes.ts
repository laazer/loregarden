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

/**
 * Whether the ticket can still take the loop's proposals, as the server reads
 * it.
 *
 * `at_cap` is the growth guard's own `would_grow_past`, not `on_ticket + queued
 * >= max`: the two disagree on a ticket already past the cap, and the card
 * disabling the wrong set is the lie this replaces. `message` is the server's
 * refusal wording, built once in `chat_feature_loop._capacity_message`; the
 * card renders the string it is given and composes none of its own. It is null
 * whenever there is room, so "there is space" and "here is why you cannot"
 * never collapse into one answer.
 */
export interface FeatureLoopCapacity {
  on_ticket: number;
  queued: number;
  max: number;
  at_cap: boolean;
  message: string | null;
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
  /** Required: a missing payload must not be readable as "not at cap". */
  capacity: FeatureLoopCapacity;
  criteria: FeatureCriterion[];
  runs: FeatureLoopRun[];
}

export interface ChatBranch {
  branch: string;
  parent_branch: string;
  worktree_path: string;
}

export interface FeatureLoopStartRequest {
  existing_ticket?: string;
  title?: string;
  workspace_slug?: string;
  parent_ticket_id?: string;
}
