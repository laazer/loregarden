import { useState } from "react";

import { diskApi } from "../../api/diskApi";
import type { LocalModel } from "../../api/diskTypes";
import { formatDiskBytes, MODEL_SOURCE_LABELS } from "../../lib/diskUsage";
import { describeError, pushToast } from "../../state/toastStore";
import { IconCloseButton } from "../IconCloseButton";
import { Button } from "../ui/Button";
import { ModalShell } from "../ui/ModalShell";

interface ModelDeleteModalProps {
  /** The model to delete; null keeps the dialog closed. */
  model: LocalModel | null;
  onClose: () => void;
  /** After a delete ran (deleted or failed), so the page re-reads the list. */
  onDeleted: () => void;
}

/**
 * Confirm deleting one model for good. It says what it frees, that its app can
 * download it again, and which LM Studio links go with an Ollama model. On
 * failure it stays open with the reason, so the person can retry or cancel.
 */
export function ModelDeleteModal({
  model,
  onClose,
  onDeleted,
}: ModelDeleteModalProps) {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  const close = () => {
    setError("");
    onClose();
  };

  const run = async () => {
    if (!model || running) return;
    setRunning(true);
    setError("");
    try {
      const result = await diskApi.deleteModel(model.id);
      onDeleted();
      if (result.outcome === "deleted") {
        pushToast({
          tone: "success",
          title: `Deleted ${result.name}`,
          message: `${formatDiskBytes(result.freed_bytes)} is free now.${result.detail ? ` ${result.detail}` : ""}`,
        });
        close();
      } else {
        setError(result.detail || "The delete failed.");
      }
    } catch (caught) {
      setError(describeError(caught, "Deleting the model failed."));
    } finally {
      setRunning(false);
    }
  };

  const source = model ? MODEL_SOURCE_LABELS[model.source] : "";
  return (
    <ModalShell
      open={model !== null}
      onDismiss={running ? undefined : close}
      labelledBy="model-delete-title"
      describedBy="model-delete-effect"
    >
      {model ? (
        <>
          <div className="modal-header">
            <div>
              <div className="state-label">Machine · Disk · Models</div>
              <h2 id="model-delete-title" className="modal-title">
                Delete {model.name}?
              </h2>
              <p id="model-delete-effect" className="modal-subtitle">
                {model.broken
                  ? `A broken link: the files it pointed at are already gone, so ${source} cannot load it. Deleting it only removes the dead entry.`
                  : model.links_to
                  ? `This is only a link to files ${MODEL_SOURCE_LABELS[model.links_to]} keeps, so deleting it frees almost nothing; the files it points at stay.`
                  : `Deleted for good, not moved to the Trash, so ${formatDiskBytes(model.frees_bytes)} is free right away. ${source} can download it again.`}
              </p>
            </div>
            <IconCloseButton disabled={running} onClick={close} />
          </div>
          <div
            className="modal-body disk-cleanup-body scroll-focus-ring"
            role="group"
            aria-label="What deleting this model does"
            tabIndex={0}
          >
            <p className="disk-cleanup-note">
              <span className="disk-folders-path">{model.path}</span>
            </p>
            {model.frees_bytes < model.size_bytes && !model.links_to ? (
              <p className="disk-cleanup-note">
                {formatDiskBytes(model.size_bytes - model.frees_bytes)} of its
                files are shared with another Ollama model and stay.
              </p>
            ) : null}
            {model.linked_by.length > 0 ? (
              <p className="disk-cleanup-note">
                LM Studio links to it, and these go too:{" "}
                {model.linked_by.join(", ")}.
              </p>
            ) : null}
            {error ? (
              <p className="disk-cleanup-error" role="alert">
                {error}
              </p>
            ) : null}
          </div>
          <div className="modal-footer">
            <Button variant="secondary" disabled={running} onClick={close}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={running}
              onClick={() => void run()}
            >
              {running
                ? "Deleting…"
                : model.links_to
                  ? "Delete link"
                  : `Delete and free ${formatDiskBytes(model.frees_bytes)}`}
            </Button>
          </div>
        </>
      ) : null}
    </ModalShell>
  );
}
