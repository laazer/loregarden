import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";

import { api } from "../api/client";
import { QueueDashboard } from "../components/QueueDashboard";
import { PageTopbar } from "../components/TopbarPageSlot";
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
      <PageTopbar title="Queue Dashboard">
        {activeWorkspace && (
          <div className="queue-topbar-metrics">
            <span className="queue-topbar-metric">
              <span className="queue-topbar-metric-label">Utilization</span>
              <span className="queue-topbar-metric-value">{utilization}%</span>
            </span>
            <span className="queue-topbar-metric">
              <span className="queue-topbar-metric-label">Active</span>
              <span className="queue-topbar-metric-value">
                {stats?.active_count ?? 0}/{stats?.max_concurrent ?? 3}
              </span>
            </span>
            <span className="queue-topbar-metric">
              <span className="queue-topbar-metric-label">Queued</span>
              <span className="queue-topbar-metric-value">{stats?.queued_count ?? 0}</span>
            </span>
            <span className={`queue-live-badge${isWebSocket ? " connected" : ""}`}>
              <span className="queue-live-badge-dot" aria-hidden />
              {isWebSocket ? "Real-time" : "Polling"}
            </span>
          </div>
        )}
        <label className="topbar-workspace-picker">
          <span className="topbar-workspace-picker-label">Workspace</span>
          <select
            className="btn-secondary topbar-workspace-picker-select"
            value={activeSlug}
            disabled={!workspaces.data?.length}
            aria-label="Queue workspace"
            onChange={(event) => setQueueWorkspaceSlug(event.target.value)}
          >
            {(workspaces.data ?? []).map((ws) => (
              <option key={ws.slug} value={ws.slug}>
                {ws.name}
              </option>
            ))}
          </select>
        </label>
      </PageTopbar>

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
