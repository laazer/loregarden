import { useEffect, useState } from "react";
import type { WorkflowTemplateSummary } from "../api/client";
import { slugify } from "../lib/slugify";
import { RepoPathExplorer } from "./RepoPathExplorer";

export interface AddWorkspaceDraft {
  name: string;
  slug: string;
  repo_path: string;
  workflow_template_slug: string;
  orchestration_profile_slug: string;
}

interface AddWorkspaceModalProps {
  open: boolean;
  templates: WorkflowTemplateSummary[];
  existingSlugs: string[];
  isSaving: boolean;
  errorMessage?: string;
  onClose: () => void;
  onCreate: (draft: AddWorkspaceDraft) => void | Promise<void>;
}

const DEFAULT_TEMPLATE = "loregarden-tdd";

export function AddWorkspaceModal({
  open,
  templates,
  existingSlugs,
  isSaving,
  errorMessage,
  onClose,
  onCreate,
}: AddWorkspaceModalProps) {
  const [draft, setDraft] = useState<AddWorkspaceDraft>({
    name: "",
    slug: "",
    repo_path: ".",
    workflow_template_slug: DEFAULT_TEMPLATE,
    orchestration_profile_slug: "",
  });
  const [slugTouched, setSlugTouched] = useState(false);

  useEffect(() => {
    if (!open) return;
    const defaultTemplate =
      templates.find((t) => t.slug === DEFAULT_TEMPLATE)?.slug ?? templates[0]?.slug ?? DEFAULT_TEMPLATE;
    setDraft({
      name: "",
      slug: "",
      repo_path: ".",
      workflow_template_slug: defaultTemplate,
      orchestration_profile_slug: "",
    });
    setSlugTouched(false);
  }, [open, templates]);

  useEffect(() => {
    if (slugTouched) return;
    setDraft((d) => ({ ...d, slug: slugify(d.name) }));
  }, [draft.name, slugTouched]);

  if (!open) return null;

  const slugConflict = draft.slug.length > 0 && existingSlugs.includes(draft.slug);
  const canSubmit =
    draft.name.trim().length > 0 &&
    draft.slug.length > 0 &&
    !slugConflict &&
    draft.workflow_template_slug.length > 0;

  const handleCreate = () => {
    if (!canSubmit) return;
    void onCreate({
      ...draft,
      name: draft.name.trim(),
      slug: draft.slug.trim(),
      repo_path: draft.repo_path.trim() || ".",
      orchestration_profile_slug: draft.orchestration_profile_slug.trim(),
    });
  };

  return (
    <>
      <div className="modal-overlay" onClick={isSaving ? undefined : onClose} role="presentation" />
      <div className="modal-panel" role="dialog" aria-labelledby="add-workspace-title">
        <div className="modal-header">
          <div>
            <div className="state-label">Workspaces</div>
            <h2 id="add-workspace-title" className="modal-title">
              Add workspace
            </h2>
            <p className="modal-subtitle">Register a repo and workflow template for a new project</p>
          </div>
          <button type="button" className="btn-secondary" disabled={isSaving} onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="modal-body">
          {errorMessage && (
            <p className="modal-hint" style={{ color: "var(--rdl)" }}>
              {errorMessage}
            </p>
          )}

          <div className="modal-field">
            <div className="modal-field-label">Name</div>
            <input
              className="btn-secondary filter-select"
              style={{ width: "100%", fontSize: 12 }}
              value={draft.name}
              disabled={isSaving}
              placeholder="Blobert"
              autoFocus
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
            />
          </div>

          <div className="modal-field">
            <div className="modal-field-label">Slug</div>
            <input
              className="btn-secondary filter-select"
              style={{ width: "100%", fontSize: 12, fontFamily: "var(--mono)" }}
              value={draft.slug}
              disabled={isSaving}
              placeholder="blobert"
              onChange={(e) => {
                setSlugTouched(true);
                setDraft((d) => ({ ...d, slug: slugify(e.target.value) }));
              }}
            />
            {slugConflict && (
              <p className="modal-hint" style={{ color: "var(--rdl)", marginTop: 6 }}>
                A workspace with this slug already exists.
              </p>
            )}
          </div>

          <div className="modal-field">
            <div className="modal-field-label">Repo path</div>
            <input
              className="btn-secondary filter-select"
              style={{ width: "100%", fontSize: 12, fontFamily: "var(--mono)" }}
              value={draft.repo_path}
              disabled={isSaving}
              placeholder="."
              onChange={(e) => setDraft((d) => ({ ...d, repo_path: e.target.value }))}
            />
            <RepoPathExplorer
              value={draft.repo_path}
              onChange={(repo_path) => setDraft((d) => ({ ...d, repo_path }))}
              disabled={isSaving}
            />
          </div>

          <div className="modal-field">
            <div className="modal-field-label">Workflow template</div>
            <select
              className="btn-secondary filter-select"
              style={{ width: "100%", fontSize: 12 }}
              value={draft.workflow_template_slug}
              disabled={isSaving || templates.length === 0}
              onChange={(e) => setDraft((d) => ({ ...d, workflow_template_slug: e.target.value }))}
            >
              {templates.length === 0 ? (
                <option value="">No templates available</option>
              ) : (
                templates.map((t) => (
                  <option key={t.slug} value={t.slug}>
                    {t.name} ({t.stage_count} stages)
                  </option>
                ))
              )}
            </select>
          </div>

          <div className="modal-field">
            <div className="modal-field-label">Orchestration profile (optional)</div>
            <input
              className="btn-secondary filter-select"
              style={{ width: "100%", fontSize: 12, fontFamily: "var(--mono)" }}
              value={draft.orchestration_profile_slug}
              disabled={isSaving}
              placeholder="blobert"
              onChange={(e) =>
                setDraft((d) => ({ ...d, orchestration_profile_slug: e.target.value.trim() }))
              }
            />
            <p className="modal-hint" style={{ marginTop: 6 }}>
              YAML stem under agent_context/orchestration. Leave blank to auto-resolve from slug.
            </p>
          </div>
        </div>

        <div className="modal-footer">
          <button type="button" className="btn-secondary" disabled={isSaving} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn-primary" disabled={isSaving || !canSubmit} onClick={handleCreate}>
            {isSaving ? "Creating…" : "Create workspace"}
          </button>
        </div>
      </div>
    </>
  );
}
