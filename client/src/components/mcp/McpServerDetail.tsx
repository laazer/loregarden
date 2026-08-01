import type { McpServerActivity, McpServerView } from "../../api/client";
import { McpActivityFeed } from "./McpActivityFeed";
import { McpHealthBadge } from "./McpHealthBadge";
import { initials } from "./mcpRouting";

function StatCard({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="mcp-stat-card" title={title}>
      <div className="mcp-stat-card-label">{label}</div>
      <div className="mcp-stat-card-value">{value}</div>
    </div>
  );
}

/**
 * One server, in full: how it is configured, what it exposes, who has called
 * it, and what it was last asked to do.
 *
 * The four stat cards are the comp's, with one substitution: it shows a p50
 * latency, and nothing measures per-call latency — the only timing that exists
 * is the last health check's round trip, so that is what the card says it is.
 * "Connected agents" is likewise agents that have *called* this server, not
 * agents that could; reach is not per-agent here, and a list of everyone would
 * carry no information.
 */
export function McpServerDetail({
  server,
  activity,
  onCheckHealth,
  onEdit,
  onRemove,
  isChecking,
  isRemoving,
}: {
  server: McpServerView;
  activity: McpServerActivity | undefined;
  onCheckHealth: () => void;
  onEdit: () => void;
  onRemove: () => void;
  isChecking: boolean;
  isRemoving: boolean;
}) {
  const listed = Boolean(server.tools_listed_at);
  const agents = activity?.agent_ids ?? [];

  return (
    <div className="mcp-detail-pane">
      <div className="mcp-detail-title-row">
        <span className="mcp-server-name">{server.name}</span>
        {!server.enabled && <span className="state-label">withheld</span>}
      </div>
      <McpHealthBadge server={server} />
      {server.description && <p className="mcp-detail-desc">{server.description}</p>}

      <div className="mcp-detail-actions">
        <button type="button" className="btn-secondary" onClick={onCheckHealth} disabled={isChecking}>
          {isChecking ? "Checking…" : "Check now"}
        </button>
        <button type="button" className="btn-secondary" onClick={onEdit}>
          Edit
        </button>
        <button type="button" className="btn-secondary" onClick={onRemove} disabled={isRemoving}>
          {isRemoving ? "Removing…" : "Remove"}
        </button>
      </div>

      <div className="mcp-stat-grid">
        <StatCard
          label="Transport"
          value={server.transport}
          title={server.transport === "http" ? server.url : server.command}
        />
        <StatCard
          label="Last check"
          value={server.last_checked_at ? `${server.last_health_latency_ms}ms` : "—"}
          title="Round trip of the last health handshake. Per-call latency is not measured."
        />
        <StatCard
          label="Auth"
          value={
            server.auth_env_var ? (server.auth_present ? "env var set" : "env var missing") : "none"
          }
          title={server.auth_env_var || "This server is registered without a credential"}
        />
        <StatCard
          label="Rate limit"
          value={server.rate_limit_per_min > 0 ? `${server.rate_limit_per_min}/min` : "none"}
          title="Calls per minute before the bridge refuses further calls"
        />
      </div>

      <div className="state-label">
        Tools exposed{listed ? ` · ${server.tools.length}` : ""}
      </div>
      {!listed ? (
        <div className="mcp-empty">
          Not listed yet — a health check asks the server what it exposes.
        </div>
      ) : server.tools.length === 0 ? (
        <div className="mcp-empty">This server answered with no tools.</div>
      ) : (
        <div className="mcp-tool-list">
          {server.tools.map((tool) => (
            <div key={tool} className="mcp-tool-row">
              <span
                className={`mcp-node-dot mcp-node-dot--${server.tool_policy === "auto" ? "ok" : "unknown"}`}
              />
              <span className="mcp-tool-name">{tool}</span>
              <span className="mcp-tool-pill">
                {server.tool_policy === "auto" ? "auto-run" : "ask me"}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="state-label">Agents that have called it · {agents.length}</div>
      {agents.length === 0 ? (
        <div className="mcp-empty">
          No recorded calls. Every MCP-enabled agent can reach this server; none has yet.
        </div>
      ) : (
        <div className="mcp-agent-chips">
          {agents.map((agent) => (
            <span key={agent} className="mcp-agent-chip">
              <span className="mcp-agent-chip-badge">{initials(agent)}</span>
              {agent}
            </span>
          ))}
        </div>
      )}

      <div className="state-label">
        Request feed
        {activity ? ` · ${activity.calls_per_min}/min over ${activity.window_minutes}m` : ""}
      </div>
      <McpActivityFeed server={server.name} />
    </div>
  );
}
