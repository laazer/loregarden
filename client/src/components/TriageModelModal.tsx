import { IconCloseButton } from "./IconCloseButton";

import { useEffect, useState } from "react";

import type { RuntimeOptions, WorkspaceRuntimeSettings } from "../api/client";
import { WorkspaceRuntimeFields, runtimeSettingsEqual } from "./WorkspaceRuntimeFields";
import { describeError } from "../state/toastStore";
import { Button } from "./ui/Button";
import { ModalShell } from "./ui/ModalShell";

interface TriageModelModalProps {
  open: boolean;
  runtime: WorkspaceRuntimeSettings;
  runtimeOptions: RuntimeOptions | undefined;
  /** Why `runtimeOptions` never arrived; without it the body reads "Loading" forever. */
  runtimeOptionsError?: unknown;
  isSaving: boolean;
  onClose: () => void;
  onSave: (runtime: WorkspaceRuntimeSettings) => Promise<void>;
  scopeLabel?: string;
  subtitle?: string;
}

export function TriageModelModal({
  open,
  runtime,
  runtimeOptions,
  runtimeOptionsError,
  isSaving,
  onClose,
  onSave,
  scopeLabel = "Triage",
  subtitle = "Choose a provider, then pick a model for this ticket",
}: TriageModelModalProps) {
  const [draft, setDraft] = useState<WorkspaceRuntimeSettings>(runtime);

  useEffect(() => {
    if (!open) return;
    setDraft(runtime);
  }, [open, runtime]);

  if (!open) {
    // Closed but mounted: the shell plays its exit with the last content it drew.
    return <ModalShell open={false} onDismiss={undefined} labelledBy="triage-model-modal-title">{null}</ModalShell>;
  }

  const dirty = !runtimeSettingsEqual(draft, runtime);

  const handleSave = async () => {
    if (!runtimeOptions) return;
    await onSave(draft);
    onClose();
  };

  return (
    <ModalShell open onDismiss={isSaving ? undefined : onClose} labelledBy="triage-model-modal-title">
      <div className="modal-header">
        <div>
          <div className="state-label">{scopeLabel}</div>
          <h2 id="triage-model-modal-title" className="modal-title">
            Model settings
          </h2>
          <p className="modal-subtitle">{subtitle}</p>
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
        ) : runtimeOptionsError ? (
          <p className="modal-hint" role="alert">
            {describeError(runtimeOptionsError, "Could not load the model options")}. Close
            and reopen to retry.
          </p>
        ) : (
          <p className="modal-hint">Loading runtime options…</p>
        )}
      </div>

      <div className="modal-footer">
        <Button variant="secondary" disabled={isSaving} onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="primary"
          disabled={isSaving || !runtimeOptions || !dirty}
          onClick={() => void handleSave()}
        >
          {isSaving ? "Saving…" : "Save"}
        </Button>
      </div>
    </ModalShell>
  );
}
