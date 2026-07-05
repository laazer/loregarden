import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api } from "../../api/client";

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
  const [browsePath, setBrowsePath] = useState(".");

  useEffect(() => {
    setBrowsePath(".");
  }, [workspaceSlug, contextRoot]);

  const browse = useQuery({
    queryKey: ["editor-browse", workspaceSlug, contextRoot, browsePath],
    queryFn: () => api.editorBrowse(workspaceSlug, browsePath, contextRoot),
    enabled: !disabled && Boolean(workspaceSlug),
  });

  const data = browse.data;
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
      <div className="editor-explorer-toolbar">
        <button
          type="button"
          className="btn-secondary btn-compact"
          disabled={disabled || browsePath === "." || !data?.parent_repo_path || browse.isFetching}
          onClick={() => data?.parent_repo_path && setBrowsePath(data.parent_repo_path)}
        >
          ↑ Up
        </button>
        <button
          type="button"
          className="btn-secondary btn-compact"
          disabled={disabled || browse.isFetching}
          onClick={() => setBrowsePath(".")}
        >
          Root
        </button>
      </div>

      <div className="editor-explorer-path" title={data?.current_path}>
        {data?.repo_path ?? "Loading…"}
      </div>

      {browse.error && (
        <p className="modal-hint editor-explorer-error">
          {browse.error instanceof Error ? browse.error.message : "Failed to browse files"}
        </p>
      )}

      <div className="editor-explorer-list" aria-label="Repository files">
        {browse.isFetching && !data ? (
          <div className="repo-path-explorer-empty">Loading…</div>
        ) : (
          <>
            {directories.map((entry) => (
              <button
                key={entry.path}
                type="button"
                className="list-btn editor-explorer-item"
                disabled={disabled}
                onClick={() => setBrowsePath(entry.repo_path)}
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

            {!browse.isFetching && directories.length === 0 && files.length === 0 && (
              <div className="repo-path-explorer-empty">No files in this folder</div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
