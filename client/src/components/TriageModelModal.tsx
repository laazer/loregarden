import { IconCloseButton } from "./IconCloseButton";

import { useEffect, useState } from "react";

import type { RuntimeOptions, WorkspaceRuntimeSettings } from "../api/client";
import { WorkspaceRuntimeFields, runtimeSettingsEqual } from "./WorkspaceRuntimeFields";

interface TriageModelModalProps {
  open: boolean;
  runtime: WorkspaceRuntimeSettings;
  runtimeOptions: RuntimeOptions | undefined;
  isSaving: boolean;
  onClose: () => void;
  onSave: (runtime: WorkspaceRuntimeSettings) => Promise<void>;
}

export function TriageModelModal({
  open,
  runtime,
  runtimeOptions,
  isSaving,
  onClose,
  onSave,
}: TriageModelModalProps) {
  const [draft, setDraft] = useState<WorkspaceRuntimeSettings>(runtime);

  useEffect(() => {
    if (!open) return;
    setDraft(runtime);
  }, [open, runtime]);

  if (!open) return null;

  const dirty = !runtimeSettingsEqual(draft, runtime);

  const handleSave = async () => {
    if (!runtimeOptions) return;
    await onSave(draft);
    onClose();
  };

  return (
    <>
      <div className="modal-overlay" onClick={isSaving ? undefined : onClose} role="presentation" />
      <div className="modal-panel" role="dialog" aria-labelledby="triage-model-modal-title">
        <div className="modal-header">
          <div>
            <div className="state-label">Triage</div>
            <h2 id="triage-model-modal-title" className="modal-title">
              Model settings
            </h2>
            <p className="modal-subtitle">Choose a provider, then pick a model for this ticket</p>
          </div>
          <IconCloseButton disabled={isSaving} onClick={onClose} />
        </div>

        <div className="modal-body">
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
            {isSaving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </>
  );
}
