/**
 * Gantt-style timeline visualization for parallel agent execution.
 * Shows execution slots, queue wait times, and agent run durations.
 */

import React, { useMemo } from 'react';
import { useParallelExecution, ActiveRun, QueuedRun } from '../hooks/useParallelExecution';
import './ParallelExecutionTimeline.css';

export interface ParallelExecutionTimelineProps {
  workspaceId: string;
  maxDuration?: number; // Max seconds to show (default 600 = 10min)
}

export function ParallelExecutionTimeline({
  workspaceId,
  maxDuration = 600,
}: ParallelExecutionTimelineProps) {
  const { activeRuns, queuedRuns, stats, loading, error } = useParallelExecution(
    workspaceId,
    5000
  );

  const timelineData = useMemo(() => {
    const slotTimelines = new Map<number, TimelineEntry[]>();

    // Initialize all slots
    for (let i = 1; i <= stats.max_concurrent; i++) {
      slotTimelines.set(i, []);
    }

    // Add active runs
    activeRuns.forEach((run) => {
      if (slotTimelines.has(run.slot_number)) {
        slotTimelines.get(run.slot_number)!.push({
          type: 'active',
          run_id: run.run_id,
          ticket_id: run.ticket_id,
          agent_id: run.agent_id,
          startTime: Math.max(0, Math.ceil(run.elapsed_seconds)),
          duration: 60, // Estimate remaining time
          status: run.status,
        });
      }
    });

    // Add queued runs (show as waiting)
    queuedRuns.forEach((run, index) => {
      if (slotTimelines.has(index + 1)) {
        const waitSeconds = Math.ceil(run.wait_seconds);
        slotTimelines.get(index + 1)!.push({
          type: 'queued',
          run_id: run.run_id,
          ticket_id: run.ticket_id,
          agent_id: run.agent_id,
          startTime: activeRuns.length > 0 ?
            Math.max(...activeRuns.map(r => r.elapsed_seconds)) + (index * 300) :
            index * 300,
          duration: 300, // Estimated queue wait
          status: 'queued',
        });
      }
    });

    return slotTimelines;
  }, [activeRuns, queuedRuns, stats.max_concurrent]);

  if (loading && activeRuns.length === 0 && queuedRuns.length === 0) {
    return (
      <div className="timeline-container loading">
        Loading timeline...
      </div>
    );
  }

  return (
    <div className="timeline-container">
      {error && (
        <div className="timeline-error" data-testid="timeline-error">
          ⚠️ {error}
        </div>
      )}

      <div className="timeline-header">
        <h3 className="timeline-title">Execution Timeline</h3>
        <div className="timeline-legend">
          <div className="legend-item">
            <span className="legend-dot active"></span>
            <span>Running</span>
          </div>
          <div className="legend-item">
            <span className="legend-dot queued"></span>
            <span>Queued</span>
          </div>
          <div className="legend-item">
            <span className="legend-dot available"></span>
            <span>Available</span>
          </div>
        </div>
      </div>

      <div className="timeline-content">
        <div className="timeline-slots">
          {Array.from(timelineData.entries()).map(([slotNumber, entries]) => (
            <TimelineSlot
              key={slotNumber}
              slotNumber={slotNumber}
              entries={entries}
              maxDuration={maxDuration}
              isActive={activeRuns.some((r) => r.slot_number === slotNumber)}
            />
          ))}
        </div>

        <div className="timeline-scale">
          <TimelineScale maxDuration={maxDuration} />
        </div>
      </div>

      {(activeRuns.length > 0 || queuedRuns.length > 0) && (
        <div className="timeline-info">
          <div className="info-item">
            <span className="info-label">Est. Completion:</span>
            <span className="info-value">
              {estimateCompletion(activeRuns, queuedRuns)}
            </span>
          </div>
          <div className="info-item">
            <span className="info-label">Queue Wait:</span>
            <span className="info-value">
              {stats.queue_wait_time_minutes > 0 ? `~${stats.queue_wait_time_minutes}m` : 'None'}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

interface TimelineEntry {
  type: 'active' | 'queued';
  run_id: string;
  ticket_id: string;
  agent_id: string;
  startTime: number;
  duration: number;
  status: string;
}

interface TimelineSlotProps {
  slotNumber: number;
  entries: TimelineEntry[];
  maxDuration: number;
  isActive: boolean;
}

function TimelineSlot({
  slotNumber,
  entries,
  maxDuration,
  isActive,
}: TimelineSlotProps) {
  return (
    <div className="timeline-slot" data-testid={`timeline-slot-${slotNumber}`}>
      <div className="slot-label">
        <span className="slot-number">Slot {slotNumber}</span>
        {isActive && <span className="slot-active-indicator">●</span>}
      </div>
      <div className="slot-bar">
        {entries.length > 0 ? (
          entries.map((entry) => (
            <TimelineBar
              key={entry.run_id}
              entry={entry}
              maxDuration={maxDuration}
            />
          ))
        ) : (
          <div className="slot-empty">Available</div>
        )}
      </div>
    </div>
  );
}

interface TimelineBarProps {
  entry: TimelineEntry;
  maxDuration: number;
}

function TimelineBar({ entry, maxDuration }: TimelineBarProps) {
  const startPercent = (entry.startTime / maxDuration) * 100;
  const widthPercent = Math.min((entry.duration / maxDuration) * 100, 100 - startPercent);

  const barColor = entry.type === 'active' ? '#3fb950' : '#4493f8';

  return (
    <div
      className={`timeline-bar ${entry.type}`}
      style={{
        left: `${startPercent}%`,
        width: `${Math.max(widthPercent, 5)}%`,
        backgroundColor: barColor,
      }}
      title={`${entry.ticket_id} (${entry.agent_id})`}
      data-testid={`timeline-bar-${entry.run_id}`}
    >
      <span className="bar-label">{entry.ticket_id}</span>
    </div>
  );
}

function TimelineScale({ maxDuration }: { maxDuration: number }) {
  const intervals = 5; // Show 5 marks on the scale
  const intervalDuration = maxDuration / intervals;

  return (
    <div className="timeline-scale-bar">
      {Array.from({ length: intervals + 1 }).map((_, i) => {
        const seconds = i * intervalDuration;
        const minutes = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        const label = minutes > 0 ? `${minutes}m` : `${secs}s`;
        const percent = (i / intervals) * 100;

        return (
          <div key={i} className="scale-mark" style={{ left: `${percent}%` }}>
            <div className="scale-tick"></div>
            <div className="scale-label">{label}</div>
          </div>
        );
      })}
    </div>
  );
}

function estimateCompletion(activeRuns: ActiveRun[], queuedRuns: QueuedRun[]): string {
  if (activeRuns.length === 0 && queuedRuns.length === 0) {
    return 'N/A';
  }

  // Find the longest running agent
  const maxElapsed = activeRuns.length > 0
    ? Math.max(...activeRuns.map((r) => r.elapsed_seconds))
    : 0;

  // Estimate remaining (assume 600 seconds = 10 min per run)
  const estimatedRemaining = Math.max(600 - maxElapsed, 0);
  const totalQueueTime = queuedRuns.length * 300; // 5 min per queued item

  const totalSeconds = estimatedRemaining + totalQueueTime;
  const minutes = Math.ceil(totalSeconds / 60);

  return minutes > 0 ? `~${minutes}m` : 'Soon';
}
