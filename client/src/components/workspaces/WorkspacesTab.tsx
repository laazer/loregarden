import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "../../api/client";
import { localInstancesApi } from "../../api/localInstancesApi";
import type { WorkspaceSummary } from "../../api/types";
import { WORKSPACE_TEMPLATES_KEY } from "../../hooks/useLocalInstances";
import { useAgentAction } from "../../lib/agentActions/useAgentAction";
import { describeError, pushToast, toastActionFailed } from "../../state/toastStore";
import { WorkspaceSetupCard } from "../instances/WorkspaceSetupCard";
import { AddWorkspaceFlow, refreshAfterWorkspaceAdded } from "./AddWorkspaceFlow";

const WORKSPACES_KEY = ["workspaces"] as const;

/**
 * Every workspace loregarden knows, and what each still needs.
 *
 * Answers "which workspaces are set up, and what is missing?" — each card's
 * chips say so folded; opening one installs gates and agent instructions, edits
 * launch templates, or archives it. Adding a workspace starts here. Archived
 * workspaces fold into their own list so the active ones stay short.
 */
export function WorkspacesTab() {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const setups = useQuery({ queryKey: WORKSPACE_TEMPLATES_KEY, queryFn: localInstancesApi.workspaceTemplates });
  const summaries = useQuery({ queryKey: WORKSPACES_KEY, queryFn: api.workspaces });

  const archive = useMutation({
    // Its own toast names archive or restore; the global one would say "Action failed" beside it.
    meta: { suppressErrorToast: true },
    mutationFn: ({ slug, archived }: { slug: string; archived: boolean }) =>
      archived ? api.archiveWorkspace(slug) : api.restoreWorkspace(slug),
    onSuccess: (updated) => {
      queryClient.setQueryData<WorkspaceSummary[]>(WORKSPACES_KEY, (rows) =>
        rows?.map((row) => (row.slug === updated.slug ? updated : row)),
      );
      void queryClient.invalidateQueries({ queryKey: WORKSPACES_KEY });
      pushToast({ tone: "success", title: `${updated.archived_at ? "Archived" : "Restored"} ${updated.name}` });
    },
    onError: (error, { archived }) => toastActionFailed(archived ? "Archive workspace" : "Restore workspace", error),
  });

  const bySlug = useMemo(() => new Map((summaries.data ?? []).map((row) => [row.slug, row])), [summaries.data]);

  // The same mutation the card's Archive/Restore button runs. The agent's
  // approval stands in for the button's confirm dialog.
  const agentArchive = (slug: string, next: boolean) => {
    const row = bySlug.get(slug);
    if (!row) throw new Error(`no workspace named ${slug}`);
    if (Boolean(row.archived_at) === next) throw new Error(`${slug} is already ${next ? "archived" : "active"}`);
    return archive.mutateAsync({ slug, archived: next }).then((updated) => ({
      workspace_slug: updated.slug,
      archived: Boolean(updated.archived_at),
    }));
  };
  useAgentAction("workspace.archive", async ({ workspace_slug }) => agentArchive(workspace_slug, true));
  useAgentAction("workspace.restore", async ({ workspace_slug }) => agentArchive(workspace_slug, false));

  // The workspace record only — the same request the Add flow's first step
  // makes. Its repository is a separate action on the new card.
  useAgentAction("workspace.create", async ({ slug, name, repo_path, workflow_template_slug }) => {
    if (bySlug.has(slug)) throw new Error(`a workspace named ${slug} already exists`);
    const created = await api.createWorkspace({ slug, name, repo_path, workflow_template_slug });
    refreshAfterWorkspaceAdded(queryClient);
    void queryClient.invalidateQueries({ queryKey: WORKSPACE_TEMPLATES_KEY });
    pushToast({ tone: "success", title: `Added ${created.name}` });
    return { workspace_slug: created.slug };
  });
  const all = setups.data ?? [];
  const active = all.filter((w) => !bySlug.get(w.slug)?.archived_at);
  const archived = all.filter((w) => bySlug.get(w.slug)?.archived_at);

  const card = (w: (typeof all)[number]) => (
    <WorkspaceSetupCard
      key={w.slug}
      workspace={w}
      summary={bySlug.get(w.slug)}
      archiving={archive.isPending && archive.variables?.slug === w.slug}
      onArchive={(next) => {
        if (archive.isPending) return;
        if (next && !window.confirm(`Archive ${w.name}? It moves to the archived list; nothing else changes.`)) return;
        archive.mutate({ slug: w.slug, archived: next });
      }}
    />
  );

  const failed = setups.error ?? summaries.error;

  return (
    <section className="instances-template-list" aria-labelledby="workspaces-active-title" aria-busy={setups.isPending}>
      <header className="instances-section-head">
        <h2 id="workspaces-active-title">
          Active workspaces {setups.data && <span className="instances-count">{active.length}</span>}
        </h2>
        <button
          type="button"
          className="btn-primary"
          onClick={() => setAdding(true)}
        >
          Add workspace
        </button>
      </header>
      <p className="instances-meta">
        What each workspace has of loregarden — gates, agent instructions, launch templates. Open one to install, edit
        or archive it.
      </p>

      {failed && (
        <div className="instances-error" role="alert">
          Could not load workspaces: {describeError(failed, "the request failed")}.{" "}
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              void setups.refetch();
              void summaries.refetch();
            }}
          >
            Try again
          </button>
        </div>
      )}
      {setups.isPending && !setups.error ? (
        <div className="local-instances-skeleton" aria-label="Loading workspaces" />
      ) : setups.data && all.length === 0 ? (
        <p className="modal-hint">No workspaces yet. Add one to point loregarden at a repository.</p>
      ) : setups.data && active.length === 0 ? (
        <p className="modal-hint">Every workspace is archived. Restore one below, or add a new one.</p>
      ) : (
        <ul className="instances-setup-list">{active.map(card)}</ul>
      )}

      {archived.length > 0 && (
        <details className="workspaces-archived">
          <summary>
            Archived <span className="instances-count">{archived.length}</span>
          </summary>
          <ul className="instances-setup-list">{archived.map(card)}</ul>
        </details>
      )}

      {adding && (
        <AddWorkspaceFlow
          existingSlugs={(summaries.data ?? []).map((w) => w.slug)}
          onClose={() => setAdding(false)}
          onCreated={(created) => {
            setAdding(false);
            pushToast({
              tone: "success",
              title: `Added ${created.name}`,
              message: "Its card below shows what it has of loregarden, and installs anything missing.",
            });
          }}
        />
      )}
    </section>
  );
}
