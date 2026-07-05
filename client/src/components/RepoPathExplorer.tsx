import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api/client";

interface RepoPathExplorerProps {
  value: string;
  onChange: (repoPath: string) => void;
  disabled?: boolean;
}

export function RepoPathExplorer({ value, onChange, disabled = false }: RepoPathExplorerProps) {
  const [browsePath, setBrowsePath] = useState<string | undefined>(undefined);

  useEffect(() => {
    setBrowsePath(undefined);
  }, [value]);

  const browse = useQuery({
    queryKey: ["browse-directory", browsePath ?? (value || ".")],
    queryFn: () => api.browseDirectory(browsePath ?? (value || ".")),
    enabled: !disabled,
  });

  const data = browse.data;
  const currentRepoPath = data?.repo_path ?? value;

  const navigate = (path: string) => {
    setBrowsePath(path);
  };

  const selectCurrent = () => {
    if (data?.repo_path) onChange(data.repo_path);
  };

  return (
    <div className="repo-path-explorer">
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
          className="btn-primary btn-compact"
          disabled={disabled || !data}
          onClick={selectCurrent}
        >
          Use this folder
        </button>
      </div>

      <div className="repo-path-explorer-path" title={data?.current_path}>
        {data?.current_path ?? "Loading…"}
      </div>

      {browse.error && (
        <p className="modal-hint" style={{ color: "var(--rdl)", marginTop: 6 }}>
          {browse.error instanceof Error ? browse.error.message : "Failed to browse directory"}
        </p>
      )}

      <div className="repo-path-explorer-list" role="listbox" aria-label="Directories">
        {browse.isFetching && !data ? (
          <div className="repo-path-explorer-empty">Loading directories…</div>
        ) : data?.entries.length ? (
          data.entries.map((entry) => (
            <button
              key={entry.path}
              type="button"
              className={`list-btn repo-path-explorer-item ${
                entry.repo_path === currentRepoPath ? "active" : ""
              }`.trim()}
              disabled={disabled}
              onClick={() => navigate(entry.path)}
              onDoubleClick={() => {
                onChange(entry.repo_path);
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
        directory. Stored path: <code>{currentRepoPath || "."}</code>
      </p>
    </div>
  );
}
