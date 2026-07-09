/**
 * Shows retry status and information for failed runs
 */

import { useMemo } from 'react';
import './RunRetryIndicator.css';

export interface RetryInfo {
  retry_count: number;
  max_retries: number;
  failure_reason?: string;
  last_failed_at?: string;
  can_retry?: boolean;
}

export interface RunRetryIndicatorProps {
  retryInfo: RetryInfo;
  onRetry?: () => void;
  compact?: boolean;
  isLoading?: boolean;
}

export function RunRetryIndicator({
  retryInfo,
  onRetry,
  compact = false,
  isLoading = false,
}: RunRetryIndicatorProps) {
  const retryPercentage = useMemo(() => {
    return Math.round((retryInfo.retry_count / retryInfo.max_retries) * 100);
  }, [retryInfo.retry_count, retryInfo.max_retries]);

  const status = useMemo(() => {
    if (retryInfo.retry_count === 0) return 'never-failed';
    if (retryInfo.retry_count >= retryInfo.max_retries) return 'max-retries';
    return 'retryable';
  }, [retryInfo.retry_count, retryInfo.max_retries]);

  if (retryInfo.retry_count === 0 && !retryInfo.failure_reason) {
    return null;
  }

  if (compact) {
    return (
      <div className={`retry-indicator compact status-${status}`}>
        <span className="retry-count">
          {retryInfo.retry_count}/{retryInfo.max_retries}
        </span>
        {status === 'max-retries' && <span className="status-badge">Max</span>}
      </div>
    );
  }

  return (
    <div className={`retry-indicator full status-${status}`}>
      <div className="retry-header">
        <div className="retry-title">Retry History</div>
        <div className="retry-count-badge">
          {retryInfo.retry_count}/{retryInfo.max_retries}
        </div>
      </div>

      <div className="retry-progress">
        <div className="progress-bar">
          <div
            className="progress-fill"
            style={{ width: `${retryPercentage}%` }}
          ></div>
        </div>
        <div className="progress-text">{retryPercentage}% exhausted</div>
      </div>

      {retryInfo.failure_reason && (
        <div className="failure-reason">
          <div className="reason-label">Last Error</div>
          <div className="reason-text">{retryInfo.failure_reason}</div>
        </div>
      )}

      {retryInfo.last_failed_at && (
        <div className="last-failed">
          <div className="time-label">Failed at</div>
          <div className="time-value">
            {new Date(retryInfo.last_failed_at).toLocaleString()}
          </div>
        </div>
      )}

      <div className="retry-status">
        {status === 'max-retries' && (
          <div className="status-message error">
            ✕ Maximum retries exceeded
          </div>
        )}

        {status === 'retryable' && (
          <div className="status-message warning">
            ⚠ Can retry {retryInfo.max_retries - retryInfo.retry_count} more{' '}
            time{retryInfo.max_retries - retryInfo.retry_count === 1 ? '' : 's'}
          </div>
        )}
      </div>

      {retryInfo.can_retry && onRetry && status !== 'max-retries' && (
        <button
          className="btn-retry-now"
          onClick={onRetry}
          disabled={isLoading}
          aria-label="Retry now"
        >
          {isLoading ? '⏳ Retrying...' : '🔄 Retry Now'}
        </button>
      )}
    </div>
  );
}
