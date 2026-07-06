/**
 * Enhanced queue visualization with timeline, drag-to-reorder, and resource allocation.
 * Shows active slots, queued runs, and estimated completion times.
 */

import React, { useState, useMemo } from 'react';
import { useParallelExecutionWS } from '../hooks/useParallelExecutionWS';
import './ParallelQueueVisualization.css';

export interface ParallelQueueVisualizationProps {
  workspaceId: string;
  userId?: string;
}

interface SlotView {
  slotNumber: number;
  isActive: boolean;
  runId?: string;
  ticketId?: string;
  elapsedSeconds: number;
  estimatedTotalSeconds: number;
  status: string;
  progress: number;
}

interface QueueItemView {
  runId: string;
  ticketId: string;
  position: number;
  waitSeconds: number;
  estimatedStartAt: string;
  isDragging: boolean;
}

export function ParallelQueueVisualization({
  workspaceId,
  userId,
}: ParallelQueueVisualizationProps) {
  const { activeRuns, queuedRuns, stats, connectionState, isWebSocket } =
    useParallelExecutionWS(workspaceId, userId);

  const [draggedItem, setDraggedItem] = useState<string | null>(null);
  const [hoverPosition, setHoverPosition] = useState<number | null>(null);
  const [isReordering, setIsReordering] = useState(false);
  const [reorderError, setReorderError] = useState<string | null>(null);

  // Compute slot views
  const slotViews = useMemo(() => {
    const slots: SlotView[] = [];

    for (let i = 1; i <= (stats?.max_concurrent || 3); i++) {
      const activeRun = activeRuns?.find((r) => r.slot_number === i);

      if (activeRun) {
        const estimatedTotal = 300; // 5 minutes default estimate
        const progress = Math.min(100, (activeRun.elapsed_seconds / estimatedTotal) * 100);

        slots.push({
          slotNumber: i,
          isActive: true,
          runId: activeRun.run_id,
          ticketId: activeRun.ticket_id,
          elapsedSeconds: activeRun.elapsed_seconds,
          estimatedTotalSeconds: estimatedTotal,
          status: activeRun.status,
          progress,
        });
      } else {
        slots.push({
          slotNumber: i,
          isActive: false,
          estimatedTotalSeconds: 0,
          elapsedSeconds: 0,
          status: 'available',
          progress: 0,
        });
      }
    }

    return slots;
  }, [activeRuns, stats?.max_concurrent]);

  // Compute queue item views
  const queueItems = useMemo(() => {
    return (queuedRuns || []).map((run, index) => ({
      runId: run.run_id,
      ticketId: run.ticket_id,
      position: index + 1,
      waitSeconds: run.wait_seconds || 0,
      estimatedStartAt: run.estimated_start_at || '',
      isDragging: draggedItem === run.run_id,
    }));
  }, [queuedRuns, draggedItem]);

  // Calculate estimated system clear time
  const estimatedClearTime = useMemo(() => {
    if (!activeRuns || !queuedRuns) return 0;

    const activeRunsTime = 300; // Assume 5 min per active run
    const queuedTime = (queuedRuns.length || 0) * 300;
    return activeRunsTime + queuedTime;
  }, [activeRuns, queuedRuns]);

  const formatTime = (seconds: number) => {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${minutes}m ${Math.round(secs)}s`;
  };

  const formatEstimatedComplete = (dateString: string) => {
    try {
      const date = new Date(dateString);
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return 'Unknown';
    }
  };

  const handleReorderDrop = async (draggedRunId: string, newPosition: number) => {
    setIsReordering(true);
    setReorderError(null);

    try {
      const response = await fetch(
        `/api/parallel/queue/${draggedRunId}/reorder?new_position=${newPosition}`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
        }
      );

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        const errorMessage =
          errorData.detail || `Failed to reorder (${response.status})`;
        setReorderError(errorMessage);
        console.error('Reorder failed:', errorMessage);
        return;
      }

      const result = await response.json();
      if (result.status === 'no_change') {
        // Run already at this position, no action needed
        return;
      }

      if (result.status !== 'reordered') {
        setReorderError(`Reorder failed: ${result.status}`);
        return;
      }

      // Success - WebSocket will update the UI with new queue state
      console.log(`Reordered ${draggedRunId} to position ${newPosition}`);
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : 'Failed to reorder run';
      setReorderError(errorMessage);
      console.error('Reorder error:', error);
    } finally {
      setIsReordering(false);
    }
  };

  return (
    <div className="queue-visualization-container">
      {/* Header with connection status */}
      <div className="queue-header">
        <h2>Parallel Execution Queue</h2>
        <div className="connection-indicator">
          {isWebSocket ? (
            <span className="ws-indicator connected">🟢 Real-time</span>
          ) : (
            <span className="ws-indicator polling">📡 Polling</span>
          )}
          {connectionState && (
            <span className="connection-state">{connectionState}</span>
          )}
        </div>
      </div>

      {/* System Status Overview */}
      <div className="queue-overview">
        <div className="overview-card">
          <div className="overview-label">Slot Usage</div>
          <div className="overview-value">
            {stats?.active_count || 0}/{stats?.max_concurrent || 3}
          </div>
          <div className="overview-bar">
            <div
              className="overview-bar-fill"
              style={{
                width: `${(((stats?.active_count || 0) / (stats?.max_concurrent || 3)) * 100)}%`,
              }}
            />
          </div>
        </div>

        <div className="overview-card">
          <div className="overview-label">Queue Length</div>
          <div className="overview-value">{queuedRuns?.length || 0}</div>
          {(queuedRuns?.length || 0) > 0 && (
            <div className="overview-hint">
              {(queuedRuns?.length || 0) === 1 ? '1 waiting' : `${queuedRuns?.length} waiting`}
            </div>
          )}
        </div>

        <div className="overview-card">
          <div className="overview-label">Estimated Clear</div>
          <div className="overview-value">{formatTime(estimatedClearTime)}</div>
          {estimatedClearTime > 0 && (
            <div className="overview-hint">All runs complete in</div>
          )}
        </div>

        <div className="overview-card">
          <div className="overview-label">Wait Time</div>
          <div className="overview-value">
            {stats?.queue_wait_time_minutes || 0}m
          </div>
          {stats?.queue_wait_time_minutes ? (
            <div className="overview-hint">Oldest item waiting</div>
          ) : (
            <div className="overview-hint">No queue</div>
          )}
        </div>
      </div>

      {/* Execution Slots Timeline */}
      <div className="queue-slots-section">
        <h3>Execution Slots</h3>
        <div className="queue-slots">
          {slotViews.map((slot) => (
            <div
              key={`slot-${slot.slotNumber}`}
              className={`slot-card ${slot.isActive ? 'active' : 'available'}`}
              data-testid={`slot-${slot.slotNumber}`}
            >
              <div className="slot-header">
                <span className="slot-number">Slot {slot.slotNumber}</span>
                <span className="slot-status">{slot.status}</span>
              </div>

              {slot.isActive ? (
                <div className="slot-content">
                  <div className="slot-run-info">
                    <div className="run-id" title={slot.runId}>
                      {slot.ticketId}
                    </div>
                    <div className="run-time">
                      {formatTime(slot.elapsedSeconds)} / {formatTime(slot.estimatedTotalSeconds)}
                    </div>
                  </div>

                  <div className="slot-progress">
                    <div className="progress-bar">
                      <div
                        className={`progress-fill ${slot.status}`}
                        style={{ width: `${slot.progress}%` }}
                      />
                    </div>
                    <div className="progress-label">{Math.round(slot.progress)}%</div>
                  </div>
                </div>
              ) : (
                <div className="slot-content empty">
                  <div className="empty-text">Available</div>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Reorder Error Notification */}
      {reorderError && (
        <div className="queue-error-notification">
          <span className="error-icon">⚠️</span>
          <span className="error-message">{reorderError}</span>
          <button
            className="error-close"
            onClick={() => setReorderError(null)}
            aria-label="Dismiss error"
          >
            ✕
          </button>
        </div>
      )}

      {/* Queue List with Drag-to-Reorder */}
      {queuedRuns && queuedRuns.length > 0 && (
        <div className="queue-list-section">
          <h3>Queue ({queuedRuns.length})</h3>
          <div className="queue-list">
            {queueItems.map((item, index) => (
              <div
                key={item.runId}
                className={`queue-item ${item.isDragging ? 'dragging' : ''} ${
                  hoverPosition === index ? 'drag-over' : ''
                }`}
                draggable
                onDragStart={() => setDraggedItem(item.runId)}
                onDragOver={(e) => {
                  e.preventDefault();
                  setHoverPosition(index);
                }}
                onDragLeave={() => setHoverPosition(null)}
                onDrop={async () => {
                  if (draggedItem && draggedItem !== item.runId) {
                    // Calculate new position: hoverPosition is 0-indexed, but positions are 1-indexed
                    const newPosition = (hoverPosition ?? index) + 1;
                    await handleReorderDrop(draggedItem, newPosition);
                  }
                  setDraggedItem(null);
                  setHoverPosition(null);
                }}
                onDragEnd={() => {
                  setDraggedItem(null);
                  setHoverPosition(null);
                }}
                data-testid={`queue-item-${item.position}`}
              >
                <div className="queue-item-handle">⋮⋮</div>

                <div className="queue-item-content">
                  <div className="queue-item-info">
                    <span className="queue-position">#{item.position}</span>
                    <span className="queue-ticket" title={item.ticketId}>
                      {item.ticketId}
                    </span>
                  </div>

                  <div className="queue-item-time">
                    <span className="queue-wait-label">Wait:</span>
                    <span className="queue-wait-time">{formatTime(item.waitSeconds)}</span>
                    <span className="queue-est-label">Est. start:</span>
                    <span className="queue-est-time">
                      {formatEstimatedComplete(item.estimatedStartAt)}
                    </span>
                  </div>
                </div>

                <div className="queue-item-badge">
                  <span className="badge-text">Queued</span>
                </div>
              </div>
            ))}
          </div>
          <div className="queue-hint">💡 Drag items to reorder queue priority</div>
        </div>
      )}

      {/* Empty State */}
      {(!activeRuns || activeRuns.length === 0) && (!queuedRuns || queuedRuns.length === 0) && (
        <div className="queue-empty">
          <div className="empty-icon">✨</div>
          <p>All slots available</p>
          <p className="empty-hint">Ready for parallel execution</p>
        </div>
      )}

      {/* Legend */}
      <div className="queue-legend">
        <div className="legend-item">
          <div className="legend-color active" />
          <span>Running</span>
        </div>
        <div className="legend-item">
          <div className="legend-color queued" />
          <span>Queued</span>
        </div>
        <div className="legend-item">
          <div className="legend-color available" />
          <span>Available</span>
        </div>
      </div>
    </div>
  );
}
