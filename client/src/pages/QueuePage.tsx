import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";

import { api } from "../api/client";
import { PageHeroAppToolbar } from "../components/PageHeroAppToolbar";
import { QueueDashboard } from "../components/QueueDashboard";
import { useParallelExecutionWS } from "../hooks/useParallelExecutionWS";
import { useUiStore } from "../state/uiStore";

export function QueuePage() {
  const workspaceSlug = useUiStore((s) => s.workspace);
  const queueWorkspaceSlug = useUiStore((s) => s.queueWorkspaceSlug);
  const setQueueWorkspaceSlug = useUiStore((s) => s.setQueueWorkspaceSlug);

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });

  const activeSlug = useMemo(() => {
    if (queueWorkspaceSlug) return queueWorkspaceSlug;
    if (workspaceSlug && workspaceSlug !== "all") return workspaceSlug;
    return workspaces.data?.[0]?.slug ?? "";
  }, [queueWorkspaceSlug, workspaceSlug, workspaces.data]);

  const activeWorkspace = useMemo(
    () => workspaces.data?.find((ws) => ws.slug === activeSlug) ?? null,
    [workspaces.data, activeSlug],
  );

  useEffect(() => {
    if (!queueWorkspaceSlug && activeSlug) {
      setQueueWorkspaceSlug(activeSlug);
    }
  }, [activeSlug, queueWorkspaceSlug, setQueueWorkspaceSlug]);

  const { stats, isWebSocket } = useParallelExecutionWS(
    activeWorkspace?.id ?? "",
    Boolean(activeWorkspace?.id),
  );

  const utilization = useMemo(() => {
    if (!stats?.max_concurrent || !stats?.active_count) return 0;
    return Math.round((stats.active_count / stats.max_concurrent) * 100);
  }, [stats]);

  return (
    <div className="screen-view screen-view--queue">
      <header className="page-hero-header">
        <div className="page-hero-copy">
          <div className="page-hero-eyebrow">
            <span>Parallel Execution</span>
            <span className="page-hero-eyebrow-dot" aria-hidden />
            <span className="page-hero-eyebrow-muted">Queue · Review · Approve</span>
          </div>
          <h1 className="page-hero-title">Queue Dashboard</h1>
          <p className="page-hero-sub">
            Workspace: <span style={{ color: "var(--tx)" }}>{activeWorkspace?.name ?? "—"}</span>
          </p>
        </div>
        <div className="page-hero-actions">
          {activeWorkspace && (
            <div className="queue-hero-metrics">
              <div className="queue-hero-metric">
                <div className="queue-hero-metric-label">Utilization</div>
                <div className="queue-hero-metric-value">{utilization}%</div>
              </div>
              <div className="queue-hero-metric">
                <div className="queue-hero-metric-label">Active</div>
                <div className="queue-hero-metric-value">
                  {stats?.active_count ?? 0}/{stats?.max_concurrent ?? 3}
                </div>
              </div>
              <div className="queue-hero-metric">
                <div className="queue-hero-metric-label">Queued</div>
                <div className="queue-hero-metric-value">{stats?.queued_count ?? 0}</div>
              </div>
              <div className={`queue-live-badge${isWebSocket ? " connected" : ""}`}>
                <span className="queue-live-badge-dot" aria-hidden />
                {isWebSocket ? "Real-time" : "Polling"}
              </div>
            </div>
          )}
          <label className="editor-workspace-picker">
            <span className="page-hero-field-label">Workspace</span>
            <select
              className="btn-secondary page-hero-field-select"
              value={activeSlug}
              disabled={!workspaces.data?.length}
              onChange={(event) => setQueueWorkspaceSlug(event.target.value)}
            >
              {(workspaces.data ?? []).map((ws) => (
                <option key={ws.slug} value={ws.slug}>
                  {ws.name}
                </option>
              ))}
            </select>
          </label>
          <PageHeroAppToolbar />
        </div>
      </header>

      <div className="queue-page-body">
        {activeWorkspace ? (
          <QueueDashboard
            workspaceId={activeWorkspace.id}
            workspaceName={activeWorkspace.name}
            embedded
          />
        ) : workspaces.isLoading ? (
          <div className="queue-page-empty">Loading workspaces…</div>
        ) : (
          <div className="queue-page-empty">
            Add a workspace in the IDE before using the queue dashboard.
          </div>
        )}
      </div>
    </div>
  );
}
