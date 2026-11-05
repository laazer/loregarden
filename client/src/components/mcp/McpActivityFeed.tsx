import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { usePolledReadFailure } from "../../hooks/usePolledReadFailure";
import { duration } from "../../lib/duration";
import { describeError, endSentence } from "../../state/toastStore";
import { StaleReadNotice } from "../ui/StaleReadNotice";

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
 * There is no execution latency here on purpose. The permission bridge sees the
 * request and the decision, never the result, so that number would be invented
 * — and a plausible invented number is worse than an absent one. `decision_ms`
 * is shown only where a human waited.
 *
 * `server` narrows the feed to one server's calls; the counts strip is dropped
 * there, since a single server's share of itself says nothing.
 */
export function McpActivityFeed({ server }: { server?: string }) {
  const telemetry = useQuery({
    queryKey: ["mcp-telemetry"],
    queryFn: api.mcpTelemetry,
    refetchInterval: 5000,
  });

  // Derived ABOVE the failure branch, so the branch is decided by what the feed
  // has to show rather than by the read's status. `data` is already
  // optional-chained here, so nothing about the success path changes.
  const data = telemetry.data;
  const servers = server
    ? []
    : Object.entries(data?.by_server ?? {}).sort((a, b) => b[1] - a[1]);
  const recent = (data?.recent ?? []).filter((call) => !server || call.server_name === server);

  // The subject is the constant the query key is pinned to: `["mcp-telemetry"]`
  // does not vary, and `server` filters client-side. There is no retry control
  // here today, so the hold makes no observable difference yet — the wrapper is
  // used so that adding one later cannot reintroduce the defect where the
  // operator's own click unmounts the reason they clicked on.
  const failure = usePolledReadFailure(
    telemetry,
    "mcp-telemetry",
    servers.length > 0 || recent.length > 0,
  );

  if (telemetry.isPending) {
    return <div className="mcp-empty">Loading activity…</div>;
  }
  if (failure.blocking) {
    // Today's copy said only "Could not load activity." — nothing the operator
    // can act on. The reason is the whole value of this pane.
    return (
      <div className="mcp-empty" role="alert">
        Could not load activity:{" "}
        {endSentence(describeError(failure.blocking.error, "The telemetry endpoint did not answer."))}
      </div>
    );
  }

  // Unreachable on a failure now, because the branch above took it: "No tool
  // calls recorded yet" over a failing read is a health claim about the
  // permission bridge that this surface cannot make.
  if (servers.length === 0 && recent.length === 0) {
    return (
      <div className="mcp-empty">
        {server ? `No calls to ${server} recorded yet.` : "No tool calls recorded yet."} Calls
        are recorded when an agent asks permission, so runs with permissions bypassed do not
        appear.
      </div>
    );
  }

  return (
    <div >
      {failure.stale && (
        <StaleReadNotice
          lead="This activity may be out of date — the last refresh failed."
          error={failure.stale.error}
          fallback="The telemetry endpoint did not answer."
          testId="mcp-refresh-failed"
        />
      )}

      {servers.length > 0 && (
        <div className="mcp-activity-counts">
          {servers.map(([name, count]) => (
            <span key={name} className="mcp-activity-count">
              <span className="mcp-activity-count-name">{name}</span>
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
