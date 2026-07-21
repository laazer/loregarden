import type { McpServerView } from "../../api/client";

function checkedAgo(iso: string): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, (Date.now() - then) / 1000);
  if (seconds < 90) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours}h ago` : `${Math.round(hours / 24)}d ago`;
}

/**
 * What the last health check found, or that there has not been one.
 *
 * "Never checked" is its own state rather than a default of healthy or
 * unhealthy — a server nobody has checked is an open question, and showing it
 * as either answer would be a claim nothing supports.
 */
export function McpHealthBadge({ server }: { server: McpServerView }) {
  if (!server.last_checked_at) {
    return <span className="mcp-health mcp-health--unknown">never checked</span>;
  }

  const when = checkedAgo(server.last_checked_at);

  if (server.last_health_ok) {
    return (
      <span className="mcp-health mcp-health--ok" title={`Answered in ${server.last_health_latency_ms}ms`}>
        answered · {server.last_health_latency_ms}ms
        {when && <span className="mcp-health-when"> · {when}</span>}
      </span>
    );
  }

  return (
    <span className="mcp-health mcp-health--bad" title={server.last_health_error}>
      {server.last_health_error || "did not answer"}
      {when && <span className="mcp-health-when"> · {when}</span>}
    </span>
  );
}
