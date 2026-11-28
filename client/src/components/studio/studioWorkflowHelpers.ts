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

/** Catalog lookup by adapter. Role/stage pins call this with `"claude"` until 981. */
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

export const ROLE_DEFAULT_MODEL_EMPTY_LABEL = "No pin — run uses the selected harness default";
export const ROLE_DEFAULT_MODEL_HINT = "Applies only when a run's harness is Claude.";
export const STAGE_MODEL_EMPTY_LABEL = "— Agent default —";
export const STAGE_MODEL_HINT = "Applies only when this stage's run harness is Claude.";

/** Claude-family pin list until 981. Empty option is this row's label, not the catalog's. */
export function claudePinModelOptions(
  options: Parameters<typeof modelOptionsForAdapter>[1],
  storedValue: string,
  emptyLabel: string,
): { id: string; label: string }[] {
  const catalog = modelOptionsForAdapter("claude", options) ?? [];
  const named = catalog.filter((opt) => opt.id !== "");
  const rows = [{ id: "", label: emptyLabel }, ...named];
  if (storedValue && !rows.some((opt) => opt.id === storedValue)) {
    rows.push({ id: storedValue, label: storedValue });
  }
  return rows;
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

/** Stable kebab key from a human label (Studio authoring). */
export function exitActionKeyFromLabel(label: string, index: number): string {
  const slug = label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || `exit-action-${index + 1}`;
}
