/**
 * Dashboard component showing active and queued parallel agent runs.
 * Displays real-time execution slots, queue positions, and statistics.
 */

import { useParallelExecution } from '../hooks/useParallelExecution';
import type { ActiveRun, QueuedRun } from '../hooks/useParallelExecution';
import './ParallelFeatureCards.css';

export interface ParallelFeatureCardsProps {
  workspaceId: string;
  compact?: boolean;
}

export function ParallelFeatureCards({
  workspaceId,
}: ParallelFeatureCardsProps) {
  const { activeRuns, queuedRuns, stats, loading, error } = useParallelExecution(
    workspaceId,
    5000 // Poll every 5 seconds
  );

  if (loading && activeRuns.length === 0 && queuedRuns.length === 0) {
    return <div className="parallel-cards loading">Loading parallel execution status...</div>;
  }

  return (
    <div className="parallel-cards-container">
      {error && (
        <div className="parallel-error" data-testid="parallel-error">
          ⚠️ {error}
        </div>
      )}

      {/* Execution Slots Overview */}
      <div className="parallel-stats-bar">
        <div className="stats-item">
          <span className="stats-label">Active</span>
          <span className="stats-value active">{stats.active_count}/{stats.max_concurrent}</span>
        </div>
        <div className="stats-item">
          <span className="stats-label">Queue</span>
          <span className="stats-value queued">{stats.queued_count}</span>
        </div>
        <div className="stats-item">
          <span className="stats-label">Available</span>
          <span className="stats-value available">{stats.available_slots}</span>
        </div>
        {stats.queue_wait_time_minutes > 0 && (
          <div className="stats-item">
            <span className="stats-label">Wait</span>
            <span className="stats-value">{stats.queue_wait_time_minutes}m</span>
          </div>
        )}
      </div>

      {/* Active Runs */}
      {activeRuns.length > 0 && (
        <div className="parallel-section">
          <h3 className="parallel-section-title">
            Active Features ({activeRuns.length}/{stats.max_concurrent})
          </h3>
          <div className="parallel-cards-grid">
            {activeRuns.map((run) => (
              <ActiveFeatureCard key={run.run_id} run={run} />
            ))}
          </div>
        </div>
      )}

      {/* Queued Runs */}
      {queuedRuns.length > 0 && (
        <div className="parallel-section">
          <h3 className="parallel-section-title">
            Queue ({queuedRuns.length})
          </h3>
          <div className="parallel-queue-list">
            {queuedRuns.map((run, index) => (
              <QueuedFeatureItem key={run.run_id} run={run} position={index + 1} />
            ))}
          </div>
        </div>
      )}

      {/* Empty State */}
      {activeRuns.length === 0 && queuedRuns.length === 0 && (
        <div className="parallel-empty">
          <div className="empty-icon">⚡</div>
          <p>No parallel runs active</p>
          <p className="empty-hint">All slots available</p>
        </div>
      )}
    </div>
  );
}

interface ActiveFeatureCardProps {
  run: ActiveRun;
}

function ActiveFeatureCard({ run }: ActiveFeatureCardProps) {
  const elapsedMinutes = Math.floor(run.elapsed_seconds / 60);
  const elapsedSeconds = run.elapsed_seconds % 60;

  const formatElapsed = () => {
    if (elapsedMinutes > 0) {
      return `${elapsedMinutes}m ${elapsedSeconds}s`;
    }
    return `${elapsedSeconds}s`;
  };

  return (
    <div
      className="feature-card active"
      data-testid={`active-run-${run.run_id}`}
    >
      <div className="card-header">
        <span className="card-badge active-badge">
          <span className="pulsing-dot"></span>
          Slot {run.slot_number}
        </span>
        <span className="card-status">{run.status}</span>
      </div>

      <div className="card-content">
        <div className="card-title" title={`Ticket: ${run.ticket_id}`}>
          {run.ticket_id}
        </div>
        <div className="card-agent">
          <span className="agent-icon">🤖</span>
          {run.agent_id}
        </div>

        <div className="card-progress">
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{
                width: `${Math.min((run.elapsed_seconds / 600) * 100, 95)}%`,
              }}
            ></div>
          </div>
          <div className="progress-label">
            Running {formatElapsed()}
          </div>
        </div>
      </div>

      <div className="card-actions">
        <button className="card-button small secondary">View Logs</button>
      </div>
    </div>
  );
}

interface QueuedFeatureItemProps {
  run: QueuedRun;
  position: number;
}

function QueuedFeatureItem({ run, position }: QueuedFeatureItemProps) {
  const estimatedStart = new Date(run.estimated_start_at);
  const now = new Date();
  const waitMinutes = Math.ceil((estimatedStart.getTime() - now.getTime()) / 60000);

  return (
    <div className="queue-item" data-testid={`queued-run-${run.run_id}`}>
      <div className="queue-position">
        <span className="position-number">{position}</span>
      </div>

      <div className="queue-details">
        <div className="queue-title">{run.ticket_id}</div>
        <div className="queue-agent">
          <span className="agent-icon">🤖</span>
          {run.agent_id}
        </div>
      </div>

      <div className="queue-eta">
        <div className="eta-label">Est. Start</div>
        <div className="eta-time">
          {waitMinutes > 0 ? `~${waitMinutes}m` : 'Soon'}
        </div>
      </div>

      <div className="queue-actions">
        <button className="queue-button small" title="Cancel this queued run">
          ✕
        </button>
      </div>
    </div>
  );
}
