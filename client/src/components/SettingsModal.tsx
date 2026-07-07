import { useEffect, useState } from "react";

import { IconCloseButton } from "./IconCloseButton";

import type { RuntimeOptions, WorkspaceRuntimeSettings, WorkspaceSummary } from "../api/client";
import {
  WorkspaceRuntimeFields,
  runtimeFromWorkspace,
  runtimeSettingsEqual,
} from "./WorkspaceRuntimeFields";

interface SettingsModalProps {
  open: boolean;
  workspaceSlug: string;
  workspaces: WorkspaceSummary[];
  runtimeOptions: RuntimeOptions | undefined;
  isSaving: boolean;
  onClose: () => void;
  onWorkspaceChange: (slug: string) => void;
  onSave: (slug: string, runtime: WorkspaceRuntimeSettings) => Promise<void>;
}

export function SettingsModal({
  open,
  workspaceSlug,
  workspaces,
  runtimeOptions,
  isSaving,
  onClose,
  onWorkspaceChange,
  onSave,
}: SettingsModalProps) {
  const workspace = workspaces.find((w) => w.slug === workspaceSlug);
  const [draft, setDraft] = useState<WorkspaceRuntimeSettings>(() => runtimeFromWorkspace(workspace));

  useEffect(() => {
    if (!open) return;
    setDraft(runtimeFromWorkspace(workspace));
  }, [open, workspace?.cli_adapter, workspace?.claude_model, workspace?.cursor_model, workspace?.lmstudio_base_url, workspace?.lmstudio_model, workspaceSlug]);

  if (!open) return null;

  const saved = runtimeFromWorkspace(workspace);
  const dirty = !runtimeSettingsEqual(draft, saved);

  const handleSave = async () => {
    if (!workspaceSlug || !runtimeOptions) return;
    await onSave(workspaceSlug, draft);
    onClose();
  };

  return (
    <>
      <div className="modal-overlay" onClick={isSaving ? undefined : onClose} role="presentation" />
      <div className="modal-panel" role="dialog" aria-labelledby="settings-modal-title">
        <div className="modal-header">
          <div>
            <div className="state-label">Workspace</div>
            <h2 id="settings-modal-title" className="modal-title">
              Agent runtime
            </h2>
            <p className="modal-subtitle">Default provider and model for stage agent runs</p>
          </div>
          <IconCloseButton disabled={isSaving} onClick={onClose} />
        </div>

        <div className="modal-body">
          {workspaces.length > 1 && (
            <div className="modal-field">
              <div className="modal-field-label">Workspace</div>
              <select
                className="btn-secondary filter-select"
                style={{ width: "100%", fontSize: 12 }}
                value={workspaceSlug}
                disabled={isSaving}
                onChange={(e) => onWorkspaceChange(e.target.value)}
              >
                {workspaces.map((w) => (
                  <option key={w.id} value={w.slug}>
                    {w.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          {runtimeOptions ? (
            <WorkspaceRuntimeFields
              runtime={draft}
              options={runtimeOptions}
              disabled={isSaving}
              onChange={setDraft}
            />
          ) : (
            <p className="modal-hint">Loading runtime options…</p>
          )}

          <p className="modal-hint" style={{ marginTop: 4 }}>
            Workspace default uses each agent&apos;s registry CLI. Choose Claude, Cursor, or LM Studio to
            override for all stage runs in this workspace.
          </p>
        </div>

        <div className="modal-footer">
          <button type="button" className="btn-secondary" disabled={isSaving} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={isSaving || !runtimeOptions || !dirty}
            onClick={() => void handleSave()}
          >
            {isSaving ? "Saving…" : "Save settings"}
          </button>
        </div>
      </div>
    </>
  );
}
