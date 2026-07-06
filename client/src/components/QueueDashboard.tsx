/**
 * Unified queue management dashboard for solo developers
 * Combines visualization, controls, notifications, and analytics
 */

import React, { useState, useMemo } from 'react';
import { ParallelQueueVisualization } from './ParallelQueueVisualization';
import { QueueNotifications } from './QueueNotifications';
import { QueueAdvancedControls } from './QueueAdvancedControls';
import { QueueHistoricalAnalytics } from './QueueHistoricalAnalytics';
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
    'overview' | 'controls' | 'analytics'
  >('overview');

  const [expandedSections, setExpandedSections] = useState<Set<string>>(
    new Set(['overview'])
  );

  const toggleSection = (sectionId: string) => {
    const newSections = new Set(expandedSections);
    if (newSections.has(sectionId)) {
      newSections.delete(sectionId);
    } else {
      newSections.add(sectionId);
    }
    setExpandedSections(newSections);
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
