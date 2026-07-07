/**
 * Unified queue management dashboard for solo developers
 * Combines visualization, controls, notifications, and analytics
 */

import React, { useState, useMemo, useEffect } from 'react';
import { ParallelQueueVisualization } from './ParallelQueueVisualization';
import { QueueNotifications } from './QueueNotifications';
import { QueueAdvancedControls } from './QueueAdvancedControls';
import { QueueHistoricalAnalytics } from './QueueHistoricalAnalytics';
import { QueueOperationReview, OperationComment } from './QueueOperationReview';
import { RunOutputReview, OutputLine } from './RunOutputReview';
import { useParallelExecutionWS } from '../hooks/useParallelExecutionWS';
import './QueueDashboard.css';

export interface QueueDashboardProps {
  workspaceId: string;
  userId?: string;
  showAnalytics?: boolean;
  showControls?: boolean;
}

export function QueueDashboard({
  workspaceId,
  userId,
  showAnalytics = true,
  showControls = true,
}: QueueDashboardProps) {
  const { activeRuns, queuedRuns, stats, connectionState, isWebSocket } =
    useParallelExecutionWS(workspaceId, userId);

  const [activeSidebarTab, setActiveSidebarTab] = useState<
    'overview' | 'controls' | 'analytics' | 'review'
  >('overview');

  const [expandedSections, setExpandedSections] = useState<Set<string>>(
    new Set(['overview'])
  );

  // Review tab state
  const [operations, setOperations] = useState<any[]>([]);
  const [selectedOperationId, setSelectedOperationId] = useState<string | null>(null);
  const [operationDetails, setOperationDetails] = useState<any | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [outputReview, setOutputReview] = useState<any | null>(null);
  const [isLoadingReview, setIsLoadingReview] = useState(false);

  const toggleSection = (sectionId: string) => {
    const newSections = new Set(expandedSections);
    if (newSections.has(sectionId)) {
      newSections.delete(sectionId);
    } else {
      newSections.add(sectionId);
    }
    setExpandedSections(newSections);
  };

  // Fetch operations list
  useEffect(() => {
    if (activeSidebarTab === 'review') {
      fetchOperations();
    }
  }, [activeSidebarTab, workspaceId]);

  const fetchOperations = async () => {
    try {
      const response = await fetch(
        `/api/parallel/workspace/${workspaceId}/queue/operations?limit=10&approved_only=false`
      );
      if (response.ok) {
        const data = await response.json();
        setOperations(data.operations || []);
      }
    } catch (error) {
      console.error('Failed to fetch operations:', error);
    }
  };

  // Fetch operation details
  useEffect(() => {
    if (selectedOperationId) {
      fetchOperationDetails(selectedOperationId);
    }
  }, [selectedOperationId]);

  const fetchOperationDetails = async (operationId: string) => {
    setIsLoadingReview(true);
    try {
      const response = await fetch(
        `/api/parallel/workspace/${workspaceId}/queue/operations/${operationId}/diff`
      );
      if (response.ok) {
        const data = await response.json();
        setOperationDetails(data);
      }
    } catch (error) {
      console.error('Failed to fetch operation details:', error);
    } finally {
      setIsLoadingReview(false);
    }
  };

  // Fetch run output review
  useEffect(() => {
    if (selectedRunId) {
      fetchRunOutputReview(selectedRunId);
    }
  }, [selectedRunId]);

  const fetchRunOutputReview = async (runId: string) => {
    setIsLoadingReview(true);
    try {
      // First, create a review if it doesn't exist
      const reviewResponse = await fetch(
        `/api/parallel/workspace/${workspaceId}/runs/${runId}/output-review`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            output_type: 'stdout',
            output_content: '', // Would fetch from actual run data
          }),
        }
      );

      if (reviewResponse.ok) {
        const reviewData = await reviewResponse.json();
        setOutputReview(reviewData);
      }
    } catch (error) {
      console.error('Failed to fetch output review:', error);
    } finally {
      setIsLoadingReview(false);
    }
  };

  // API handlers for review actions
  const handleAddComment = async (content: string, runId?: string) => {
    if (!selectedOperationId) return;

    try {
      await fetch(
        `/api/parallel/workspace/${workspaceId}/queue/operations/${selectedOperationId}/comment`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            content,
            run_id: runId,
            created_by: userId || 'system',
          }),
        }
      );
      // Refresh operation details
      await fetchOperationDetails(selectedOperationId);
    } catch (error) {
      console.error('Failed to add comment:', error);
    }
  };

  const handleApproveOperation = async () => {
    if (!selectedOperationId) return;

    try {
      await fetch(
        `/api/parallel/workspace/${workspaceId}/queue/operations/${selectedOperationId}/approve`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            approved_by: userId || 'system',
          }),
        }
      );
      // Refresh operation details
      await fetchOperationDetails(selectedOperationId);
    } catch (error) {
      console.error('Failed to approve operation:', error);
    }
  };

  const handleSubmitToAgent = async (agentId: string, instructions?: string) => {
    if (!selectedOperationId) return;

    try {
      await fetch(
        `/api/parallel/workspace/${workspaceId}/queue/operations/${selectedOperationId}/submit-to-agent`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            agent_id: agentId,
            instructions: instructions || '',
            approved_by: userId || 'system',
          }),
        }
      );
      // Refresh operation details
      await fetchOperationDetails(selectedOperationId);
    } catch (error) {
      console.error('Failed to submit to agent:', error);
    }
  };

  const handleAddLineComment = async (lineNumber: number, content: string) => {
    if (!selectedRunId || !outputReview) return;

    try {
      await fetch(
        `/api/parallel/workspace/${workspaceId}/runs/${selectedRunId}/output-review/${outputReview.review_id}/comment`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            line_number: lineNumber,
            content,
          }),
        }
      );
      // Refresh output review
      await fetchRunOutputReview(selectedRunId);
    } catch (error) {
      console.error('Failed to add line comment:', error);
    }
  };

  // Calculate dashboard metrics
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
      {/* Notifications Container (fixed, top-right) */}
      <QueueNotifications workspaceId={workspaceId} />

      {/* Main Layout */}
      <div className="dashboard-layout">
        {/* Header */}
        <header className="dashboard-header">
          <div className="header-title">
            <h1>Queue Dashboard</h1>
            <p className="workspace-indicator">Workspace: {workspaceId}</p>
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

            <div className={`connection-badge ${isWebSocket ? 'connected' : 'polling'}`}>
              {isWebSocket ? '🟢 Real-time' : '📡 Polling'}
            </div>
          </div>
        </header>

        {/* Main Content Area */}
        <div className="dashboard-content">
          {/* Primary Visualization */}
          <div className="visualization-section">
            <ParallelQueueVisualization workspaceId={workspaceId} userId={userId} />
          </div>

          {/* Sidebar Sections */}
          <aside className="dashboard-sidebar">
            {/* Tab Navigation */}
            <div className="sidebar-tabs">
              <button
                className={`tab-btn ${activeSidebarTab === 'overview' ? 'active' : ''}`}
                onClick={() => setActiveSidebarTab('overview')}
              >
                📊 Overview
              </button>

              <button
                className={`tab-btn ${activeSidebarTab === 'review' ? 'active' : ''}`}
                onClick={() => setActiveSidebarTab('review')}
              >
                💬 Review
              </button>

              {showControls && (
                <button
                  className={`tab-btn ${activeSidebarTab === 'controls' ? 'active' : ''}`}
                  onClick={() => setActiveSidebarTab('controls')}
                >
                  ⚙️ Controls
                </button>
              )}

              {showAnalytics && (
                <button
                  className={`tab-btn ${activeSidebarTab === 'analytics' ? 'active' : ''}`}
                  onClick={() => setActiveSidebarTab('analytics')}
                >
                  📈 Analytics
                </button>
              )}
            </div>

            {/* Tab Content */}
            <div className="sidebar-content">
              {activeSidebarTab === 'overview' && (
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

                  <div className="legend-section">
                    <h4>Status Legend</h4>
                    <div className="legend-items">
                      <div className="legend-item">
                        <span className="legend-color active"></span>
                        <span className="legend-text">Running</span>
                      </div>
                      <div className="legend-item">
                        <span className="legend-color queued"></span>
                        <span className="legend-text">Queued</span>
                      </div>
                      <div className="legend-item">
                        <span className="legend-color available"></span>
                        <span className="legend-text">Available</span>
                      </div>
                    </div>
                  </div>

                  <div className="connection-section">
                    <h4>Connection</h4>
                    <div className="connection-status">
                      <span className={`status-dot ${isWebSocket ? 'connected' : 'polling'}`}></span>
                      <span className="status-text">
                        {isWebSocket ? 'Real-time WebSocket' : 'Polling (5s interval)'}
                      </span>
                    </div>
                    <p className="connection-hint">
                      {isWebSocket
                        ? 'Updates arrive in real-time'
                        : 'Fallback to polling mode'}
                    </p>
                  </div>
                </div>
              )}

              {activeSidebarTab === 'review' && (
                <div className="review-panel">
                  {!selectedOperationId ? (
                    <div className="review-list">
                      <h3>Queue Operations</h3>
                      {operations.length === 0 ? (
                        <p className="no-items">No operations to review</p>
                      ) : (
                        <div className="operations-list">
                          {operations.map((op) => (
                            <button
                              key={op.id}
                              className={`operation-item ${
                                selectedOperationId === op.id ? 'selected' : ''
                              }`}
                              onClick={() => setSelectedOperationId(op.id)}
                            >
                              <span className="op-type">{op.operation_type}</span>
                              <span className="op-status">
                                {op.approved ? '✓ Approved' : '◯ Pending'}
                              </span>
                              <span className="op-affects">
                                {op.affected_count} runs
                              </span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  ) : operationDetails ? (
                    <div className="review-detail">
                      <button
                        className="back-btn"
                        onClick={() => {
                          setSelectedOperationId(null);
                          setOperationDetails(null);
                          setSelectedRunId(null);
                        }}
                      >
                        ← Back
                      </button>

                      {!selectedRunId ? (
                        <QueueOperationReview
                          operationId={operationDetails.operation_id}
                          comments={operationDetails.comments || []}
                          approved={operationDetails.approved}
                          approvedBy={operationDetails.approved_by}
                          onAddComment={handleAddComment}
                          onApprove={handleApproveOperation}
                          onSubmitToAgent={handleSubmitToAgent}
                          isLoading={isLoadingReview}
                        />
                      ) : outputReview ? (
                        <RunOutputReview
                          outputType={outputReview.output_type || 'stdout'}
                          lines={outputReview.lines || []}
                          approved={outputReview.approved}
                          approvedBy={outputReview.approved_by}
                          onAddComment={handleAddLineComment}
                          isLoading={isLoadingReview}
                        />
                      ) : null}
                    </div>
                  ) : (
                    <div className="loading">Loading operation details...</div>
                  )}
                </div>
              )}

              {activeSidebarTab === 'controls' && showControls && (
                <QueueAdvancedControls
                  workspaceId={workspaceId}
                  activeRuns={activeRuns || []}
                  queuedRuns={queuedRuns || []}
                />
              )}

              {activeSidebarTab === 'analytics' && showAnalytics && (
                <QueueHistoricalAnalytics workspaceId={workspaceId} />
              )}
            </div>
          </aside>
        </div>
      </div>

      {/* Performance Metrics (development only) */}
      {process.env.NODE_ENV === 'development' && (
        <div className="performance-monitor">
          <details>
            <summary>Performance Debug</summary>
            <div className="perf-stats">
              <p>Active Runs: {activeRuns?.length || 0}</p>
              <p>Queued Runs: {queuedRuns?.length || 0}</p>
              <p>Connection: {connectionState}</p>
              <p>WebSocket: {isWebSocket ? 'Yes' : 'No'}</p>
            </div>
          </details>
        </div>
      )}
    </div>
  );
}
