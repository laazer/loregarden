/**
 * Panel showing failed runs with retry and skip options
 */

import { useMemo } from 'react';
import { RunRetryIndicator } from './RunRetryIndicator';
import './FailedRunsPanel.css';

export interface FailedRun {
  run_id: string;
  ticket_id: string;
  retry_count: number;
  max_retries: number;
  failure_reason?: string;
  last_failed_at?: string;
  can_retry: boolean;
}

export interface FailedRunsPanelProps {
  failedRuns: FailedRun[];
  onRetryRun?: (runId: string) => void;
  onRetryAll?: () => void;
  onSkipAll?: () => void;
  isLoading?: boolean;
  isEmpty?: boolean;
}

export function FailedRunsPanel({
  failedRuns,
  onRetryRun,
  onRetryAll,
  onSkipAll,
  isLoading = false,
  isEmpty = false,
}: FailedRunsPanelProps) {
  const retryableCount = useMemo(
    () => failedRuns.filter((r) => r.can_retry).length,
    [failedRuns]
  );

  const exhaustedCount = useMemo(
    () => failedRuns.filter((r) => !r.can_retry).length,
    [failedRuns]
  );

  if (isEmpty || failedRuns.length === 0) {
    return (
      <div className="failed-runs-panel empty">
        <div className="empty-state">
          <div className="empty-icon">✓</div>
          <div className="empty-text">No failed runs</div>
          <div className="empty-hint">All runs executing successfully</div>
        </div>
      </div>
    );
  }

  return (
    <div className="failed-runs-panel">
      <div className="panel-header">
        <h3>Failed Runs</h3>
        <div className="panel-stats">
          <span className="stat retryable">
            {retryableCount} retryable
          </span>
          {exhaustedCount > 0 && (
            <span className="stat exhausted">
              {exhaustedCount} maxed out
            </span>
          )}
        </div>
      </div>

      <div className="panel-actions">
        {retryableCount > 0 && onRetryAll && (
          <button
            className="btn btn-retry-all"
            onClick={onRetryAll}
            disabled={isLoading}
            aria-label="Retry all failed runs"
          >
            🔄 Retry All ({retryableCount})
          </button>
        )}

        {onSkipAll && (
          <button
            className="btn btn-skip-all"
            onClick={onSkipAll}
            disabled={isLoading}
            aria-label="Skip all failed runs"
          >
            ⏭ Skip All ({failedRuns.length})
          </button>
        )}
      </div>

      <div className="runs-list">
        {failedRuns.map((run) => (
          <div key={run.run_id} className="run-item">
            <div className="run-header">
              <div className="run-identity">
                <span className="run-id-label">Run ID</span>
                <span className="run-id-value">{run.run_id}</span>
              </div>
              <div className="run-ticket">
                <span className="ticket-label">Ticket</span>
                <span className="ticket-value">{run.ticket_id}</span>
              </div>
            </div>

            <div className="run-retry-info">
              <RunRetryIndicator
                retryInfo={{
                  retry_count: run.retry_count,
                  max_retries: run.max_retries,
                  failure_reason: run.failure_reason,
                  last_failed_at: run.last_failed_at,
                  can_retry: run.can_retry,
                }}
                onRetry={
                  run.can_retry && onRetryRun
                    ? () => onRetryRun(run.run_id)
                    : undefined
                }
                isLoading={isLoading}
              />
            </div>

            {run.can_retry && onRetryRun && (
              <button
                className="btn-run-retry"
                onClick={() => onRetryRun(run.run_id)}
                disabled={isLoading}
                aria-label={`Retry run ${run.run_id}`}
              >
                Retry Now
              </button>
            )}

            {!run.can_retry && (
              <div className="exhausted-badge">
                Max retries exceeded
              </div>
            )}
          </div>
        ))}
      </div>

      {isLoading && (
        <div className="panel-loading">
          <div className="spinner"></div>
          <span>Processing...</span>
        </div>
      )}
    </div>
  );
}
