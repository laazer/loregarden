import { IconCloseButton } from "./IconCloseButton";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type WorkflowTemplateSummary } from "../api/client";
import { INITIALIZABLE_REPOSITORY_STATES, type RepositoryProbe, type RepositoryState } from "../api/workspaceRepositoryTypes";
import { useDebounced } from "../hooks/useDebounced";
import { defaultGateSelection } from "../lib/gateSelection";
import { GatePresetPicker } from "./workspaces/GatePresetPicker";
import { describeError } from "../state/toastStore";
import { slugify } from "../lib/slugify";
import { RepoPathExplorer } from "./RepoPathExplorer";
import { useDialogDismiss } from "../hooks/useDialogDismiss";
import { useDialogFocusTrap } from "../hooks/useDialogFocusTrap";

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
  /**
   * `repoState` is what the probe last said about the path, or undefined if it could not say;
   * `gateCommands` the toolchain gates ticked, to add to the workspace's profile.
   */
  onCreate: (
    draft: AddWorkspaceDraft,
    repoState: RepositoryState | undefined,
    gateCommands: string[],
  ) => void | Promise<void>;
}

const DEFAULT_TEMPLATE = "loregarden-tdd";
const PROBE_DEBOUNCE_MS = 300;

/** Paths a workspace cannot use; the probe's `detail` says why and what to pick instead. */
const UNUSABLE: ReadonlySet<RepositoryState> = new Set(["not_a_repository", "inside_repository"]);

const PROBE_SUMMARY: Record<RepositoryState, string> = {
  repository: "Git repository — it will be registered as is.",
  missing: "Nothing here yet — a new git repository will be created, with loregarden's gates and AGENTS.md committed.",
  empty: "Empty folder — it will be initialized as a git repository, with loregarden's gates and AGENTS.md committed.",
  not_a_repository: "Cannot use this path.",
  inside_repository: "Cannot use this path.",
};

const isAbsolute = (path: string) => path.startsWith("/");

function RepoPathStatus({ path, probe }: { path: string; probe: ReturnType<typeof useRepositoryProbe> }) {
  if (!path) return null;
  if (!isAbsolute(path)) {
    return <p className="modal-hint" style={{ color: "var(--rdl)", marginTop: 6 }}>Enter an absolute path, starting with /.</p>;
  }
  if (probe.isPending) return null;
  if (probe.error) {
    return (
      <p className="modal-hint" style={{ color: "var(--rdl)", marginTop: 6 }}>
        Could not check this path: {describeError(probe.error, "the request failed")}. The workspace can still be
        added; its card will say what the path needs.
      </p>
    );
  }
  const { state, detail } = probe.data;
  return (
    <p className="modal-hint" style={{ marginTop: 6, color: UNUSABLE.has(state) ? "var(--rdl)" : undefined }}>
      {PROBE_SUMMARY[state]}
      {UNUSABLE.has(state) && <> {detail}</>}
    </p>
  );
}

function useRepositoryProbe(path: string) {
  const settled = useDebounced(path, PROBE_DEBOUNCE_MS);
  return useQuery<RepositoryProbe>({
    queryKey: ["repository-probe", settled],
    queryFn: () => api.probeRepository(settled),
    enabled: isAbsolute(settled) && settled === path,
    // A path's state changes under it (a clone, a mkdir); always ask again.
    staleTime: 0,
    meta: { suppressErrorToast: true },
  });
}

