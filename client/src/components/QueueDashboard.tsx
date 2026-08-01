import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import baxterHead from "../assets/chat/baxter-head.png";
import {
  getQueueOperationDiff,
  listQueueOperations,
  type QueueOperationDetails,
  type QueueOperationSummary,
} from "../lib/queueReviewApi";
import type { QueueEvent } from "../lib/queueSocket";
import { useQueueStatus } from "../state/QueueStatusContext";
import { pushToast, type ToastInput } from "../state/toastStore";
import { OperationDiffReviewView } from "./OperationDiffReviewView";
import { ParallelQueueVisualization } from "./ParallelQueueVisualization";
import { QueueAdvancedControls } from "./QueueAdvancedControls";
import { QueueGitAutomation } from "./QueueGitAutomation";
import { QueueHistoricalAnalytics } from "./QueueHistoricalAnalytics";
import "./QueueDashboard.css";

export interface QueueDashboardProps {
  workspaceId: string;
  showAnalytics?: boolean;
  showControls?: boolean;
}

type SidebarTab = "overview" | "review" | "controls" | "analytics";

const TABS: { key: SidebarTab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "review", label: "Review" },
  { key: "controls", label: "Controls" },
  { key: "analytics", label: "Analytics" },
];

/** A forwarded queue event, as a toast for the app-wide stack. */
function toToast(event: QueueEvent): ToastInput {
  const runId = event.data?.runId ?? "";
  const shortRun = runId ? runId.slice(0, 8) : "a run";

  if (event.type === "queue_promoted") {
    return {
      tone: "info",
      title: "Run promoted",
      message: `${shortRun} took slot ${event.data?.slotNumber ?? "?"}.`,
    };
  }

  if (event.type === "run_completed") {
    const failed = event.data?.status !== "succeeded";
    return {
      tone: failed ? "error" : "success",
      title: failed ? "Run failed" : "Run complete",
      message: `${shortRun} finished as ${event.data?.status ?? "unknown"}.`,
    };
  }

  return {
    tone: "error",
    title: "Queue error",
    message: event.data?.message ?? "The queue reported a failure.",
    // Stays until dismissed: a queue failure is the one thing worth reading late.
    duration: 0,
  };
}

export function QueueDashboard({
  workspaceId,
  showAnalytics = true,
  showControls = true,
}: QueueDashboardProps) {
  const { activeRuns, queuedRuns, stats, activeSlug, onQueueEvent } = useQueueStatus();

  const [activeSidebarTab, setActiveSidebarTab] = useState<SidebarTab>("overview");

  const [operations, setOperations] = useState<QueueOperationSummary[]>([]);
  const [selectedOperationId, setSelectedOperationId] = useState<string | null>(null);
  const [operationDetails, setOperationDetails] = useState<QueueOperationDetails | null>(null);
  const [runOutputById, setRunOutputById] = useState<
    Record<string, { stdout?: string; stderr?: string; run_code?: string }>
  >({});

  // The producer the toast stack never had. Events come from the shared socket
  // via the context; the snapshot alone cannot say that a run just finished.
  useEffect(
    () => onQueueEvent((event) => pushToast(toToast(event))),
    [onQueueEvent],
  );

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
                workspaceId={workspaceId}
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
            <div className="queue-rail-tabs">
              {visibleTabs.map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  className={`queue-rail-tab${activeSidebarTab === tab.key ? " is-active" : ""}`}
                  onClick={() => setActiveSidebarTab(tab.key)}
                >
                  {tab.label}
                </button>
              ))}
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
                  <QueueGitAutomation workspaceSlug={activeSlug} />
                  <div className="queue-rail-divider" />
                  <QueueAdvancedControls
                    workspaceId={workspaceId}
                    activeRuns={activeRuns || []}
                    queuedRuns={queuedRuns || []}
                  />
                </>
              ) : null}

              {activeSidebarTab === "analytics" && showAnalytics ? (
                <QueueHistoricalAnalytics workspaceId={workspaceId} />
              ) : null}
            </div>
          </div>

          <div className="queue-rail-baxter">
            <img src={baxterHead} alt="" width={46} height={46} />
            <div className="queue-rail-baxter-copy">{idleCopy}</div>
          </div>
        </aside>
      </div>
    </div>
  );
}
