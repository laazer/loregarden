/**
 * Historical Analytics for queue performance
 * Tracks run duration, success rates, and performance trends over time
 */

import React, { useState, useEffect } from 'react';
import './QueueHistoricalAnalytics.css';

export interface RunMetrics {
  ticket_type: string;
  count: number;
  avg_duration_seconds: number;
  min_duration_seconds: number;
  max_duration_seconds: number;
  success_rate: number;
  last_7_days_count: number;
  last_7_days_success_rate: number;
}

export interface QueueAnalyticsProps {
  workspaceId: string;
}

export function QueueHistoricalAnalytics({
  workspaceId,
}: QueueAnalyticsProps) {
  const [metrics, setMetrics] = useState<RunMetrics[]>([]);
  const [timeRange, setTimeRange] = useState<'7d' | '30d' | '90d'>('7d');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchMetrics = async () => {
      setLoading(true);
      setError(null);

      try {
        const response = await fetch(
          `/api/parallel/workspace/${workspaceId}/analytics?range=${timeRange}`
        );

        if (!response.ok) {
          throw new Error('Failed to fetch analytics');
        }

        const data = await response.json();
        setMetrics(data.metrics || []);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : 'Failed to load analytics'
        );
      } finally {
        setLoading(false);
      }
    };

    fetchMetrics();
  }, [workspaceId, timeRange]);

  const formatDuration = (seconds: number) => {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const minutes = Math.floor(seconds / 60);
    return `${minutes}m`;
  };

  const getSuccessColor = (rate: number) => {
    if (rate >= 0.95) return 'excellent';
    if (rate >= 0.85) return 'good';
    if (rate >= 0.75) return 'fair';
    return 'poor';
  };

  return (
    <div className="queue-analytics-container">
      <div className="analytics-header">
        <h3>Run Performance History</h3>
        <div className="time-range-selector">
          {(['7d', '30d', '90d'] as const).map((range) => (
            <button
              key={range}
              className={`range-btn ${timeRange === range ? 'active' : ''}`}
              onClick={() => setTimeRange(range)}
            >
              {range === '7d' && 'Last 7 days'}
              {range === '30d' && 'Last 30 days'}
              {range === '90d' && 'Last 90 days'}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="analytics-error">
          <span>{error}</span>
        </div>
      )}

      {loading ? (
        <div className="analytics-loading">
          <span>Loading analytics...</span>
        </div>
      ) : metrics.length === 0 ? (
        <div className="analytics-empty">
          <p>No run history available yet</p>
          <p className="empty-hint">
            Run some tickets to see performance trends
          </p>
        </div>
      ) : (
        <div className="analytics-grid">
          {metrics.map((metric) => (
            <div key={metric.ticket_type} className="analytics-card">
              <div className="card-header">
                <div className="ticket-type">{metric.ticket_type}</div>
                <div
                  className={`success-badge ${getSuccessColor(
                    metric.success_rate
                  )}`}
                >
                  {Math.round(metric.success_rate * 100)}%
                </div>
              </div>

              <div className="card-metrics">
                <div className="metric-row">
                  <span className="metric-label">Total Runs</span>
                  <span className="metric-value">{metric.count}</span>
                </div>

                <div className="metric-row">
                  <span className="metric-label">Avg Duration</span>
                  <span className="metric-value">
                    {formatDuration(metric.avg_duration_seconds)}
                  </span>
                </div>

                <div className="metric-row">
                  <span className="metric-label">Range</span>
                  <span className="metric-value">
                    {formatDuration(metric.min_duration_seconds)} —{' '}
                    {formatDuration(metric.max_duration_seconds)}
                  </span>
                </div>

                <div className="metric-row">
                  <span className="metric-label">Last 7 Days</span>
                  <span className="metric-value">
                    {metric.last_7_days_count} runs (
                    {Math.round(metric.last_7_days_success_rate * 100)}%)
                  </span>
                </div>
              </div>

              <div className="success-bar">
                <div
                  className={`success-fill ${getSuccessColor(
                    metric.success_rate
                  )}`}
                  style={{ width: `${metric.success_rate * 100}%` }}
                />
              </div>

              <div className="card-insights">
                {metric.avg_duration_seconds < 120 && (
                  <div className="insight fast">
                    ⚡ Fast execution
                  </div>
                )}
                {metric.success_rate >= 0.95 && (
                  <div className="insight reliable">
                    ✓ Highly reliable
                  </div>
                )}
                {metric.success_rate < 0.85 && (
                  <div className="insight concerning">
                    ⚠️ Success rate needs attention
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Summary Statistics */}
      {metrics.length > 0 && (
        <div className="analytics-summary">
          <div className="summary-card">
            <div className="summary-label">Overall Success Rate</div>
            <div className="summary-value">
              {Math.round(
                (metrics.reduce((sum, m) => sum + m.success_rate, 0) /
                  metrics.length) *
                  100
              )}
              %
            </div>
          </div>

          <div className="summary-card">
            <div className="summary-label">Total Runs Completed</div>
            <div className="summary-value">
              {metrics.reduce((sum, m) => sum + m.count, 0)}
            </div>
          </div>

          <div className="summary-card">
            <div className="summary-label">Average Run Duration</div>
            <div className="summary-value">
              {formatDuration(
                metrics.reduce((sum, m) => sum + m.avg_duration_seconds, 0) /
                  metrics.length
              )}
            </div>
          </div>

          <div className="summary-card">
            <div className="summary-label">Ticket Types Tracked</div>
            <div className="summary-value">{metrics.length}</div>
          </div>
        </div>
      )}
    </div>
  );
}
