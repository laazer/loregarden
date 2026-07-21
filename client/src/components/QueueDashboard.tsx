import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import {
  getQueueOperationDiff,
  listQueueOperations,
  type QueueOperationDetails,
  type QueueOperationSummary,
} from "../lib/queueReviewApi";
import { OperationDiffReviewView } from "./OperationDiffReviewView";
import { ParallelQueueVisualization } from "./ParallelQueueVisualization";
import { QueueAdvancedControls } from "./QueueAdvancedControls";
import { QueueHistoricalAnalytics } from "./QueueHistoricalAnalytics";
import { QueueNotifications } from "./QueueNotifications";
import { useParallelExecutionWS } from "../hooks/useParallelExecutionWS";
import "./QueueDashboard.css";

export interface QueueDashboardProps {
  workspaceId: string;
  workspaceName?: string;
  showAnalytics?: boolean;
  showControls?: boolean;
  embedded?: boolean;
}

export function QueueDashboard({
  workspaceId,
  workspaceName,
  showAnalytics = true,
  showControls = true,
  embedded = false,
}: QueueDashboardProps) {
  const { activeRuns, queuedRuns, stats, isWebSocket } = useParallelExecutionWS(workspaceId);

  const [activeSidebarTab, setActiveSidebarTab] = useState<
    "overview" | "controls" | "analytics" | "review"
  >("overview");

  const [operations, setOperations] = useState<QueueOperationSummary[]>([]);
  const [selectedOperationId, setSelectedOperationId] = useState<string | null>(null);
  const [operationDetails, setOperationDetails] = useState<QueueOperationDetails | null>(null);
  const [runOutputById, setRunOutputById] = useState<
    Record<string, { stdout?: string; stderr?: string; run_code?: string }>
  >({});

  const fetchOperations = useCallback(async () => {
    try {
      const data = await listQueueOperations(workspaceId, { limit: 20 });
      setOperations(data.operations || []);
    } catch (error) {
      console.error("Failed to fetch operations:", error);
    }
  }, [workspaceId]);

  const refreshOperationDetails = useCallback(async () => {
    if (!selectedOperationId) return;
    const data = await getQueueOperationDiff(workspaceId, selectedOperationId);
    setOperationDetails(data);

    const runIds = [
      ...new Set([
        ...(data.affected_run_ids ?? []),
        ...(data.diff ?? []).map((change) => change.run_id),
      ].filter(Boolean)),
    ];

    const outputs: Record<string, { stdout?: string; stderr?: string; run_code?: string }> = {};
    await Promise.all(
      runIds.map(async (runId) => {
        try {
          const run = await api.run(runId);
          outputs[runId] = {
            stdout: run.stdout,
            stderr: run.stderr,
            run_code: run.run_code,
          };
        } catch {
          outputs[runId] = { run_code: runId };
        }
      }),
    );
    setRunOutputById(outputs);
  }, [selectedOperationId, workspaceId]);

  useEffect(() => {
    if (activeSidebarTab === "review") {
      void fetchOperations();
    }
  }, [activeSidebarTab, fetchOperations]);

  useEffect(() => {
    if (!selectedOperationId) {
      setOperationDetails(null);
      setRunOutputById({});
      return;
    }
    void refreshOperationDetails().catch((error) => {
      console.error("Failed to fetch operation details:", error);
    });
  }, [selectedOperationId, refreshOperationDetails]);

  const dashboardMetrics = useMemo(() => {
    const totalRuns = (activeRuns?.length || 0) + (queuedRuns?.length || 0);
    const utilization =
      stats?.max_concurrent && stats?.active_count
        ? Math.round((stats.active_count / stats.max_concurrent) * 100)
        : 0;

    return {
      totalRuns,
      utilization,
      activeCount: stats?.active_count || 0,
      queuedCount: stats?.queued_count || 0,
      maxConcurrent: stats?.max_concurrent || 3,
    };
  }, [activeRuns, queuedRuns, stats]);

  return (
    <div className="queue-dashboard">
      <QueueNotifications workspaceId={workspaceId} />

      <div className="dashboard-layout">
        {!embedded && (
          <header className="dashboard-header">
            <div className="header-title">
              <h1>Queue Dashboard</h1>
              <p className="workspace-indicator">Workspace: {workspaceName ?? workspaceId}</p>
            </div>

            <div className="header-metrics">
              <div className="metric-badge">
                <span className="metric-label">Utilization</span>
                <span className="metric-value">{dashboardMetrics.utilization}%</span>
              </div>
              <div className="metric-badge">
                <span className="metric-label">Active</span>
                <span className="metric-value">
                  {dashboardMetrics.activeCount}/{dashboardMetrics.maxConcurrent}
                </span>
              </div>
              <div className="metric-badge">
                <span className="metric-label">Queued</span>
                <span className="metric-value">{dashboardMetrics.queuedCount}</span>
              </div>
              <div className={`connection-badge ${isWebSocket ? "connected" : "polling"}`}>
                {isWebSocket ? "🟢 Real-time" : "📡 Polling"}
              </div>
            </div>
          </header>
        )}

        <div className="dashboard-content">
          <div className="visualization-section">
            {activeSidebarTab === "review" && operationDetails ? (
              <div className="review-main-panel">
                <button
                  type="button"
                  className="btn-secondary btn-compact review-main-back"
                  onClick={() => {
                    setSelectedOperationId(null);
                    setOperationDetails(null);
                  }}
                >
                  ← All operations
                </button>
                <OperationDiffReviewView
                  workspaceId={workspaceId}
                  operation={operationDetails}
                  runOutputById={runOutputById}
                  onRefresh={refreshOperationDetails}
                />
              </div>
            ) : (
              <ParallelQueueVisualization workspaceId={workspaceId} />
            )}
          </div>

          <aside className="dashboard-sidebar">
            <div className="sidebar-tabs">
              <button
                type="button"
                className={`tab-btn ${activeSidebarTab === "overview" ? "active" : ""}`}
                onClick={() => setActiveSidebarTab("overview")}
              >
                Overview
              </button>
              <button
                type="button"
                className={`tab-btn ${activeSidebarTab === "review" ? "active" : ""}`}
                onClick={() => setActiveSidebarTab("review")}
              >
                Review
              </button>
              {showControls ? (
                <button
                  type="button"
                  className={`tab-btn ${activeSidebarTab === "controls" ? "active" : ""}`}
                  onClick={() => setActiveSidebarTab("controls")}
                >
                  Controls
                </button>
              ) : null}
              {showAnalytics ? (
                <button
                  type="button"
                  className={`tab-btn ${activeSidebarTab === "analytics" ? "active" : ""}`}
                  onClick={() => setActiveSidebarTab("analytics")}
                >
                  Analytics
                </button>
              ) : null}
            </div>

            <div className="sidebar-content">
              {activeSidebarTab === "overview" ? (
                <div className="overview-panel">
                  <h3>Queue Status</h3>
                  <div className="status-grid">
                    <div className="status-item">
                      <span className="status-label">Total Runs</span>
                      <span className="status-value">{dashboardMetrics.totalRuns}</span>
                    </div>
                    <div className="status-item">
                      <span className="status-label">Utilization</span>
                      <span className="status-value">{dashboardMetrics.utilization}%</span>
                    </div>
                    <div className="status-item">
                      <span className="status-label">Active Slots</span>
                      <span className="status-value">
                        {dashboardMetrics.activeCount}/{dashboardMetrics.maxConcurrent}
                      </span>
                    </div>
                    <div className="status-item">
                      <span className="status-label">Queue Depth</span>
                      <span className="status-value">{dashboardMetrics.queuedCount}</span>
                    </div>
                  </div>
                </div>
              ) : null}

              {activeSidebarTab === "review" ? (
                <div className="review-panel">
                  <h3>Queue Operations</h3>
                  {operations.length === 0 ? (
                    <p className="no-items">No operations to review</p>
                  ) : (
                    <div className="operations-list">
                      {operations.map((op) => (
                        <button
                          key={op.id}
                          type="button"
                          className={`operation-item ${selectedOperationId === op.id ? "selected" : ""}`}
                          onClick={() => setSelectedOperationId(op.id)}
                        >
                          <span className="op-type">{op.operation_type}</span>
                          <span className="op-status">{op.approved ? "✓ Approved" : "◯ Pending"}</span>
                          <span className="op-affects">{op.affected_count} runs</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ) : null}

              {activeSidebarTab === "controls" && showControls ? (
                <QueueAdvancedControls
                  workspaceId={workspaceId}
                  activeRuns={activeRuns || []}
                  queuedRuns={queuedRuns || []}
                />
              ) : null}

              {activeSidebarTab === "analytics" && showAnalytics ? (
                <QueueHistoricalAnalytics workspaceId={workspaceId} />
              ) : null}
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
