import { useCallback } from "react";

import { api } from "../api/client";
import { usePathBrowse } from "../hooks/usePathBrowse";
import { browseSeed } from "../lib/pathExplorer";
import { PathExplorerToolbar } from "./PathExplorerToolbar";

interface RepoPathExplorerProps {
  value: string;
  onChange: (repoPath: string) => void;
  disabled?: boolean;
  /** Store absolute filesystem paths (for iCloud/Obsidian) instead of repo-relative paths. */
  absolutePaths?: boolean;
  /** Isolate React Query cache between multiple explorers on one screen. */
  explorerKey?: string;
  /** Browse location when the user has not navigated yet. */
  startPath?: string;
  /** Jump the explorer to a typed/pasted path when this value changes. */
  navigateTo?: string;
}

export { sanitizeBrowsePath } from "../lib/pathExplorer";

export function RepoPathExplorer({
  value,
  onChange,
  disabled = false,
  absolutePaths = false,
  explorerKey = "default",
  startPath = ".",
  navigateTo = "",
}: RepoPathExplorerProps) {
  const fetchListing = useCallback(
    (seed: string) => api.browseDirectory(seed === "." ? undefined : seed),
    [],
  );

  const { data, loading, pathMismatch, error, displayPath, navigate, resetBrowse } = usePathBrowse({
    explorerKey,
    startPath,
    navigateTo,
    enabled: !disabled,
    fetchListing,
  });

  const storedPath = absolutePaths ? value || displayPath : value || data?.repo_path || displayPath;
  const startFolder = browseSeed(startPath || ".");
  const showEntries = !loading && Boolean(data?.entries.length);

  const selectCurrent = () => {
    if (absolutePaths) {
      if (data?.current_path) onChange(data.current_path);
      return;
    }
    if (data?.repo_path) onChange(data.repo_path);
  };

  return (
    <div className="repo-path-explorer">
      <PathExplorerToolbar
        disabled={disabled}
        loading={loading}
        parentPath={data?.parent_path}
        onUp={() => data?.parent_path && navigate(data.parent_path)}
        onRoot={!absolutePaths ? () => data?.repo_root && navigate(data.repo_root) : undefined}
        onStart={absolutePaths && startFolder !== "." ? resetBrowse : undefined}
        showSelect
        selectDisabled={!data || loading}
        onSelect={selectCurrent}
      />

      <div className="repo-path-explorer-path" title={displayPath}>
        {loading ? `Opening ${displayPath}…` : displayPath}
      </div>

      {error && (
        <p className="modal-hint" style={{ color: "var(--rdl)", marginTop: 6 }}>
          {error instanceof Error ? error.message : "Failed to browse directory"}
        </p>
      )}

      {pathMismatch && data?.current_path && (
        <p className="modal-hint" style={{ color: "var(--rdl)", marginTop: 6 }}>
          Could not open the requested folder. Showing{" "}
          <code>{data.current_path}</code> instead.
        </p>
      )}

      <div className="repo-path-explorer-list" role="listbox" aria-label="Directories">
        {loading ? (
          <div className="repo-path-explorer-empty">Loading directories…</div>
        ) : showEntries ? (
          data!.entries.map((entry) => (
            <button
              key={entry.path}
              type="button"
              className={`list-btn repo-path-explorer-item ${
                (absolutePaths ? entry.path : entry.repo_path) === storedPath ? "active" : ""
              }`.trim()}
              disabled={disabled}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                navigate(entry.path);
              }}
              onDoubleClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onChange(absolutePaths ? entry.path : entry.repo_path);
              }}
            >
              <span className="repo-path-explorer-folder" aria-hidden>
                📁
              </span>
              <span className="repo-path-explorer-name">{entry.name}</span>
              <span className="repo-path-explorer-rel">{entry.repo_path}</span>
            </button>
          ))
        ) : (
          <div className="repo-path-explorer-empty">No subdirectories</div>
        )}
      </div>

      <p className="modal-hint" style={{ marginTop: 6 }}>
        Click a folder to open it, double-click to select, or use “Use this folder” for the current
        directory. Stored path: <code>{storedPath || "."}</code>
      </p>
    </div>
  );
}
