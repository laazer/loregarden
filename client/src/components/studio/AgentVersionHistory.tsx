import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";

interface Props {
  slug: string;
  currentVersion?: number;
}

/**
 * Version history for a studio agent: every edit appends an immutable snapshot.
 * Lists versions newest-first, lets you inspect a version's role body, and
 * restore an old version (which lands as a new head version — history is never
 * mutated).
 */
export function AgentVersionHistory({ slug, currentVersion }: Props) {
  const qc = useQueryClient();
  const [openVersion, setOpenVersion] = useState<number | null>(null);

  const versions = useQuery({
    queryKey: ["studio-agent-versions", slug],
    queryFn: () => api.studioAgentVersions(slug),
    enabled: Boolean(slug),
  });

  const detail = useQuery({
    queryKey: ["studio-agent-version", slug, openVersion],
    queryFn: () => api.studioAgentVersion(slug, openVersion!),
    enabled: openVersion != null,
  });

  const restore = useMutation({
    mutationFn: (version: number) => api.restoreStudioAgentVersion(slug, version),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["studio-agents"] });
      qc.invalidateQueries({ queryKey: ["studio-agent-versions", slug] });
      setOpenVersion(null);
    },
  });

  const rows = versions.data ?? [];
  const head = currentVersion ?? (rows[0]?.version ?? 1);

  return (
    <div className="studio-card">
      <h3 className="studio-card-title">Version history</h3>
      {versions.isLoading && <p className="studio-muted">Loading…</p>}
      {!versions.isLoading && rows.length === 0 && (
        <p className="studio-muted">No versions recorded yet.</p>
      )}
      <ul className="studio-version-list">
        {rows.map((v) => {
          const isHead = v.version === head;
          const isOpen = v.version === openVersion;
          return (
            <li key={v.version} className="studio-version-item">
              <div className="studio-version-row">
                <span className="studio-version-tag">
                  v{v.version}
                  {isHead ? " · current" : ""}
                </span>
                <span className="studio-muted">{v.created_by}</span>
                <span className="studio-muted">
                  {new Date(v.created_at).toLocaleString()}
                </span>
                <span className="studio-version-actions">
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() => setOpenVersion(isOpen ? null : v.version)}
                  >
                    {isOpen ? "Hide" : "View"}
                  </button>
                  {!isHead && (
                    <button
                      type="button"
                      className="btn-secondary btn-sm"
                      disabled={restore.isPending}
                      onClick={() => restore.mutate(v.version)}
                    >
                      Restore
                    </button>
                  )}
                </span>
              </div>
              {v.change_note && (
                <p className="studio-version-note">{v.change_note}</p>
              )}
              {isOpen && (
                <pre className="studio-version-body">
                  {detail.isFetching
                    ? "Loading…"
                    : detail.data?.snapshot?.role_body ?? "(no snapshot)"}
                </pre>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
