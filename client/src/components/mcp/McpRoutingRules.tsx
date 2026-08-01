import type { RoutingRule } from "./mcpRouting";

/**
 * Who may call what, and whether it stops for a human.
 *
 * Derived rather than stored: there is no routing table in the database, so
 * every row here restates a rule that is enforced somewhere else — an agent's
 * `--tools` grant, or a server's `tool_policy`. Rows an operator cannot change
 * from this page say so in their scope text rather than pretending to be
 * editable.
 */
export function McpRoutingRules({ rules }: { rules: RoutingRule[] }) {
  if (rules.length === 0) {
    return <div className="mcp-empty">No agents defined, so nothing routes yet.</div>;
  }

  return (
    <div className="mcp-rules" role="table" aria-label="Routing rules">
      <div className="mcp-rules-head" role="row">
        <span role="columnheader">Agent</span>
        <span role="columnheader">Server</span>
        <span role="columnheader">Allowed tools</span>
        <span role="columnheader">Policy</span>
      </div>
      {rules.map((rule) => (
        <div key={rule.key} className="mcp-rules-row" role="row">
          <span className="mcp-rules-agent" role="cell">
            {rule.agent}
          </span>
          <span className="mcp-rules-server" role="cell">
            {rule.server}
          </span>
          <span className="mcp-rules-scope" role="cell" title={rule.scope}>
            {rule.scope}
          </span>
          <span role="cell">
            <span className={`mcp-policy-pill mcp-policy-pill--${rule.policy}`} title={rule.policyTitle}>
              {rule.policyLabel}
            </span>
          </span>
        </div>
      ))}
    </div>
  );
}
