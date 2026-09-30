import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { WorkspaceCreateResponse } from "../../api/types";
import { INSTANCES_KEY } from "../../hooks/useLocalInstances";
import { describeError } from "../../state/toastStore";
import { AddWorkspaceModal, type AddWorkspaceDraft } from "../AddWorkspaceModal";

interface AddWorkspaceFlowProps {
  /** Every slug in use, archived ones included — a slug is unique across both. */
  existingSlugs: string[];
  onClose: () => void;
  onCreated: (created: WorkspaceCreateResponse) => void;
}

/**
 * The add-workspace dialog and the request behind it, for the Console and the
 * Workspaces page alike. Mount it to open it: each opening starts clean.
 *
 * Anything listing workspaces refreshes on success — the Console's list, the
 * Workspaces page's setup cards, and the workflow each workspace resolves.
 */
export function AddWorkspaceFlow({ existingSlugs, onClose, onCreated }: AddWorkspaceFlowProps) {
  const queryClient = useQueryClient();
  const workflowTemplates = useQuery({ queryKey: ["workflow-templates"], queryFn: api.workflowTemplates });
  const create = useMutation({
    meta: { errorTitle: "Create workspace" },
    mutationFn: (draft: AddWorkspaceDraft) =>
      api.createWorkspace({
        slug: draft.slug,
        name: draft.name,
        repo_path: draft.repo_path,
        workflow_template_slug: draft.workflow_template_slug,
        orchestration_profile_slug: draft.orchestration_profile_slug || undefined,
      }),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      void queryClient.invalidateQueries({ queryKey: ["workspace-workflow"] });
      void queryClient.invalidateQueries({ queryKey: INSTANCES_KEY });
      onCreated(created);
    },
  });

  return (
    <AddWorkspaceModal
      open
      templates={workflowTemplates.data ?? []}
      existingSlugs={existingSlugs}
      isSaving={create.isPending}
      errorMessage={
        create.error
          ? describeError(create.error, "Could not add the workspace")
          : workflowTemplates.error
            ? `Could not load workflow templates: ${describeError(workflowTemplates.error, "the request failed")}`
            : undefined
      }
      onClose={onClose}
      onCreate={(draft) => create.mutate(draft)}
    />
  );
}
