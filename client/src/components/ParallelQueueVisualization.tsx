/**
 * The queue, as the v6 design draws it: slot usage, execution slots, and the
 * runs waiting behind them.
 *
 * Two things here used to be invented. Every progress bar measured against a
 * hardcoded 300-second run, and "estimated clear" was 300 plus 300 per queued
 * run — so a two-second run and a twenty-minute run drew identical bars, and a
 * queue of three across three slots read as three times the wait it was. Both
 * now come from the server's median of what that agent has actually taken, and
 * when there is no history to draw on the UI says so rather than guessing.
 */

import { useState, useMemo } from 'react';
import { API_BASE } from '../api/client';
import { IconCloseButton } from './IconCloseButton';
import { QueueDispatchButton } from './QueueDispatchButton';
import { useQueueStatus } from '../state/QueueStatusContext';
import './ParallelQueueVisualization.css';

interface SlotView {
  slotNumber: number;
  isActive: boolean;
  runId?: string;
  title: string;
  subtitle: string;
  elapsedSeconds: number;
  status: string;
  /** Null when the workspace has no run history — render indeterminate. */
  progress: number | null;
}

interface QueueItemView {
  runId: string;
  title: string;
  subtitle: string;
  position: number;
  waitSeconds: number;
  estimatedStartAt: string;
  isDragging: boolean;
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${minutes}m ${secs}s`;
}

function formatClock(dateString: string): string {
  if (!dateString) return '—';
  const date = new Date(dateString);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function ParallelQueueVisualization() {
  const { activeRuns, queuedRuns, stats, estimatedClearSeconds, isWebSocket } =
    useQueueStatus();

  const [draggedItem, setDraggedItem] = useState<string | null>(null);
  const [hoverPosition, setHoverPosition] = useState<number | null>(null);
  const [reorderError, setReorderError] = useState<string | null>(null);

  const slotViews = useMemo(() => {
    const slots: SlotView[] = [];

    for (let i = 1; i <= (stats?.max_concurrent || 3); i++) {
      const run = activeRuns?.find((r) => r.slot_number === i);

      if (!run) {
        slots.push({
          slotNumber: i,
          isActive: false,
          title: 'Available',
          subtitle: 'Idle · ready for dispatch',
          elapsedSeconds: 0,
          status: 'available',
          progress: 0,
        });
        continue;
      }

      const estimate = run.estimated_duration_seconds;
      slots.push({
        slotNumber: i,
        isActive: true,
        runId: run.run_id,
        // The id is the fallback, not the label: a slot card is the one place
        // in the app where you should be able to read what is running.
        title: run.ticket_title || run.ticket_id,
        subtitle: [run.agent_name || run.agent_id, run.stage_key].filter(Boolean).join(' · '),
        elapsedSeconds: run.elapsed_seconds,
        status: run.status,
        progress:
          estimate && estimate > 0
            ? Math.min(100, (run.elapsed_seconds / estimate) * 100)
            : null,
      });
    }

    return slots;
  }, [activeRuns, stats?.max_concurrent]);

  const queueItems = useMemo<QueueItemView[]>(
    () =>
      (queuedRuns || []).map((run, index) => ({
        runId: run.run_id,
        title: run.ticket_title || run.ticket_id,
        subtitle: [run.agent_name || run.agent_id, run.stage_key].filter(Boolean).join(' · '),
        position: index + 1,
        waitSeconds: run.wait_seconds || 0,
        estimatedStartAt: run.estimated_start_at || '',
        isDragging: draggedItem === run.run_id,
      })),
    [queuedRuns, draggedItem]
  );

  const slotUsagePercent = stats?.max_concurrent
    ? ((stats.active_count || 0) / stats.max_concurrent) * 100
    : 0;

  const handleReorderDrop = async (draggedRunId: string, newPosition: number) => {
    setReorderError(null);

    try {
      const response = await fetch(
        `${API_BASE}/api/parallel/queue/${draggedRunId}/reorder?new_position=${newPosition}`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' } }
      );

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        setReorderError(errorData.detail || `Failed to reorder (${response.status})`);
        return;
      }

      const result = await response.json();
      if (result.status === 'no_change') return;
      if (result.status !== 'reordered') {
        setReorderError(`Reorder failed: ${result.status}`);
      }
      // Success needs no local update — the socket pushes the new order.
    } catch (error) {
      setReorderError(error instanceof Error ? error.message : 'Failed to reorder run');
    }
  };

  return (
    <div className="queue-panel">
      <div className="queue-panel-head">
        <h2 className="queue-panel-title">Parallel Execution Queue</h2>
        <div className={`queue-conn${isWebSocket ? ' queue-conn--live' : ''}`}>
          <span className="queue-conn-dot" aria-hidden />
          {isWebSocket ? 'Connected' : 'Polling'}
        </div>
      </div>

      <div className="queue-stat-grid">
        <div className="queue-stat">
          <div className="queue-stat-label">Slot usage</div>
          <div className="queue-stat-value">
            {stats?.active_count || 0}/{stats?.max_concurrent || 3}
          </div>
          <div className="queue-stat-bar">
            <div className="queue-stat-bar-fill" style={{ width: `${slotUsagePercent}%` }} />
          </div>
        </div>

        <div className="queue-stat">
          <div className="queue-stat-label">Queue length</div>
          <div className="queue-stat-value">{queuedRuns?.length || 0}</div>
          <div className="queue-stat-sub">
            {queuedRuns?.length ? 'runs waiting' : 'no queue'}
          </div>
        </div>

        <div className="queue-stat">
          <div className="queue-stat-label">Est. clear</div>
          <div className="queue-stat-value">
            {estimatedClearSeconds === null ? '—' : formatDuration(estimatedClearSeconds)}
          </div>
          <div className="queue-stat-sub">
            {estimatedClearSeconds === null
              ? 'no run history yet'
              : 'all runs complete in'}
          </div>
        </div>

        <div className="queue-stat">
          <div className="queue-stat-label">Wait time</div>
          <div className="queue-stat-value">{stats?.queue_wait_time_minutes || 0}m</div>
          <div className="queue-stat-sub">
            {stats?.queue_wait_time_minutes ? 'oldest item waiting' : 'no queue'}
          </div>
        </div>
      </div>

      <div className="queue-section-head">
        <span className="queue-section-title">Execution slots</span>
        <QueueDispatchButton />
      </div>

      <div className="queue-slot-grid">
        {slotViews.map((slot) => (
          <div
            key={`slot-${slot.slotNumber}`}
            className={`queue-slot${slot.isActive ? ' queue-slot--busy' : ''}`}
            data-testid={`slot-${slot.slotNumber}`}
          >
            <div className="queue-slot-head">
              <span className="queue-slot-dot" aria-hidden />
              <span className="queue-slot-name">Slot {slot.slotNumber}</span>
              <span className="queue-slot-badge">
                {slot.isActive ? 'running' : 'available'}
              </span>
            </div>

            <div className="queue-slot-body">
              <div className="queue-slot-title">{slot.title}</div>
              <div className="queue-slot-sub">{slot.subtitle}</div>

              {slot.isActive ? (
                <>
                  <div className="queue-slot-time">
                    {formatDuration(slot.elapsedSeconds)} elapsed
                  </div>
                  {slot.progress === null ? (
                    // No median to measure against. An indeterminate bar says
                    // "running, duration unknown"; a percentage would be made up.
                    <div
                      className="queue-slot-bar queue-slot-bar--indeterminate"
                      data-testid={`slot-${slot.slotNumber}-progress-unknown`}
                    >
                      <div className="queue-slot-bar-fill" />
                    </div>
                  ) : (
                    <div className="queue-slot-bar">
                      <div
                        className="queue-slot-bar-fill"
                        style={{ width: `${slot.progress}%` }}
                      />
                    </div>
                  )}
                </>
              ) : null}
            </div>
          </div>
        ))}
      </div>

      {reorderError ? (
        <div className="queue-inline-error" role="alert">
          <span className="queue-inline-error-dot" aria-hidden />
          <span className="queue-inline-error-message">{reorderError}</span>
          <IconCloseButton onClick={() => setReorderError(null)} aria-label="Dismiss error" />
        </div>
      ) : null}

      {queuedRuns && queuedRuns.length > 0 ? (
        <div className="queue-waiting">
          <div className="queue-section-head">
            <span className="queue-section-title">Waiting</span>
            <span className="queue-section-hint">Drag to reorder priority</span>
          </div>

          <div className="queue-waiting-list">
            {queueItems.map((item, index) => (
              <div
                key={item.runId}
                className={`queue-waiting-row${item.isDragging ? ' is-dragging' : ''}${
                  hoverPosition === index ? ' is-drop-target' : ''
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
                    // hoverPosition is 0-indexed; queue positions are 1-indexed.
                    await handleReorderDrop(draggedItem, (hoverPosition ?? index) + 1);
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
                <span className="queue-waiting-position">{item.position}</span>
                <div className="queue-waiting-copy">
                  <div className="queue-waiting-title">{item.title}</div>
                  <div className="queue-waiting-sub">{item.subtitle}</div>
                </div>
                <div className="queue-waiting-times">
                  <span className="queue-waiting-wait">
                    waited {formatDuration(item.waitSeconds)}
                  </span>
                  <span className="queue-waiting-start">
                    starts ~{formatClock(item.estimatedStartAt)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {!activeRuns?.length && !queuedRuns?.length ? (
        <div className="queue-idle">All slots open — dispatch a run to get started.</div>
      ) : null}
    </div>
  );
}
