import { IconCloseButton } from "./IconCloseButton";

import { useEffect, useId, useState } from "react";

import {
  ImportTicketFileExplorer,
  type SelectedImportFile,
} from "./ImportTicketFileExplorer";
import { selectedImportFileList } from "../lib/importTicketFiles";

export type ImportMode = "regular" | "smart";

export interface ImportTicketsModalProps {
  open: boolean;
  workspaceSlug: string;
  initialBrowsePath?: string;
  isLoading: boolean;
  errorMessage?: string | null;
  onClose: () => void;
  onContinue: (filePaths: string[], mode: ImportMode) => void | Promise<void>;
  initialMode?: ImportMode;
  /** Hide the Regular/Smart selector and lock the modal to `initialMode`. */
  lockMode?: boolean;
}

function normalizeInitialMode(mode: unknown): ImportMode {
  return mode === "smart" ? "smart" : "regular";
}

export function ImportTicketsModal({
  open,
  workspaceSlug,
  initialBrowsePath = ".",
  isLoading,
  errorMessage,
  onClose,
  onContinue,
  initialMode = "regular",
  lockMode = false,
}: ImportTicketsModalProps) {
  const normalizedInitialMode = normalizeInitialMode(initialMode);
  const [selectedFiles, setSelectedFiles] = useState<Map<string, SelectedImportFile>>(new Map());
  const [mode, setMode] = useState<ImportMode>(normalizedInitialMode);
  const [hasSubmitted, setHasSubmitted] = useState(false);
  const descriptionId = useId();

  const handleModeKeyDown = (key: string, currentMode: ImportMode) => {
    if (isLoading) return;
    if (key === "ArrowRight" || key === "ArrowDown") {
      setMode(currentMode === "regular" ? "smart" : "regular");
    } else if (key === "ArrowLeft" || key === "ArrowUp") {
      setMode(currentMode === "smart" ? "regular" : "smart");
    }
  };

  useEffect(() => {
    if (!open) return;
    setSelectedFiles(new Map());
    setMode(normalizedInitialMode);
    setHasSubmitted(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // A new onContinue means the caller is offering a fresh submission path
  // (e.g. a retry handler); don't leave the button stuck disabled from a
  // guard raised against the previous callback.
  useEffect(() => {
    setHasSubmitted(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onContinue]);

  if (!open) return null;

  const selected = selectedImportFileList(selectedFiles);
  const canContinue = selected.length > 0 && !isLoading && !hasSubmitted;

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

  const toggleFiles = (files: SelectedImportFile[], checked: boolean) => {
    setSelectedFiles((current) => {
      const next = new Map(current);
      for (const file of files) {
        if (checked) {
          next.set(file.path, file);
        } else {
          next.delete(file.path);
        }
      }
      return next;
    });
  };

  const handleContinue = () => {
    if (!canContinue) return;
    // Guard synchronously so rapid/duplicate clicks before a re-render can't
    // fire onContinue more than once for a given submission.
    setHasSubmitted(true);
    try {
      const result = onContinue(selected.map((file) => file.path), mode);
      if (result instanceof Promise) {
        result.catch(() => {
          // Error handling - allow caller to observe the rejection without crashing
        });
      }
    } catch {
      // Error handling - allow caller to observe the error without crashing
    }
  };

  return (
    <>
      <div className="modal-overlay" onClick={isLoading ? undefined : () => onClose()} role="presentation" />
      <div className="modal-panel" role="dialog" aria-labelledby="import-tickets-picker-title">
        <div className="modal-header">
          <div>
            <div className="state-label">{workspaceSlug}</div>
            <h2 id="import-tickets-picker-title" className="modal-title">
              Import work items
            </h2>
            <p className="modal-subtitle">
              {lockMode && mode === "smart"
                ? "Select ticket files to open as a scoping session in Ticket Studio."
                : "Select ticket files to import from the repository."}
            </p>
          </div>
          <IconCloseButton disabled={isLoading} onClick={onClose} />
        </div>

        <div className="modal-body">
          {!lockMode && (
          <div className="modal-field">
            <div
              role="radiogroup"
              aria-label="Import mode"
              onKeyDown={(e) => {
                if (e.key.startsWith("Arrow")) {
                  e.preventDefault();
                  handleModeKeyDown(e.key, mode);
                }
              }}
            >
              <button
                type="button"
                role="radio"
                aria-checked={mode === "regular"}
                aria-describedby={descriptionId}
                disabled={isLoading}
                onClick={() => !isLoading && setMode("regular")}
                className="import-mode-option"
              >
                Regular import
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={mode === "smart"}
                aria-describedby={descriptionId}
                title="Smart import enriches your work items with Studio-style preview data and enhanced descriptions"
                disabled={isLoading}
                onClick={() => !isLoading && setMode("smart")}
                className="import-mode-option"
              >
                Smart import
              </button>
            </div>
            <p id={descriptionId} style={{ fontSize: 12, color: "var(--txm)", margin: "6px 0 0 0" }}>
              Smart import includes Studio-style metadata; Regular import uses standard fields only.
            </p>
          </div>
          )}

          {errorMessage && (
            <p className="modal-hint" style={{ color: "var(--rdl)" }}>
              {errorMessage}
            </p>
          )}

          <ImportTicketFileExplorer
            explorerKey={`import-tickets-${workspaceSlug}`}
            selectedFiles={selectedFiles}
            onToggleFile={toggleFile}
            onToggleFiles={toggleFiles}
            disabled={isLoading}
            startPath={initialBrowsePath}
          />

          {selected.length > 0 && (
            <div className="modal-field" style={{ marginTop: 12 }}>
              <div className="modal-field-label">
                Selected ({selected.length})
              </div>
              <ul
                className="import-selected-list"
                style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--txm)" }}
              >
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
