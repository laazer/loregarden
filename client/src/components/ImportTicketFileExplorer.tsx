import { useCallback, useMemo } from "react";

import { api } from "../api/client";
import { usePathBrowse } from "../hooks/usePathBrowse";
import { PathExplorerToolbar } from "./PathExplorerToolbar";

export interface SelectedImportFile {
  path: string;
  name: string;
  repo_path: string;
}

interface ImportTicketFileExplorerProps {
  selectedFiles: Map<string, SelectedImportFile>;
  onToggleFile: (file: SelectedImportFile, checked: boolean) => void;
  onToggleFiles?: (files: SelectedImportFile[], checked: boolean) => void;
  disabled?: boolean;
  startPath?: string;
  navigateTo?: string;
  explorerKey?: string;
}

export function ImportTicketFileExplorer({
  selectedFiles,
  onToggleFile,
  onToggleFiles,
  disabled = false,
  startPath = ".",
  navigateTo = "",
  explorerKey = "import-tickets",
}: ImportTicketFileExplorerProps) {
  const fetchListing = useCallback(
    (seed: string) => api.browseImportDirectory(seed === "." ? undefined : seed),
    [],
  );

  const { data, loading, pathMismatch, error, displayPath, navigate, resetBrowse } = usePathBrowse({
    explorerKey,
    startPath,
    navigateTo,
    enabled: !disabled,
    fetchListing,
  });

  const directories = useMemo(
    () => (data?.entries ?? []).filter((entry) => entry.kind === "directory"),
    [data?.entries],
  );
  const files = useMemo(
    () => (data?.entries ?? []).filter((entry) => entry.kind === "file"),
    [data?.entries],
  );

  const folderFiles = useMemo<SelectedImportFile[]>(
    () => files.map((entry) => ({ path: entry.path, name: entry.name, repo_path: entry.repo_path })),
    [files],
  );
  const selectedInFolder = folderFiles.filter((file) => selectedFiles.has(file.path)).length;
  const allSelected = folderFiles.length > 0 && selectedInFolder === folderFiles.length;

  const setFolderSelection = (checked: boolean) => {
    if (onToggleFiles) {
      onToggleFiles(folderFiles, checked);
      return;
    }
    for (const file of folderFiles) {
      if (selectedFiles.has(file.path) !== checked) {
        onToggleFile(file, checked);
      }
    }
  };

  return (
    <div className="repo-path-explorer import-file-explorer">
      <PathExplorerToolbar
        disabled={disabled}
        loading={loading}
        parentPath={data?.parent_path}
        onUp={() => data?.parent_path && navigate(data.parent_path)}
        onRoot={() => data?.repo_root && navigate(data.repo_root)}
        onStart={resetBrowse}
        startLabel="Workspace root"
      />

      <div className="repo-path-explorer-path" title={displayPath}>
        {loading ? `Opening ${displayPath}…` : displayPath}
      </div>

      {pathMismatch && data?.current_path && (
        <p className="modal-hint" style={{ color: "var(--rdl)", margin: "6px 10px 0" }}>
          Could not open the requested folder. Showing <code>{data.current_path}</code> instead.
        </p>
      )}

      {error && (
        <p className="modal-hint" style={{ color: "var(--rdl)", margin: "6px 10px 0" }}>
          {error instanceof Error ? error.message : "Failed to browse directory"}
        </p>
      )}

      {!loading && folderFiles.length > 0 && (
        <div className="import-file-bulk-actions">
          <button
            type="button"
            className="btn-secondary btn-compact"
            disabled={disabled || allSelected}
            onClick={() => setFolderSelection(true)}
          >
            Select all
          </button>
          <button
            type="button"
            className="btn-secondary btn-compact"
            disabled={disabled || selectedInFolder === 0}
            onClick={() => setFolderSelection(false)}
          >
            Clear all
          </button>
          <span className="import-file-bulk-count">
            {selectedInFolder}/{folderFiles.length} selected in this folder
          </span>
        </div>
      )}

      <div className="repo-path-explorer-list import-file-explorer-list" aria-label="Import files">
        {loading ? (
          <div className="repo-path-explorer-empty">Loading…</div>
        ) : (
          <>
            {directories.map((entry) => (
              <button
                key={entry.path}
                type="button"
                className="list-btn repo-path-explorer-item"
                disabled={disabled}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  navigate(entry.path);
                }}
                onDoubleClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  navigate(entry.path);
                }}
              >
                <span className="repo-path-explorer-folder" aria-hidden>
                  📁
                </span>
                <span className="repo-path-explorer-name">{entry.name}</span>
                <span className="repo-path-explorer-rel">{entry.repo_path}</span>
              </button>
            ))}

            {files.map((entry) => {
              const checked = selectedFiles.has(entry.path);
              return (
                <label
                  key={entry.path}
                  className={`import-file-row ${checked ? "import-file-row-selected" : ""}`.trim()}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={disabled}
                    onChange={(event) =>
                      onToggleFile(
                        { path: entry.path, name: entry.name, repo_path: entry.repo_path },
                        event.target.checked,
                      )
                    }
                  />
                  <span className="import-file-icon" aria-hidden>
                    📄
                  </span>
                  <span className="repo-path-explorer-name">{entry.name}</span>
                  <span className="repo-path-explorer-rel">{entry.repo_path}</span>
                </label>
              );
            })}

            {!loading && directories.length === 0 && files.length === 0 && (
              <div className="repo-path-explorer-empty">No importable files in this folder</div>
            )}
          </>
        )}
      </div>

      <p className="modal-hint" style={{ margin: "6px 10px 10px" }}>
        Open folders to find tickets. Check .md, .json, .yaml, or .yml files — selections persist
        while you browse.
      </p>
    </div>
  );
}
