import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import baxterHead from "../assets/chat/baxter-head.png";
import {
  getQueueOperationDiff,
  listQueueOperations,
  type QueueOperationDetails,
  type QueueOperationSummary,
} from "../lib/queueReviewApi";
import { useQueueStatus } from "../state/QueueStatusContext";
import { OperationDiffReviewView } from "./OperationDiffReviewView";
import { ParallelQueueVisualization } from "./ParallelQueueVisualization";
import { QueueAdvancedControls } from "./QueueAdvancedControls";
import { QueueGitAutomation } from "./QueueGitAutomation";
import { QueueHistoricalAnalytics } from "./QueueHistoricalAnalytics";
import { QueueHistoryRail } from "./QueueHistoryRail";
import "./QueueDashboard.css";

export interface QueueDashboardProps {
  showAnalytics?: boolean;
  showControls?: boolean;
}

type SidebarTab = "overview" | "history" | "review" | "controls" | "analytics";

const TABS: { key: SidebarTab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "history", label: "History" },
  { key: "review", label: "Review" },
  { key: "controls", label: "Controls" },
  { key: "analytics", label: "Analytics" },
];

export function QueueDashboard({
  showAnalytics = true,
  showControls = true,
}: QueueDashboardProps) {
  const { activeRuns, queuedRuns, stats, workspaces } = useQueueStatus();

  const [activeSidebarTab, setActiveSidebarTab] = useState<SidebarTab>("overview");

  const [operations, setOperations] = useState<QueueOperationSummary[]>([]);
  const [selectedOperationId, setSelectedOperationId] = useState<string | null>(null);
  const [operationDetails, setOperationDetails] = useState<QueueOperationDetails | null>(null);
  const [runOutputById, setRunOutputById] = useState<
    Record<string, { stdout?: string; stderr?: string; run_code?: string }>
  >({});

  const fetchOperations = useCallback(async () => {
    try {
      const data = await listQueueOperations({ limit: 20 });
      setOperations(data.operations || []);
    } catch (error) {
      console.error("Failed to fetch operations:", error);
    }
  }, []);

  const selectedWorkspaceId = useMemo(() => {
    const fromList = operations.find((op) => op.id === selectedOperationId)?.workspace_id;
    return fromList ?? operationDetails?.workspace_id ?? "";
  }, [operations, selectedOperationId, operationDetails]);

  const refreshOperationDetails = useCallback(async () => {
    if (!selectedOperationId || !selectedWorkspaceId) return;
    const data = await getQueueOperationDiff(selectedWorkspaceId, selectedOperationId);
    setOperationDetails(data);

    const runIds = [
      ...new Set(
        [
          ...(data.affected_run_ids ?? []),
          ...(data.diff ?? []).map((change) => change.run_id),
        ].filter(Boolean),
      ),
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
  }, [selectedOperationId, selectedWorkspaceId]);

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

  const metrics = useMemo(() => {
    const activeCount = stats?.active_count || 0;
    const maxConcurrent = stats?.max_concurrent || 3;

    return {
      totalRuns: (activeRuns?.length || 0) + (queuedRuns?.length || 0),
      utilization: activeCount ? Math.round((activeCount / maxConcurrent) * 100) : 0,
      activeCount,
      queuedCount: stats?.queued_count || 0,
      maxConcurrent,
    };
  }, [activeRuns, queuedRuns, stats]);

  const visibleTabs = TABS.filter(
    (tab) =>
      (tab.key !== "controls" || showControls) && (tab.key !== "analytics" || showAnalytics),
  );

  const idleCopy = metrics.totalRuns
    ? `${metrics.activeCount} running · ${metrics.queuedCount} queued behind`
    : "All slots open — dispatch a run and Baxter will fetch the queue.";

  return (
    <div className="queue-dashboard">
      <div className="queue-layout">
        <main className="queue-layout-main">
          {activeSidebarTab === "review" && operationDetails ? (
            <div className="queue-review-main">
              <button
                type="button"
                className="btn-secondary btn-compact"
                onClick={() => {
                  setSelectedOperationId(null);
                  setOperationDetails(null);
                }}
              >
                ← All operations
              </button>
              <OperationDiffReviewView
                workspaceId={selectedWorkspaceId}
                operation={operationDetails}
                runOutputById={runOutputById}
                onRefresh={refreshOperationDetails}
              />
            </div>
          ) : (
            <ParallelQueueVisualization />
          )}
        </main>

        <aside className="queue-rail">
          <div className="queue-rail-card">
            <div className="queue-rail-tabs tab-bar">
              <div className="tab-bar-scroll" role="tablist" aria-label="Queue panels">
                {visibleTabs.map((tab) => (
                  <button
                    key={tab.key}
                    type="button"
                    role="tab"
                    aria-selected={activeSidebarTab === tab.key}
                    className={`tab-btn${activeSidebarTab === tab.key ? " active" : ""}`}
                    onClick={() => setActiveSidebarTab(tab.key)}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="queue-rail-body">
              {activeSidebarTab === "overview" ? (
                <>
                  <div className="queue-rail-heading">Queue status</div>
                  <div className="queue-rail-grid">
                    <div className="queue-rail-tile">
                      <div className="queue-rail-tile-label">Total runs</div>
                      <div className="queue-rail-tile-value">{metrics.totalRuns}</div>
                    </div>
                    <div className="queue-rail-tile">
                      <div className="queue-rail-tile-label">Utilization</div>
                      <div className="queue-rail-tile-value">{metrics.utilization}%</div>
                    </div>
                    <div className="queue-rail-tile">
                      <div className="queue-rail-tile-label">Active slots</div>
                      <div className="queue-rail-tile-value">
                        {metrics.activeCount}/{metrics.maxConcurrent}
                      </div>
                    </div>
                    <div className="queue-rail-tile">
                      <div className="queue-rail-tile-label">Queue depth</div>
                      <div className="queue-rail-tile-value">{metrics.queuedCount}</div>
                    </div>
                  </div>
                </>
              ) : null}

              {activeSidebarTab === "history" ? <QueueHistoryRail /> : null}

              {activeSidebarTab === "review" ? (
                <>
                  <div className="queue-rail-heading">Queue operations</div>
                  {operations.length === 0 ? (
                    <p className="queue-rail-empty">No operations to review</p>
                  ) : (
                    <div className="queue-op-list">
                      {operations.map((op) => (
                        <button
                          key={op.id}
                          type="button"
                          className={`queue-op${selectedOperationId === op.id ? " is-selected" : ""}`}
                          onClick={() => setSelectedOperationId(op.id)}
                        >
                          <span className="queue-op-type">{op.operation_type}</span>
                          <span
                            className={`queue-op-status${op.approved ? " is-approved" : ""}`}
                          >
                            <span className="queue-op-dot" aria-hidden />
                            {op.approved ? "Approved" : "Pending"}
                          </span>
                          <span className="queue-op-affects">{op.affected_count} runs</span>
                        </button>
                      ))}
                    </div>
                  )}
                </>
              ) : null}

              {activeSidebarTab === "controls" && showControls ? (
                <>
                  {workspaces.map((ws) => (
                    <div key={ws.id}>
                      <QueueGitAutomation workspaceSlug={ws.slug} workspaceName={ws.name} />
                      <div className="queue-rail-divider" />
                    </div>
                  ))}
                  <QueueAdvancedControls
                    activeRuns={activeRuns || []}
                    queuedRuns={queuedRuns || []}
                  />
                </>
              ) : null}

              {activeSidebarTab === "analytics" && showAnalytics ? (
                <QueueHistoricalAnalytics />
              ) : null}
            </div>

            <div className="queue-rail-baxter">
              <img src={baxterHead} alt="" width={32} height={32} />
              <div className="queue-rail-baxter-copy">{idleCopy}</div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
