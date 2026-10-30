/**
 * "Describe it" → a draft, for both editors.
 *
 * The two generations are one concern: `StudioDescribeBar` hands over a
 * sentence, the server answers with a generated agent or workflow, and the
 * result is merged into the draft the operator is already editing rather than
 * replacing it. The agent branch merges field by field so a generation that
 * omits a field (the server leaves `adapter` or `default_skill` empty when it
 * has no opinion) keeps what the operator had, instead of blanking it.
 */

import { useMutation } from "@tanstack/react-query";
import type { Dispatch, SetStateAction } from "react";

import type { StudioWorkflowStage, WorkflowTransition } from "../../api/client";
import type { AgentDraft } from "./agentDraft";
import { emptyStage } from "./studioWorkflowHelpers";

/**
 * The workflow editor's draft.
 *
 * `transitions` round-trips through the editor untouched: the editor cannot author
 * routes yet, but dropping them made every save regenerate a bare linear chain
 * server-side, silently destroying `when: reject` edges.
 */
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

export function useStudioDraftGeneration({
  agentPrompt,
  workflowPrompt,
  setAgentDraft,
  setWorkflowDraft,
  generateAgent,
  generateWorkflow,
}: {
  agentPrompt: string;
  workflowPrompt: string;
  setAgentDraft: Dispatch<SetStateAction<AgentDraft>>;
  setWorkflowDraft: Dispatch<SetStateAction<WorkflowDraft>>;
  generateAgent: (prompt: string) => Promise<{
    slug: string;
    name: string;
    description: string;
    role_body: string;
    adapter: string;
    default_skill: string;
    mcp_tools: string[];
  }>;
  generateWorkflow: (prompt: string) => Promise<{
    slug: string;
    name: string;
    description: string;
    stages: StudioWorkflowStage[];
  }>;
}) {
  const generateAgentDraft = useMutation({
    meta: { errorTitle: "Generate agent draft" },
    mutationFn: () => generateAgent(agentPrompt),
    onSuccess: (generated) => {
      setAgentDraft((draft) => ({
        ...draft,
        slug: generated.slug || draft.slug,
        name: generated.name,
        description: generated.description,
        role_body: generated.role_body,
        adapter: generated.adapter || draft.adapter,
        default_skill: generated.default_skill || draft.default_skill,
        mcp_tools: generated.mcp_tools.length ? generated.mcp_tools : draft.mcp_tools,
        mcp_enabled: generated.mcp_tools.length ? true : draft.mcp_enabled,
      }));
    },
  });

  const generateWorkflowDraft = useMutation({
    meta: { errorTitle: "Generate workflow draft" },
    mutationFn: () => generateWorkflow(workflowPrompt),
    onSuccess: (generated) => {
      setWorkflowDraft({
        slug: generated.slug,
        name: generated.name,
        description: generated.description,
        stages: generated.stages.length ? generated.stages : [emptyStage(1)],
        transitions: [],
      });
    },
  });

  return { generateAgentDraft, generateWorkflowDraft };
}
