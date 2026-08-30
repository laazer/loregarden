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
    gate_required: false,
    terminal: false,
    skip_when: "",
    classify_routes: [],
    parallel_agents: [],
    model: "",
  };
}
