/**
 * The pieces of the Studio's workflow editor that both halves of it need.
 *
 * `StudioStagesCard` was lifted out of `StudioPage` to bring that file back
 * under the 1200-line gate, and these two functions are the only things the
 * page and the card both call. They live here rather than being imported from
 * one into the other, which would be a cycle — the page imports the card.
 */

import type { StudioWorkflowStage, WorkflowTransition } from "../../api/client";

/**
 * The workflow being edited, before it is published.
 *
 * Named here because the page holds it and the stages card edits it; an inline
 * type on `useState` cannot be referred to by the component that receives it.
 */
export interface StudioWorkflowDraft {
  slug: string;
  name: string;
  description: string;
  stages: StudioWorkflowStage[];
  transitions: WorkflowTransition[];
}

/** Model dropdown options for the agent's declared adapter. */
export function modelOptionsForAdapter(
  adapter: string,
  options:
    | {
        claude_models?: { id: string; label: string }[];
        cursor_models?: { id: string; label: string }[];
        codex_models?: { id: string; label: string }[];
        lmstudio_models?: { id: string; label: string }[];
      }
    | undefined,
) {
  if (adapter === "cursor") return options?.cursor_models;
  if (adapter === "claude") return options?.claude_models;
  if (adapter === "codex") return options?.codex_models;
  if (adapter === "lmstudio") {
    const models = options?.lmstudio_models;
    // Only the Auto placeholder → keep free-text (LM Studio offline).
    return models && models.length > 1 ? models : undefined;
  }
  return undefined;
}

/** A new stage, at `order`, with the defaults the editor opens it on. */
export function emptyStage(order: number): StudioWorkflowStage {
  return {
    key: `stage_${order}`,
    name: `Stage ${order}`,
    stage_type: "agent",
    agent_id: "planner",
    skill_name: "plan",
    optional: false,
    order,
    exit_actions_enabled: false,
    exit_actions: [],
    terminal: false,
    skip_when: "",
    classify_routes: [],
    parallel_agents: [],
    model: "",
  };
}

/** Server-owned catalogs mirrored for Studio selects (GET /api/studio/exit-action-requirements). */
export const EXIT_ACTION_REQUIREMENT_KINDS = [
  { value: "runtime_capability", label: "Runtime capability" },
  { value: "credential", label: "Credential" },
  { value: "authority", label: "Authority" },
  { value: "operator_judgment", label: "Operator judgment" },
] as const;

export const EXIT_ACTION_CAPABILITY_IDS = [
  "http_test_client",
  "git_push",
  "github_pull_request",
  "shell_command",
  "workspace_file_write",
] as const;

export const EXIT_ACTION_CREDENTIAL_KEYS = [
  "claude_profile",
  "cursor_profile",
  "codex_profile",
  "github_token",
] as const;

export const EXIT_ACTION_AUTHORITY_SCOPES = [
  "release:publish",
  "repo:push",
  "workspace:destructive_write",
  "ticket:supersede",
] as const;

/** Stable kebab key from a human label (Studio authoring). */
export function exitActionKeyFromLabel(label: string, index: number): string {
  const slug = label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || `exit-action-${index + 1}`;
}
