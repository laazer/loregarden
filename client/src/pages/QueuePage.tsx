import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";

import { api } from "../api/client";
import { AppTopbarActions } from "../components/AppTopbarActions";
import { BrandMark } from "../components/BrandMark";
import { QueueDashboard } from "../components/QueueDashboard";
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

  return (
    <div className="app-shell queue-page">
      <header className="topbar">
        <div className="brand">
          <BrandMark />
          <div>
            <div className="brand-title">Parallel Execution</div>
            <div className="brand-sub">Queue, review, and approve agent runs</div>
          </div>
        </div>

        <div className="topbar-center">
          <label className="editor-workspace-picker">
            <span>Workspace</span>
            <select
              className="btn-secondary filter-select"
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
        </div>

        <div className="topbar-spacer" />

        <AppTopbarActions />
      </header>

      <div className="queue-page-body">
        {activeWorkspace ? (
          <QueueDashboard
            workspaceId={activeWorkspace.id}
            workspaceName={activeWorkspace.name}
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
