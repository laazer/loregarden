import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "../../api/client";
import { localInstancesApi } from "../../api/localInstancesApi";
import type { WorkspaceSummary } from "../../api/types";
import { WORKSPACE_TEMPLATES_KEY } from "../../hooks/useLocalInstances";
import { useAgentAction } from "../../lib/agentActions/useAgentAction";
import { describeError, pushToast, toastActionFailed } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { AddWorkspaceFlow, refreshAfterWorkspaceAdded } from "./AddWorkspaceFlow";
import { WorkspacesTable } from "./WorkspacesTable";
import "./WorkspacesTable.css";

const WORKSPACES_KEY = ["workspaces"] as const;

/**
 * Every workspace loregarden knows, and what each still needs.
 *
 * Answers "which of my repositories needs something from loregarden, and what
 * fixes it?" as a table whose Needs column carries the fix beside each
 * problem. Adding a workspace starts here. Archived workspaces fold into their
 * own table so the active ones stay short.
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

  // The same mutation the row's Archive/Restore menu item runs. The agent's
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
  // makes. Its repository is a separate action on the new row.
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

  const onArchive = (w: (typeof all)[number], next: boolean) => {
    if (archive.isPending) return;
    if (next && !window.confirm(`Archive ${w.name}? It moves to the archived list; nothing else changes.`)) return;
    archive.mutate({ slug: w.slug, archived: next });
  };
  const archivingSlug = archive.isPending ? (archive.variables?.slug ?? null) : null;

  const failed = setups.error ?? summaries.error;

  return (
    <section className="instances-template-list" aria-labelledby="workspaces-active-title" aria-busy={setups.isPending}>
      <header className="instances-section-head">
        <h2 id="workspaces-active-title">
          Active workspaces {setups.data && <span className="instances-count">{active.length}</span>}
        </h2>
        <Button variant="primary" onClick={() => setAdding(true)} aria-expanded={adding}>
          Add workspace…
        </Button>
      </header>
      <p className="instances-meta">
        A workspace&rsquo;s name opens its dashboard. Each need carries its fix: a missing or outdated AGENTS.md or gate
        block opens a pull request in that repository, and never edits its checkout. ⋯ → Set up… installs in place,
        and edits gate presets and launch templates.
      </p>

      {failed && (
        <div className="instances-error" role="alert">
          Could not load workspaces: {describeError(failed, "the request failed")}.{" "}
          <Button
            variant="secondary"
            onClick={() => {
              void setups.refetch();
              void summaries.refetch();
            }}
          >
            Try again
          </Button>
        </div>
      )}
      {setups.isPending && !setups.error ? (
        <div className="local-instances-skeleton" aria-label="Loading workspaces" />
      ) : setups.data && all.length === 0 ? (
        <p className="modal-hint">No workspaces yet. Add one to point loregarden at a repository.</p>
      ) : setups.data && active.length === 0 ? (
        <p className="modal-hint">Every workspace is archived. Restore one below, or add a new one.</p>
      ) : (
        <WorkspacesTable
          workspaces={active}
          summaries={bySlug}
          onArchive={onArchive}
          archivingSlug={archivingSlug}
          caption="Active workspaces"
        />
      )}

      {archived.length > 0 && (
        <details className="workspaces-archived">
          <summary>
            Archived <span className="instances-count">{archived.length}</span>
          </summary>
          <WorkspacesTable
            workspaces={archived}
            summaries={bySlug}
            onArchive={onArchive}
            archivingSlug={archivingSlug}
            caption="Archived workspaces"
          />
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
              message: "Its row below says what it needs from loregarden, with the fix beside each.",
            });
          }}
        />
      )}
    </section>
  );
}
