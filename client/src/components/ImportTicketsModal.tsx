import { IconCloseButton } from "./IconCloseButton";

import { useEffect, useState } from "react";

import {
  ImportTicketFileExplorer,
  type SelectedImportFile,
} from "./ImportTicketFileExplorer";
import { selectedImportFileList } from "../lib/importTicketFiles";

export interface ImportTicketsModalProps {
  open: boolean;
  workspaceSlug: string;
  initialBrowsePath?: string;
  isLoading: boolean;
  errorMessage?: string | null;
  onClose: () => void;
  onContinue: (filePaths: string[]) => void | Promise<void>;
}

export function ImportTicketsModal({
  open,
  workspaceSlug,
  initialBrowsePath = ".",
  isLoading,
  errorMessage,
  onClose,
  onContinue,
}: ImportTicketsModalProps) {
  const [selectedFiles, setSelectedFiles] = useState<Map<string, SelectedImportFile>>(new Map());

  useEffect(() => {
    if (!open) return;
    setSelectedFiles(new Map());
  }, [open]);

  if (!open) return null;

  const selected = selectedImportFileList(selectedFiles);
  const canContinue = selected.length > 0 && !isLoading;

  const toggleFile = (file: SelectedImportFile, checked: boolean) => {
    setSelectedFiles((current) => {
      const next = new Map(current);
      if (checked) {
        next.set(file.path, file);
      } else {
        next.delete(file.path);
      }
      return next;
    });
  };

  const handleContinue = () => {
    if (!canContinue) return;
    void onContinue(selected.map((file) => file.path));
  };

  return (
    <>
      <div className="modal-overlay" onClick={isLoading ? undefined : onClose} role="presentation" />
      <div className="modal-panel" role="dialog" aria-labelledby="import-tickets-picker-title">
        <div className="modal-header">
          <div>
            <div className="state-label">{workspaceSlug}</div>
            <h2 id="import-tickets-picker-title" className="modal-title">
              Import work items
            </h2>
            <p className="modal-subtitle">
              Select ticket files to import from the repository.
            </p>
          </div>
          <IconCloseButton disabled={isLoading} onClick={onClose} />
        </div>

        <div className="modal-body">
          {errorMessage && (
            <p className="modal-hint" style={{ color: "var(--rdl)" }}>
              {errorMessage}
            </p>
          )}

          <ImportTicketFileExplorer
            explorerKey={`import-tickets-${workspaceSlug}`}
            selectedFiles={selectedFiles}
            onToggleFile={toggleFile}
            disabled={isLoading}
            startPath={initialBrowsePath}
          />

          {selected.length > 0 && (
            <div className="modal-field" style={{ marginTop: 12 }}>
              <div className="modal-field-label">
                Selected ({selected.length})
              </div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--txm)" }}>
                {selected.map((file) => (
                  <li key={file.path}>{file.repo_path}</li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button type="button" className="btn-secondary" disabled={isLoading} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!canContinue}
            onClick={handleContinue}
          >
            {isLoading ? "Reading files…" : `Continue with ${selected.length || 0} file${selected.length === 1 ? "" : "s"}`}
          </button>
        </div>
      </div>
    </>
  );
}
