/**
 * Advanced Gantt-style timeline view for queue visualization
 * Shows time-based progression of active and queued runs
 */

import React, { useMemo } from 'react';
import './QueueAdvancedTimeline.css';

export interface TimelineRun {
  run_id: string;
  ticket_id: string;
  slot_number?: number;
  position?: number;
  elapsed_seconds: number;
  estimated_duration_seconds: number;
  status: string;
}

export interface QueueAdvancedTimelineProps {
  activeRuns: TimelineRun[];
  queuedRuns: TimelineRun[];
  currentTime?: Date;
  timeScale?: 'minutes' | 'hours'; // How to scale the timeline
}

export function QueueAdvancedTimeline({
  activeRuns = [],
  queuedRuns = [],
  currentTime = new Date(),
  timeScale = 'minutes',
}: QueueAdvancedTimelineProps) {
  // Calculate timeline metrics
  const timelineMetrics = useMemo(() => {
    const defaultDuration = 300; // 5 minutes default

    // Calculate total time for queue to clear
    const activeTime = activeRuns.reduce(
      (sum, run) =>
        sum +
        Math.max(0, run.estimated_duration_seconds - run.elapsed_seconds),
      0
    );

    const queuedTime = queuedRuns.length * defaultDuration;
    const totalTime = activeTime + queuedTime;

    // Determine timeline scale
    const timelineEnd =
      timeScale === 'hours'
        ? Math.max(totalTime, 3600) // At least 1 hour
        : Math.max(totalTime, 600); // At least 10 minutes

    // Pixels per second (responsive)
    const pixelsPerSecond = 300 / timelineEnd;

    return {
      totalTime,
      timelineEnd,
      activeTime,
      queuedTime,
      pixelsPerSecond,
    };
  }, [activeRuns, queuedRuns, timeScale]);

  // Format time display
  const formatTime = (seconds: number) => {
    if (timeScale === 'hours') {
      const hours = Math.floor(seconds / 3600);
      const mins = Math.floor((seconds % 3600) / 60);
      return `${hours}h ${mins}m`;
    }
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}m ${secs}s`;
  };

  // Calculate position in timeline (pixels from start)
  const getPositionPixels = (seconds: number) => {
    return seconds * timelineMetrics.pixelsPerSecond;
  };

  // Calculate width in timeline (pixels)
  const getWidthPixels = (seconds: number) => {
    return Math.max(20, seconds * timelineMetrics.pixelsPerSecond);
  };

  return (
    <div className="timeline-container">
      <div className="timeline-header">
        <h3>Queue Timeline</h3>
        <div className="timeline-info">
          <span className="timeline-metric">
            Total Clear Time: {formatTime(timelineMetrics.totalTime)}
          </span>
          <span className="timeline-metric">
            Active: {formatTime(timelineMetrics.activeTime)}
          </span>
          <span className="timeline-metric">
            Queued: {formatTime(timelineMetrics.queuedTime)}
          </span>
        </div>
      </div>

      <div className="timeline-wrapper">
        {/* Timeline Ruler */}
        <div className="timeline-ruler">
          <div className="ruler-track">
            {/* Render time markers */}
            {Array.from(
              { length: Math.ceil(timelineMetrics.timelineEnd / 300) + 1 },
              (_, i) => i * 300
            ).map((seconds) => (
              <div
                key={`marker-${seconds}`}
                className="ruler-marker"
                style={{
                  left: `${getPositionPixels(seconds)}px`,
                }}
              >
                <div className="marker-line"></div>
                <div className="marker-label">{formatTime(seconds)}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Timeline Content */}
        <div className="timeline-content">
          {/* Active Runs Section */}
          {activeRuns.length > 0 && (
            <div className="timeline-section active-section">
              <div className="section-label">Active Runs</div>

              {activeRuns.map((run, index) => (
                <div key={run.run_id} className="timeline-row active-row">
                  <div className="row-label">
                    <span className="slot-badge">Slot {run.slot_number}</span>
                    <span className="ticket-id">{run.ticket_id}</span>
                  </div>

                  <div className="timeline-bar">
                    <div
                      className="bar-background"
                      style={{
                        width: `${getWidthPixels(
                          run.estimated_duration_seconds
                        )}px`,
                      }}
                    >
                      {/* Progress section */}
                      <div
                        className="bar-progress active"
                        style={{
                          width: `${
                            (run.elapsed_seconds /
                              run.estimated_duration_seconds) *
                            100
                          }%`,
                        }}
                      ></div>

                      {/* Remaining section */}
                      <div
                        className="bar-remaining"
                        style={{
                          width: `${
                            ((run.estimated_duration_seconds -
                              run.elapsed_seconds) /
                              run.estimated_duration_seconds) *
                            100
                          }%`,
                        }}
                      ></div>
                    </div>

                    <div className="bar-label">
                      {formatTime(
                        Math.max(0, run.estimated_duration_seconds - run.elapsed_seconds)
                      )}
                      {' remaining'}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Queued Runs Section */}
          {queuedRuns.length > 0 && (
            <div className="timeline-section queued-section">
              <div className="section-label">Queue</div>

              {queuedRuns.map((run, index) => {
                // Calculate when this run will start
                const startTimeSeconds =
                  timelineMetrics.activeTime + index * 300;
                const endTimeSeconds = startTimeSeconds + 300;

                return (
                  <div key={run.run_id} className="timeline-row queued-row">
                    <div className="row-label">
                      <span className="position-badge">#{run.position}</span>
                      <span className="ticket-id">{run.ticket_id}</span>
                    </div>

                    <div className="timeline-bar">
                      {/* Waiting time (before this run starts) */}
                      <div
                        className="bar-waiting"
                        style={{
                          width: `${getWidthPixels(startTimeSeconds)}px`,
                        }}
                      ></div>

                      {/* Estimated execution time */}
                      <div
                        className="bar-background queued"
                        style={{
                          width: `${getWidthPixels(300)}px`,
                        }}
                      ></div>
                    </div>

                    <div className="bar-label">
                      Starts in {formatTime(startTimeSeconds)}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Queue Clear Indicator */}
          {(activeRuns.length > 0 || queuedRuns.length > 0) && (
            <div className="timeline-completion">
              <div className="completion-line"></div>
              <div className="completion-label">
                All clear at {formatTime(timelineMetrics.totalTime)}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Legend */}
      <div className="timeline-legend">
        <div className="legend-item">
          <div className="legend-box active"></div>
          <span>Running</span>
        </div>
        <div className="legend-item">
          <div className="legend-box progress"></div>
          <span>Progress</span>
        </div>
        <div className="legend-item">
          <div className="legend-box remaining"></div>
          <span>Remaining</span>
        </div>
        <div className="legend-item">
          <div className="legend-box waiting"></div>
          <span>Waiting</span>
        </div>
      </div>

      {/* Empty State */}
      {activeRuns.length === 0 && queuedRuns.length === 0 && (
        <div className="timeline-empty">
          <p>No active or queued runs</p>
        </div>
      )}
    </div>
  );
}
