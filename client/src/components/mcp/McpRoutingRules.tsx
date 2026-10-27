import { Link } from "react-router-dom";

import { studioAgentPath } from "../../lib/appNavigation";
import { STANDARD_SET, type RoutingRule } from "./mcpRouting";

/**
 * Who may call what, and whether it stops for a human.
 *
 * Derived rather than stored: there is no routing table in the database, so
 * every row here restates a rule that is enforced somewhere else — an agent's
 * `--tools` grant, or a server's `tool_policy`. Rows an operator cannot change
 * from this page say so in their scope text rather than pretending to be
 * editable.
 *
 * A real table, compared across rows by agent, server and policy. An agent's
 * name opens it in Agent Studio, which is where its grant is changed: a list of
 * thirty-two rules with nowhere to go from any of them was a dead end.
 */
export function McpRoutingRules({ rules, standard }: { rules: RoutingRule[]; standard: string[] | null }) {
  if (rules.length === 0) {
    return <div className="mcp-empty">No agents defined, so nothing routes yet.</div>;
  }

  // Exactly the standard set; a row with changes to it does not share it.
  const onStandard = standard ? rules.filter((rule) => rule.scope === STANDARD_SET).length : 0;
  return (
    <>
    {standard ? (
      <details className="mcp-standard">
        <summary>
          Standard set: the {standard.length} loregarden tools {onStandard} agents share
        </summary>
        <p className="mcp-standard-tools">{standard.join(", ")}</p>
      </details>
    ) : null}
    {/* Scrollable in its own right: one row per agent plus one per server runs
        past any viewport, and letting it grow pushed the switchboard above it
        off screen. The agent links make it keyboard-reachable. */}
    <div className="mcp-rules">
      <table className="mcp-rules-table">
        <caption className="mcp-sr">Routing rules, {rules.length} rules</caption>
        <thead>
          <tr className="mcp-rules-head">
            <th scope="col">Agent</th>
            <th scope="col">Server</th>
            <th scope="col">Allowed tools</th>
            <th scope="col">Policy</th>
          </tr>
        </thead>
        <tbody>
          {rules.map((rule) => (
            <tr key={rule.key} className="mcp-rules-row">
              <td className="mcp-rules-agent">
                {rule.agentSlug ? (
                  <Link
                    className="mcp-rules-agent-link"
                    to={studioAgentPath(rule.agentSlug)}
                    title={`Open ${rule.agent} in Agent Studio to change what it may call`}
                  >
                    {rule.agent}
                  </Link>
                ) : (
                  rule.agent
                )}
              </td>
              <td className="mcp-rules-server">{rule.server}</td>
              <td className="mcp-rules-scope" title={rule.scopeTitle || rule.scope}>
                {rule.scope}
              </td>
              <td>
                <span className={`mcp-policy-pill mcp-policy-pill--${rule.policy}`} title={rule.policyTitle}>
                  {rule.policyLabel}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    </>
  );
}
