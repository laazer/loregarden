import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { WorkspaceCreateResponse } from "../../api/types";
import { INITIALIZABLE_REPOSITORY_STATES, type RepositoryState } from "../../api/workspaceRepositoryTypes";
import { INSTANCES_KEY, INTEGRATION_KEY } from "../../hooks/useLocalInstances";
import { saveGateSelection } from "../../lib/gateSelection";
import { describeError, pushToast, toastActionFailed } from "../../state/toastStore";
import { AddWorkspaceModal, type AddWorkspaceDraft } from "../AddWorkspaceModal";

interface AddWorkspaceFlowProps {
  /** Every slug in use, archived ones included — a slug is unique across both. */
  existingSlugs: string[];
  onClose: () => void;
  onCreated: (created: WorkspaceCreateResponse) => void;
}

interface CreateRequest {
  draft: AddWorkspaceDraft;
  repoState: RepositoryState | undefined;
  gateCommands: string[];
}

interface Created {
  workspace: WorkspaceCreateResponse;
  /** Set when the repository was created and something is still left to the operator. */
  followUp: string;
  /** Set when the workspace was added but creating its repository failed. */
  repositoryError: unknown;
  /** Set when the workspace was added but saving its chosen gates failed. */
  gatesError: unknown;
}

/** Run a step after the workspace exists; its failure is reported, never thrown past the add. */
async function afterAdd(step: () => Promise<unknown>): Promise<unknown> {
  try {
    await step();
    return undefined;
  } catch (error) {
    // silent-ok: returned to onSuccess, which toasts it; the workspace exists and its card retries the step
    return error;
  }
}

/**
 * The add-workspace dialog and the requests behind it, for the Console and the
 * Workspaces page alike. Mount it to open it: each opening starts clean.
 *
 * A path with nothing at it, or an empty folder, gets its repository created
 * right after the workspace is added. That second step failing does not undo
 * the first — the workspace exists and its card offers the repository again —
 * so it is reported as a toast after the dialog closes, not as a dialog error
 * the operator would "fix" by resubmitting a slug that is now taken.
 *
 * Anything listing workspaces refreshes on success — the Console's list, the
 * Workspaces page's setup cards, and the workflow each workspace resolves.
 */
/** What a new workspace makes stale — shared with the agent's workspace.create. */
export function refreshAfterWorkspaceAdded(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
  void queryClient.invalidateQueries({ queryKey: ["workspace-workflow"] });
  void queryClient.invalidateQueries({ queryKey: INSTANCES_KEY });
  void queryClient.invalidateQueries({ queryKey: INTEGRATION_KEY });
}

export function AddWorkspaceFlow({ existingSlugs, onClose, onCreated }: AddWorkspaceFlowProps) {
  const queryClient = useQueryClient();
  const workflowTemplates = useQuery({ queryKey: ["workflow-templates"], queryFn: api.workflowTemplates });
  const create = useMutation({
    meta: { errorTitle: "Create workspace" },
    mutationFn: async ({ draft, repoState, gateCommands }: CreateRequest): Promise<Created> => {
      const workspace = await api.createWorkspace({
        slug: draft.slug,
        name: draft.name,
        repo_path: draft.repo_path,
        workflow_template_slug: draft.workflow_template_slug,
        orchestration_profile_slug: draft.orchestration_profile_slug || undefined,
      });
      let followUp = "";
      const repositoryError =
        repoState && INITIALIZABLE_REPOSITORY_STATES.has(repoState)
          ? await afterAdd(async () => {
              followUp = (await api.createWorkspaceRepository(workspace.slug)).follow_up;
            })
          : undefined;
      const gatesError =
        gateCommands.length > 0 ? await afterAdd(() => saveGateSelection(workspace.slug, gateCommands, [])) : undefined;
      return { workspace, followUp, repositoryError, gatesError };
    },
    onSuccess: ({ workspace, followUp, repositoryError, gatesError }) => {
      refreshAfterWorkspaceAdded(queryClient);
      if (repositoryError) {
        toastActionFailed(`Added ${workspace.name}, but creating its repository`, repositoryError);
      } else if (followUp) {
        pushToast({ tone: "warning", title: `Created ${workspace.name}'s repository`, message: followUp });
      }
      if (gatesError) {
        toastActionFailed(`Added ${workspace.name}, but saving its toolchain gates`, gatesError);
      }
      onCreated(workspace);
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
      onCreate={(draft, repoState, gateCommands) => {
        if (!create.isPending) create.mutate({ draft, repoState, gateCommands });
      }}
    />
  );
}
