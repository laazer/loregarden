/**
 * Advanced queue control panel for solo developers
 * Pause/resume runs, cancel queued runs, manual slot management
 */

import { useState } from 'react';
import type { ActiveRun, QueuedRun } from '../hooks/useParallelExecution';
import './QueueAdvancedControls.css';

export type { ActiveRun, QueuedRun };

export interface QueueAdvancedControlsProps {
  workspaceId: string;
  activeRuns: ActiveRun[];
  queuedRuns: QueuedRun[];
  onRunControl?: (action: string, runId: string) => Promise<void>;
}

export function QueueAdvancedControls({
  activeRuns = [],
  queuedRuns = [],
  onRunControl,
}: QueueAdvancedControlsProps) {
  const [expandedRun, setExpandedRun] = useState<string | null>(null);
  const [selectedRuns, setSelectedRuns] = useState<Set<string>>(new Set());
  const [isProcessing, setIsProcessing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleRunAction = async (action: string, runId: string) => {
    setIsProcessing(runId);
    setError(null);

    try {
      if (onRunControl) {
        await onRunControl(action, runId);
      } else {
        // Default API call
        const response = await fetch(
          `/api/parallel/queue/${runId}/${action}`,
          { method: 'POST' }
        );

        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          throw new Error(data.detail || `Failed to ${action}`);
        }
      }

      setExpandedRun(null);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : `Failed to ${action} run`
      );
    } finally {
      setIsProcessing(null);
    }
  };

  const handleBulkAction = async (action: string) => {
    if (selectedRuns.size === 0) return;

    setIsProcessing('bulk');
    setError(null);

    try {
      const results = await Promise.allSettled(
        Array.from(selectedRuns).map((runId) =>
          fetch(`/api/parallel/queue/${runId}/${action}`, {
            method: 'POST',
          })
        )
      );

      const failed = results.filter((r) => r.status === 'rejected').length;
      if (failed > 0) {
        setError(`Failed to ${action} ${failed} run(s)`);
      }

      setSelectedRuns(new Set());
    } catch (err) {
      setError(
        err instanceof Error ? err.message : `Failed to ${action} runs`
      );
    } finally {
      setIsProcessing(null);
    }
  };

  const toggleRunSelection = (runId: string) => {
    const newSelected = new Set(selectedRuns);
    if (newSelected.has(runId)) {
      newSelected.delete(runId);
    } else {
      newSelected.add(runId);
    }
    setSelectedRuns(newSelected);
  };

  return (
    <div className="queue-advanced-controls">
      <div className="controls-header">
        <h3>Queue Controls</h3>
        {selectedRuns.size > 0 && (
          <span className="selection-badge">{selectedRuns.size} selected</span>
        )}
      </div>

      {error && (
        <div className="controls-error">
          <span>{error}</span>
          <button
            className="error-dismiss"
            onClick={() => setError(null)}
            aria-label="Dismiss error"
          >
            ✕
          </button>
        </div>
      )}

      {/* Active Runs Controls */}
      {activeRuns.length > 0 && (
        <div className="controls-section">
          <div className="section-title">Active Runs</div>
          <div className="runs-list">
            {activeRuns.map((run) => (
              <div
                key={run.run_id}
                className={`run-control-item active ${
                  selectedRuns.has(run.run_id) ? 'selected' : ''
                }`}
              >
                <input
                  type="checkbox"
                  checked={selectedRuns.has(run.run_id)}
                  onChange={() => toggleRunSelection(run.run_id)}
                  aria-label={`Select ${run.ticket_id}`}
                />

                <div className="run-info">
                  <div className="run-ticket">{run.ticket_id}</div>
                  <div className="run-detail">
                    Slot {run.slot_number} • {run.elapsed_seconds}s elapsed
                  </div>
                </div>

                <button
                  className="run-control-toggle"
                  onClick={() =>
                    setExpandedRun(
                      expandedRun === run.run_id ? null : run.run_id
                    )
                  }
                  aria-expanded={expandedRun === run.run_id}
                  aria-label="Toggle controls"
                >
                  ⋯
                </button>

                {expandedRun === run.run_id && (
                  <div className="run-actions">
                    <button
                      className="action-btn pause"
                      onClick={() =>
                        handleRunAction('pause', run.run_id)
                      }
                      disabled={isProcessing === run.run_id}
                    >
                      {isProcessing === run.run_id ? '⏳' : '⏸'} Pause
                    </button>
                    <button
                      className="action-btn cancel"
                      onClick={() =>
                        handleRunAction('cancel', run.run_id)
                      }
                      disabled={isProcessing === run.run_id}
                    >
                      {isProcessing === run.run_id ? '⏳' : '⏹'} Cancel
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Queued Runs Controls */}
      {queuedRuns.length > 0 && (
        <div className="controls-section">
          <div className="section-title">Queued Runs</div>
          <div className="runs-list">
            {queuedRuns.map((run) => (
              <div
                key={run.run_id}
                className={`run-control-item queued ${
                  selectedRuns.has(run.run_id) ? 'selected' : ''
                }`}
              >
                <input
                  type="checkbox"
                  checked={selectedRuns.has(run.run_id)}
                  onChange={() => toggleRunSelection(run.run_id)}
                  aria-label={`Select ${run.ticket_id}`}
                />

                <div className="run-info">
                  <div className="run-ticket">{run.ticket_id}</div>
                  <div className="run-detail">
                    #{run.position} • Waiting {Math.round(run.wait_seconds / 60)}m
                  </div>
                </div>

                <button
                  className="run-control-toggle"
                  onClick={() =>
                    setExpandedRun(
                      expandedRun === run.run_id ? null : run.run_id
                    )
                  }
                  aria-expanded={expandedRun === run.run_id}
                  aria-label="Toggle controls"
                >
                  ⋯
                </button>

                {expandedRun === run.run_id && (
                  <div className="run-actions">
                    <button
                      className="action-btn promote"
                      onClick={() =>
                        handleRunAction('promote', run.run_id)
                      }
                      disabled={isProcessing === run.run_id}
                    >
                      {isProcessing === run.run_id ? '⏳' : '⬆'} Promote
                    </button>
                    <button
                      className="action-btn cancel"
                      onClick={() =>
                        handleRunAction('cancel', run.run_id)
                      }
                      disabled={isProcessing === run.run_id}
                    >
                      {isProcessing === run.run_id ? '⏳' : '✕'} Cancel
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Bulk Actions */}
      {selectedRuns.size > 0 && (
        <div className="bulk-actions">
          <button
            className="bulk-btn cancel"
            onClick={() => handleBulkAction('cancel')}
            disabled={isProcessing === 'bulk'}
          >
            {isProcessing === 'bulk' ? '⏳' : '✕'} Cancel Selected ({selectedRuns.size})
          </button>
          <button
            className="bulk-btn clear"
            onClick={() => setSelectedRuns(new Set())}
            disabled={isProcessing === 'bulk'}
          >
            Clear Selection
          </button>
        </div>
      )}

      {/* Empty State */}
      {activeRuns.length === 0 && queuedRuns.length === 0 && (
        <div className="controls-empty">
          <p>No active or queued runs</p>
        </div>
      )}
    </div>
  );
}
