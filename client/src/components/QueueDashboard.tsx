import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import baxterHead from "../assets/chat/baxter-head.png";
import {
  getQueueOperationDiff,
  listQueueOperations,
  type QueueOperationDetails,
  type QueueOperationSummary,
} from "../lib/queueReviewApi";
import { useDockerCapacity } from "../hooks/useDockerCapacity";
import { useQueueStatus } from "../state/QueueStatusContext";
import { describeError, pushToast, toastActionFailed } from "../state/toastStore";
import { OperationDiffReviewView } from "./OperationDiffReviewView";
import { DockerAttentionRail } from "./DockerAttentionRail";
import { DockerCapacityRail } from "./DockerCapacityRail";
import { DockerQueueBoard } from "./DockerQueueBoard";
import { QueueKindToggle, type QueueKind } from "./QueueKindToggle";
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

type SidebarTab =
  | "overview"
  | "history"
  | "review"
  | "controls"
  | "analytics"
  | "capacity"
  | "attention";

/**
 * The rail's panels, per queue.
 *
 * They differ because the two pools genuinely have different questions behind
 * them: an agent lane has a history, a review and controls; docker capacity has
 * a ceiling and a short list of leases nobody could resolve automatically.
 * Carrying one queue's tabs into the other would offer panels with nothing to
 * put in them, which is worse than a shorter list.
 */
const TABS_BY_KIND: Record<QueueKind, { key: SidebarTab; label: string }[]> = {
  agents: [
    { key: "overview", label: "Overview" },
    { key: "history", label: "History" },
    { key: "review", label: "Review" },
    { key: "controls", label: "Controls" },
    { key: "analytics", label: "Analytics" },
  ],
  docker: [
    { key: "capacity", label: "Capacity" },
    { key: "attention", label: "Attention" },
  ],
};

export function QueueDashboard({
  showAnalytics = true,
  showControls = true,
}: QueueDashboardProps) {
  const { activeRuns, queuedRuns, stats, workspaces } = useQueueStatus();

  const [queueKind, setQueueKind] = useState<QueueKind>("agents");
  const [activeSidebarTab, setActiveSidebarTab] = useState<SidebarTab>("overview");
  // One read for both halves of the Docker view, so the rail summary and the
  // board beside it cannot disagree about the same moment.
  const docker = useDockerCapacity();

  /**
   * Switching queue also switches the rail, because the old tab does not exist
   * in the new vocabulary. Landing on the first panel of the queue you asked
   * for beats leaving a tablist with nothing selected.
   */
  const selectQueueKind = useCallback((kind: QueueKind) => {
    setQueueKind(kind);
    setActiveSidebarTab(TABS_BY_KIND[kind][0].key);
  }, []);

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
      // An empty list and a failed read look identical on the Review tab, so
      // the failure has to say so rather than pass for "no operations yet".
      toastActionFailed("Load queue operations", error);
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
    // A run whose output could not be read renders as a run that produced
    // none. Collected here and reported once, rather than one toast per run.
    const failedRuns: string[] = [];
    let firstOutputError: unknown;
    await Promise.all(
      runIds.map(async (runId) => {
        try {
          const run = await api.run(runId);
          outputs[runId] = {
            stdout: run.stdout,
            stderr: run.stderr,
            run_code: run.run_code,
          };
        } catch (error) {
          outputs[runId] = { run_code: runId };
          failedRuns.push(runId);
          firstOutputError ??= error;
        }
      }),
    );
    setRunOutputById(outputs);
    if (failedRuns.length > 0) {
      pushToast({
        tone: "warning",
        title: `Output unavailable for ${failedRuns.length} run(s)`,
        message: describeError(firstOutputError, "The run log could not be read"),
      });
    }
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
      toastActionFailed("Load operation details", error);
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

  const visibleTabs = TABS_BY_KIND[queueKind].filter(
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
          ) : queueKind === "docker" ? (
            <DockerQueueBoard
              status={docker.status}
              error={docker.error}
              loading={docker.loading}
              headerSlot={<QueueKindToggle value={queueKind} onChange={selectQueueKind} />}
            />
          ) : (
            <ParallelQueueVisualization
              headerSlot={<QueueKindToggle value={queueKind} onChange={selectQueueKind} />}
            />
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

              {/* Mounted only while selected, so its poll costs nothing on
                  the other tabs — the same reason Review fetches lazily. */}
              {activeSidebarTab === "capacity" ? (
                <DockerCapacityRail
                  status={docker.status}
                  error={docker.error}
                  loading={docker.loading}
                />
              ) : null}

              {activeSidebarTab === "attention" ? (
                <DockerAttentionRail status={docker.status} />
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
