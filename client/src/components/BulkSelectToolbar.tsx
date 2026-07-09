/**
 * Toolbar for bulk queue operations (select, cancel, pause, reorder multiple)
 */

import { useMemo } from 'react';
import './BulkSelectToolbar.css';

export interface BulkSelectToolbarProps {
  selectedRunIds: Set<string>;
  totalRuns: number;
  onSelectAll: () => void;
  onClearSelection: () => void;
  onBulkCancel: (runIds: string[]) => void;
  onBulkPause: (runIds: string[]) => void;
  onBulkRetry?: (runIds: string[]) => void;
  isLoading?: boolean;
  failedCount?: number;
}

export function BulkSelectToolbar({
  selectedRunIds,
  totalRuns,
  onSelectAll,
  onClearSelection,
  onBulkCancel,
  onBulkPause,
  onBulkRetry,
  isLoading = false,
  failedCount = 0,
}: BulkSelectToolbarProps) {
  const selectedCount = selectedRunIds.size;
  const isAllSelected = selectedCount === totalRuns && totalRuns > 0;
  const hasSelection = selectedCount > 0;

  const selectionSummary = useMemo(() => {
    if (selectedCount === 0) return 'No selection';
    if (isAllSelected) return `All ${totalRuns} selected`;
    return `${selectedCount} of ${totalRuns} selected`;
  }, [selectedCount, totalRuns, isAllSelected]);

  const handleBulkCancel = () => {
    if (
      window.confirm(
        `Cancel ${selectedCount} run${selectedCount === 1 ? '' : 's'}?`
      )
    ) {
      onBulkCancel(Array.from(selectedRunIds));
    }
  };

  const handleBulkPause = () => {
    if (
      window.confirm(
        `Pause ${selectedCount} run${selectedCount === 1 ? '' : 's'}?`
      )
    ) {
      onBulkPause(Array.from(selectedRunIds));
    }
  };

  const handleBulkRetry = () => {
    if (onBulkRetry) {
      onBulkRetry(Array.from(selectedRunIds));
    }
  };

  return (
    <div className="bulk-select-toolbar">
      <div className="toolbar-left">
        <div className="selection-status">
          <input
            type="checkbox"
            checked={isAllSelected}
            onChange={() => (isAllSelected ? onClearSelection() : onSelectAll())}
            aria-label="Select all runs"
          />
          <span className="selection-summary">{selectionSummary}</span>
        </div>
      </div>

      <div className="toolbar-center">
        {hasSelection && (
          <div className="selection-badge">{selectedCount}</div>
        )}
      </div>

      <div className="toolbar-right">
        {hasSelection && (
          <div className="action-group">
            <button
              className="btn btn-primary"
              onClick={handleBulkPause}
              disabled={isLoading}
              aria-label={`Pause ${selectedCount} run(s)`}
            >
              ⏸ Pause ({selectedCount})
            </button>

            <button
              className="btn btn-danger"
              onClick={handleBulkCancel}
              disabled={isLoading}
              aria-label={`Cancel ${selectedCount} run(s)`}
            >
              ✕ Cancel ({selectedCount})
            </button>

            {onBulkRetry && (
              <button
                className="btn btn-secondary"
                onClick={handleBulkRetry}
                disabled={isLoading}
                aria-label={`Retry ${selectedCount} run(s)`}
              >
                🔄 Retry ({selectedCount})
              </button>
            )}

            {failedCount > 0 && (
              <div className="failed-badge">
                {failedCount} failed
              </div>
            )}

            <button
              className="btn btn-ghost"
              onClick={onClearSelection}
              disabled={isLoading}
              aria-label="Clear selection"
            >
              Clear
            </button>
          </div>
        )}

        {!hasSelection && failedCount > 0 && (
          <div className="failed-alert">
            <span className="alert-icon">⚠</span>
            <span>{failedCount} failed run{failedCount === 1 ? '' : 's'}</span>
          </div>
        )}
      </div>

      {isLoading && (
        <div className="toolbar-loading">
          <div className="spinner"></div>
          <span>Processing...</span>
        </div>
      )}
    </div>
  );
}
