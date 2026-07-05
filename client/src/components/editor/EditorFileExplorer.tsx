import { useCallback, useEffect, useMemo } from "react";

import { api } from "../../api/client";
import { usePathBrowse } from "../../hooks/usePathBrowse";
import { PathExplorerToolbar } from "../PathExplorerToolbar";

interface EditorFileExplorerProps {
  workspaceSlug: string;
  contextRoot: string;
  selectedPath: string | null;
  onOpenFile: (path: string) => void;
  disabled?: boolean;
}

export function EditorFileExplorer({
  workspaceSlug,
  contextRoot,
  selectedPath,
  onOpenFile,
  disabled = false,
}: EditorFileExplorerProps) {
  const fetchListing = useCallback(
    (seed: string) => api.editorBrowse(workspaceSlug, seed, contextRoot),
    [workspaceSlug, contextRoot],
  );

  const { data, loading, error, displayPath, navigate, resetBrowse } = usePathBrowse({
    explorerKey: `editor-${workspaceSlug}-${contextRoot}`,
    startPath: ".",
    enabled: !disabled && Boolean(workspaceSlug),
    fetchListing,
  });

  useEffect(() => {
    resetBrowse();
  }, [workspaceSlug, contextRoot, resetBrowse]);

  const directories = useMemo(
    () => (data?.entries ?? []).filter((entry) => entry.kind === "directory"),
    [data?.entries],
  );
  const files = useMemo(
    () => (data?.entries ?? []).filter((entry) => entry.kind === "file"),
    [data?.entries],
  );

  return (
    <div className="editor-file-explorer">
      <PathExplorerToolbar
        disabled={disabled}
        loading={loading}
        parentPath={data?.parent_repo_path}
        onUp={() => data?.parent_repo_path && navigate(data.parent_repo_path)}
        onRoot={resetBrowse}
        rootLabel="Root"
      />

      <div className="editor-explorer-path" title={data?.current_path}>
        {loading ? `Opening ${displayPath}…` : data?.repo_path ?? "Loading…"}
      </div>

      {error && (
        <p className="modal-hint editor-explorer-error">
          {error instanceof Error ? error.message : "Failed to browse files"}
        </p>
      )}

      <div className="editor-explorer-list" aria-label="Repository files">
        {loading ? (
          <div className="repo-path-explorer-empty">Loading…</div>
        ) : (
          <>
            {directories.map((entry) => (
              <button
                key={entry.path}
                type="button"
                className="list-btn editor-explorer-item"
                disabled={disabled}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  navigate(entry.repo_path);
                }}
              >
                <span className="editor-explorer-icon" aria-hidden>
                  📁
                </span>
                <span className="editor-explorer-name">{entry.name}</span>
              </button>
            ))}

            {files.map((entry) => (
              <button
                key={entry.path}
                type="button"
                className={`list-btn editor-explorer-item ${
                  selectedPath === entry.repo_path ? "active" : ""
                }`.trim()}
                disabled={disabled}
                onClick={() => onOpenFile(entry.repo_path)}
              >
                <span className="editor-explorer-icon" aria-hidden>
                  📄
                </span>
                <span className="editor-explorer-name">{entry.name}</span>
              </button>
            ))}

            {!loading && directories.length === 0 && files.length === 0 && (
              <div className="repo-path-explorer-empty">No files in this folder</div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
