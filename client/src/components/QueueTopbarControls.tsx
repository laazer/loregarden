/**
 * The queue page's header, living in the top action bar.
 *
 * The v6 design draws these as a per-page hero (docs/design/v6, the QUEUE
 * section); we render the same content in the shared topbar instead, which is
 * the pattern the app is moving to. The metrics read from QueueStatusContext,
 * so this adds no second subscription — see that module.
 */

import { useQueueStatus } from "../state/QueueStatusContext";

export function QueueTopbarControls() {
  const { workspaces, activeSlug, setWorkspaceSlug, stats, isWebSocket } = useQueueStatus();

  const utilization =
    stats.max_concurrent && stats.active_count
      ? Math.round((stats.active_count / stats.max_concurrent) * 100)
      : 0;

  return (
    <div className="queue-topbar">
      <div className="queue-topbar-metrics">
        <div className="queue-topbar-metric">
          <span className="queue-topbar-metric-label">Utilization</span>
          <span className="queue-topbar-metric-value queue-topbar-metric-value--accent">
            {utilization}%
          </span>
        </div>
        <div className="queue-topbar-metric">
          <span className="queue-topbar-metric-label">Active</span>
          <span className="queue-topbar-metric-value queue-topbar-metric-value--blue">
            {stats.active_count}/{stats.max_concurrent}
          </span>
        </div>
        <div className="queue-topbar-metric">
          <span className="queue-topbar-metric-label">Queued</span>
          <span className="queue-topbar-metric-value">{stats.queued_count}</span>
        </div>
      </div>

      <div className={`queue-live-badge queue-live-badge--compact${isWebSocket ? " connected" : ""}`}>
        <span className="queue-live-badge-dot" aria-hidden />
        {isWebSocket ? "Real-time" : "Polling"}
      </div>

      <label className="topbar-workspace-picker">
        <span className="topbar-workspace-picker-label">Workspace</span>
        <select
          className="btn-secondary topbar-workspace-picker-select"
          value={activeSlug}
          disabled={!workspaces.length}
          aria-label="Queue workspace"
          onChange={(event) => setWorkspaceSlug(event.target.value)}
        >
          {workspaces.length ? null : <option value="">No workspaces</option>}
          {workspaces.map((ws) => (
            <option key={ws.slug} value={ws.slug}>
              {ws.name}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
