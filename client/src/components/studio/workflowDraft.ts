/**
 * The workflow editor's draft.
 *
 * `transitions` round-trips through the editor untouched: the editor cannot
 * author routes yet, but dropping them made every save regenerate a bare linear
 * chain server-side, silently destroying `when: reject` edges.
 */

import type { StudioWorkflow, StudioWorkflowStage, WorkflowTransition } from "../../api/client";
import { emptyStage } from "./studioWorkflowHelpers";

export interface WorkflowDraft {
  slug: string;
  name: string;
  description: string;
  stages: StudioWorkflowStage[];
  transitions: WorkflowTransition[];
}

export function emptyWorkflowDraft(): WorkflowDraft {
  return { slug: "", name: "", description: "", stages: [emptyStage(1)], transitions: [] };
}

export function draftFromWorkflow(workflow: StudioWorkflow): WorkflowDraft {
  return {
    slug: workflow.slug,
    name: workflow.name,
    description: workflow.description,
    stages: workflow.stages.length ? workflow.stages : [emptyStage(1)],
    transitions: workflow.transitions ?? [],
  };
}
