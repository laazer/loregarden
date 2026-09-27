import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { localInstancesApi } from "../../api/localInstancesApi";
import type { TemplateSpec, WorkspaceTemplateEntry, WorkspaceTemplates } from "../../api/localInstancesTypes";
import { describeError, pushToast, toastActionFailed } from "../../state/toastStore";
import { INSTANCES_KEY } from "../../hooks/useLocalInstances";
import { TemplateEditor } from "./TemplateEditor";

const ORIGIN_LABEL = { code: "built in", file: "repo file", stored: "saved here" } as const;

function statusOf(entry: WorkspaceTemplateEntry): string {
  if (entry.shadowed_by) return `Not used: the ${ORIGIN_LABEL[entry.shadowed_by]} defines ${entry.name} too`;
  if (entry.error) return `Invalid: ${entry.error}`;
  return "Launchable";
}

interface WorkspaceTemplatesPanelProps {
  workspace: WorkspaceTemplates;
}

type Editing = { mode: "new" } | { mode: "edit"; spec: TemplateSpec } | null;

/**
 * One workspace's templates: where each comes from, and whether it can launch.
 *
 * Only templates saved here are edited here. The repo file is code — change it
 * in the repo, where it is reviewed — and the built-in ones are loregarden's
 * own. Its path is shown so there is no guessing where to look.
 */
export function WorkspaceTemplatesPanel({ workspace }: WorkspaceTemplatesPanelProps) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<Editing>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  const refresh = () => queryClient.invalidateQueries({ queryKey: INSTANCES_KEY });

  const save = useMutation({
    mutationFn: (spec: TemplateSpec) =>
      editing?.mode === "edit"
        ? localInstancesApi.replaceTemplate(workspace.slug, spec)
        : localInstancesApi.createTemplate(workspace.slug, spec),
    onSuccess: (_, spec) => {
      pushToast({ tone: "success", title: `Saved ${workspace.slug}/${spec.name}` });
      setEditing(null);
      setSaveError(null);
      void refresh();
    },
    onError: (error) => setSaveError(describeError(error, "Could not save the template")),
  });

  const remove = (entry: WorkspaceTemplateEntry) => {
    if (!window.confirm(`Delete the saved template ${entry.qualified_name}? Running instances keep running.`)) return;
    setDeleting(entry.name);
    localInstancesApi
      .deleteTemplate(workspace.slug, entry.name)
      .then(refresh)
      .catch((error: unknown) => toastActionFailed(`Delete ${entry.qualified_name}`, error))
      .finally(() => setDeleting(null));
  };

  return (
    <section className="instances-templates" aria-labelledby={`templates-${workspace.slug}`}>
      <header className="instances-section-head">
        <h3 id={`templates-${workspace.slug}`}>{workspace.name} templates</h3>
        <button
          type="button"
          className="btn-secondary"
          disabled={editing !== null}
          onClick={() => {
            setSaveError(null);
            setEditing({ mode: "new" });
          }}
        >
          New template
        </button>
      </header>

      <p className="modal-hint">
        Repo file: <code>{workspace.template_file}</code>
      </p>
      {workspace.file_error && (
        <p className="instances-error" role="alert">
          The repo file cannot be used: {workspace.file_error}
        </p>
      )}
      {workspace.conflicts.map((conflict) => (
        <p key={conflict} className="instances-warning">
          {conflict}
        </p>
      ))}

      {editing && (
        <TemplateEditor
          initial={editing.mode === "edit" ? editing.spec : undefined}
          saving={save.isPending}
          error={saveError}
          onSave={(spec) => save.mutate(spec)}
          onCancel={() => {
            setEditing(null);
            setSaveError(null);
          }}
        />
      )}

      {workspace.entries.length === 0 ? (
        <p className="modal-hint">
          No templates yet. Commit <code>.loregarden/instances.yaml</code> to the repo, or create one here with New
          template.
        </p>
      ) : (
        <table className="instances-table">
          <caption className="visually-hidden">Templates for {workspace.name}</caption>
          <thead>
            <tr>
              <th scope="col">Template</th>
              <th scope="col">Source</th>
              <th scope="col">Kind</th>
              <th scope="col">Status</th>
              <th scope="col">
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {workspace.entries.map((entry) => (
              <tr key={`${entry.origin}:${entry.name}`}>
                <td>
                  <strong>{entry.name}</strong>
                  {entry.description && <div className="instances-meta">{entry.description}</div>}
                </td>
                <td>{ORIGIN_LABEL[entry.origin]}</td>
                <td>{entry.kind ?? "—"}</td>
                <td className={entry.launchable ? undefined : "instances-warning"}>{statusOf(entry)}</td>
                <td className="instances-actions">
                  {entry.origin === "stored" && (
                    <>
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={!entry.spec || editing !== null}
                        title={entry.spec ? undefined : "This saved template no longer validates; delete it and create it again."}
                        onClick={() => entry.spec && setEditing({ mode: "edit", spec: entry.spec })}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={deleting === entry.name}
                        aria-busy={deleting === entry.name}
                        onClick={() => remove(entry)}
                      >
                        {deleting === entry.name ? "Deleting…" : "Delete"}
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
