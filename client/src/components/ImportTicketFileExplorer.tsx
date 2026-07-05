import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";

export interface SelectedImportFile {
  path: string;
  name: string;
  repo_path: string;
}

interface ImportTicketFileExplorerProps {
  selectedFiles: Map<string, SelectedImportFile>;
  onToggleFile: (file: SelectedImportFile, checked: boolean) => void;
  disabled?: boolean;
  initialPath?: string;
}

export function ImportTicketFileExplorer({
  selectedFiles,
  onToggleFile,
  disabled = false,
  initialPath = ".",
}: ImportTicketFileExplorerProps) {
  const [browsePath, setBrowsePath] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (!disabled) {
      setBrowsePath(initialPath);
    }
  }, [disabled, initialPath]);

  const browse = useQuery({
    queryKey: ["browse-import", browsePath ?? initialPath ?? "."],
    queryFn: () => api.browseImportDirectory(browsePath ?? initialPath ?? "."),
    enabled: !disabled,
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

  const navigate = (path: string) => {
    setBrowsePath(path);
  };

  return (
    <div className="repo-path-explorer import-file-explorer">
      <div className="repo-path-explorer-toolbar">
        <button
          type="button"
          className="btn-secondary btn-compact"
          disabled={disabled || !data?.parent_path || browse.isFetching}
          onClick={() => data?.parent_path && navigate(data.parent_path)}
        >
          ↑ Up
        </button>
        <button
          type="button"
          className="btn-secondary btn-compact"
          disabled={disabled || browse.isFetching}
          onClick={() => data?.repo_root && navigate(data.repo_root)}
        >
          Loregarden root
        </button>
        <button
          type="button"
          className="btn-secondary btn-compact"
          disabled={disabled || browse.isFetching}
          onClick={() => navigate(initialPath)}
        >
          Workspace root
        </button>
      </div>

      <div className="repo-path-explorer-path" title={data?.current_path}>
        {data?.current_path ?? "Loading…"}
      </div>

      {browse.error && (
        <p className="modal-hint" style={{ color: "var(--rdl)", margin: "6px 10px 0" }}>
          {browse.error instanceof Error ? browse.error.message : "Failed to browse directory"}
        </p>
      )}

      <div className="repo-path-explorer-list import-file-explorer-list" aria-label="Import files">
        {browse.isFetching && !data ? (
          <div className="repo-path-explorer-empty">Loading…</div>
        ) : (
          <>
            {directories.map((entry) => (
              <button
                key={entry.path}
                type="button"
                className="list-btn repo-path-explorer-item"
                disabled={disabled}
                onClick={() => navigate(entry.path)}
                onDoubleClick={() => navigate(entry.path)}
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

            {!browse.isFetching && directories.length === 0 && files.length === 0 && (
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
