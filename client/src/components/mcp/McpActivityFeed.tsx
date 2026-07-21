import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { duration } from "../../lib/duration";

/** How a request was resolved, in the operator's words rather than the enum's. */
const DECISION_LABELS: Record<string, string> = {
  auto_server: "trusted server",
  auto_allowlist: "allowlisted",
  auto_cli: "read-only",
  auto_run: "run auto-approve",
  auto_scope: "standing allowance",
  approved: "you approved",
  rejected: "you rejected",
};

function decisionLabel(decision: string): string {
  return DECISION_LABELS[decision] ?? decision;
}

/**
 * What agents actually asked for, and how each request was resolved.
 *
 * There is no request rate and no execution latency here on purpose. The
 * permission bridge sees the request and the decision, never the result, so
 * either number would be invented — and a plausible invented number is worse
 * than an absent one. `decision_ms` is shown only where a human waited.
 */
export function McpActivityFeed() {
  const telemetry = useQuery({
    queryKey: ["mcp-telemetry"],
    queryFn: api.mcpTelemetry,
    refetchInterval: 5000,
  });

  if (telemetry.isPending) {
    return <div className="mcp-empty">Loading activity…</div>;
  }
  if (telemetry.isError) {
    return <div className="mcp-empty">Could not load activity.</div>;
  }

  const data = telemetry.data;
  const servers = Object.entries(data?.by_server ?? {}).sort((a, b) => b[1] - a[1]);
  const recent = data?.recent ?? [];

  if (servers.length === 0 && recent.length === 0) {
    return (
      <div className="mcp-empty">
        No tool calls recorded yet. Calls are recorded when an agent asks permission, so
        runs with permissions bypassed do not appear.
      </div>
    );
  }

  return (
    <div className="mcp-activity">
      {servers.length > 0 && (
        <div className="mcp-activity-counts">
          {servers.map(([server, count]) => (
            <span key={server} className="mcp-activity-count">
              <span className="mcp-activity-count-name">{server}</span>
              <span className="mcp-activity-count-value">{count}</span>
            </span>
          ))}
        </div>
      )}

      <ul className="mcp-activity-list">
        {recent.map((call) => (
          <li key={call.id} className="mcp-activity-row">
            <span className="mcp-activity-tool">{call.tool_name}</span>
            <span className="mcp-activity-decision">{decisionLabel(call.decision)}</span>
            {call.decision_ms > 0 && (
              <span className="mcp-activity-wait">waited {duration(call.decision_ms / 1000)}</span>
            )}
            <span className="mcp-activity-agent">{call.agent_id}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