export function AddWorkspaceModal({
  open,
  templates,
  existingSlugs,
  isSaving,
  errorMessage,
  onClose,
  onCreate,
}: AddWorkspaceModalProps) {
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  // Escape and the backdrop agree on purpose: whatever makes a click
  // dismiss this dialog is what makes the key dismiss it.
  useDialogDismiss(!open ? null : isSaving ? undefined : onClose);
  const [draft, setDraft] = useState<AddWorkspaceDraft>({
    name: "",
    slug: "",
    repo_path: "",
    workflow_template_slug: DEFAULT_TEMPLATE,
    orchestration_profile_slug: "",
  });
  const [slugTouched, setSlugTouched] = useState(false);

  // Reset on open only. Keyed on `templates` too, a list that arrived after the
  // operator started typing wiped the draft — and a caller passing `data ?? []`
  // handed a fresh array every render, so the reset re-rendered forever.
  useEffect(() => {
    if (!open) return;
    setDraft({
      name: "",
      slug: "",
      repo_path: "",
      workflow_template_slug: DEFAULT_TEMPLATE,
      orchestration_profile_slug: "",
    });
    setSlugTouched(false);
  }, [open]);

  // Once the list is known, move off a template it does not offer; a choice it
  // does offer is kept, so this touches nothing the operator picked.
  useEffect(() => {
    if (templates.length === 0) return;
    setDraft((d) =>
      templates.some((t) => t.slug === d.workflow_template_slug)
        ? d
        : {
            ...d,
            workflow_template_slug:
              templates.find((t) => t.slug === DEFAULT_TEMPLATE)?.slug ?? templates[0].slug,
          },
    );
  }, [templates]);

  useEffect(() => {
    if (slugTouched) return;
    setDraft((d) => ({ ...d, slug: slugify(d.name) }));
  }, [draft.name, slugTouched]);

  const repoPath = draft.repo_path.trim();
  const probe = useRepositoryProbe(repoPath);
  const probedState = probe.isFetching ? undefined : probe.data?.state;
  const presets = useQuery({
    queryKey: ["gate-presets", "path", repoPath],
    queryFn: () => api.gatePresetsForPath(repoPath),
    enabled: open && probedState !== undefined && !UNUSABLE.has(probedState),
    meta: { suppressErrorToast: true },
  });
  const [gateSelection, setGateSelection] = useState<ReadonlySet<string>>(new Set());
  // A new path is a new repository: start from what it detects, not the last path's ticks.
  useEffect(() => {
    setGateSelection(defaultGateSelection(presets.data));
  }, [presets.data]);

  if (!open) return null;

  // Only an answer for this exact path, settled: the key follows the debounced path, so a
  // keystroke since leaves the query pending, and a refetch may be answering for a changed disk.
  const repoState = probedState;
  const probing = isAbsolute(repoPath) && (probe.isPending || probe.isFetching) && !probe.error;
  const slugConflict = draft.slug.length > 0 && existingSlugs.includes(draft.slug);
  const canSubmit =
    draft.name.trim().length > 0 &&
    draft.slug.length > 0 &&
    !slugConflict &&
    draft.workflow_template_slug.length > 0 &&
    isAbsolute(repoPath) &&
    !probing &&
    !(repoState && UNUSABLE.has(repoState));
  const initializing = repoState !== undefined && INITIALIZABLE_REPOSITORY_STATES.has(repoState);

  const handleCreate = () => {
    if (!canSubmit) return;
    void onCreate(
      {
        ...draft,
        name: draft.name.trim(),
        slug: draft.slug.trim(),
        repo_path: repoPath,
        orchestration_profile_slug: draft.orchestration_profile_slug.trim(),
      },
      probe.error ? undefined : repoState,
      [...gateSelection],
    );
  };

  return (
    <>
      <div className="modal-overlay" onClick={isSaving ? undefined : onClose} role="presentation" />
      <div
        ref={dialogRef}
        className="modal-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-workspace-title"
      >
        <div className="modal-header">
          <div>
            <div className="state-label">Workspaces</div>
            <h2 id="add-workspace-title" className="modal-title">
              Add workspace
            </h2>
            <p className="modal-subtitle">Register a repository — or create one — and pick its workflow template</p>
          </div>
          <IconCloseButton disabled={isSaving} onClick={onClose} />
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
              aria-label="Name"
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
              aria-label="Slug"
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
              aria-label="Repo path"
              placeholder="/Users/you/workspace/project"
              onChange={(e) => setDraft((d) => ({ ...d, repo_path: e.target.value }))}
            />
            <div aria-live="polite">
              <RepoPathStatus path={repoPath} probe={probe} />
            </div>
            <RepoPathExplorer
              explorerKey="workspace-repo"
              value={draft.repo_path}
              startPath={draft.repo_path || "."}
              onChange={(repo_path) => setDraft((d) => ({ ...d, repo_path }))}
              disabled={isSaving}
              absolutePaths
            />
          </div>

          {repoState !== undefined && !UNUSABLE.has(repoState) && (
            <div className="modal-field">
              <div className="modal-field-label">Toolchain gates</div>
              <p className="modal-hint" style={{ marginTop: 0 }}>
                Checks every stage transition runs, beside loregarden's own guardrails. Change them later on the
                workspace's card.
              </p>
              {presets.isPending ? (
                <div className="local-instances-skeleton" aria-label="Looking for toolchains" />
              ) : presets.error ? (
                <p className="modal-hint" style={{ color: "var(--rdl)" }}>
                  Could not look for toolchains: {describeError(presets.error, "the request failed")}. The workspace
                  can still be added; pick gates on its card.
                </p>
              ) : (
                <GatePresetPicker
                  presets={presets.data}
                  selected={gateSelection}
                  disabled={isSaving}
                  idPrefix="add-workspace-gates"
                  onToggle={(command, on) =>
                    setGateSelection((current) => {
                      const next = new Set(current);
                      if (on) next.add(command);
                      else next.delete(command);
                      return next;
                    })
                  }
                />
              )}
            </div>
          )}

          <div className="modal-field">
            <div className="modal-field-label">Workflow template</div>
            <select
              className="btn-secondary filter-select"
              style={{ width: "100%", fontSize: 12 }}
              value={draft.workflow_template_slug}
              disabled={isSaving || templates.length === 0}
              aria-label="Workflow template"
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
              aria-label="Orchestration profile"
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
            {isSaving ? "Creating…" : probing ? "Checking path…" : initializing ? "Create workspace and repository" : "Create workspace"}
          </button>
        </div>
      </div>
    </>
  );
}
