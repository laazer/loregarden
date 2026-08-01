import type { SwitchboardAgent, SwitchboardServer } from "./mcpRouting";

/** Where a node sits on the board, in percent of the plot. */
function place(index: number, count: number): number {
  // Spread across all but the outer 8% so badges clear the frame and, at the
  // node cap, still clear each other.
  if (count <= 1) return 50;
  return 8 + (index * 84) / (count - 1);
}

/**
 * Agents, the gateway, and the servers behind it.
 *
 * The edges are drawn from what the control plane actually does, which is
 * simpler than the comp implies and is drawn as such: every MCP-enabled agent
 * reaches every enabled server, because registered servers are composed into
 * each agent's config wholesale. A dashed edge is a link that exists in the
 * registry but carries nothing — an agent with MCP off, or a withheld server.
 */
export function McpSwitchboard({
  agents,
  servers,
  selectedServer,
  onSelectServer,
}: {
  agents: SwitchboardAgent[];
  servers: SwitchboardServer[];
  selectedServer: string | null;
  onSelectServer: (key: string) => void;
}) {
  if (agents.length === 0 && servers.length === 0) {
    return <div className="mcp-empty">Nothing to route yet.</div>;
  }

  return (
    <div className="mcp-switchboard" data-testid="mcp-switchboard">
      <svg className="mcp-switchboard-wires" viewBox="0 0 100 100" preserveAspectRatio="none">
        {agents.map((agent, index) => (
          <line
            key={agent.key}
            x1="24"
            y1={place(index, agents.length)}
            x2="50"
            y2="50"
            className={`mcp-wire${agent.connected ? "" : " mcp-wire--idle"}`}
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {servers.map((server, index) => (
          <line
            key={server.key}
            x1="50"
            y1="50"
            x2="78"
            y2={place(index, servers.length)}
            className={`mcp-wire${server.connected ? "" : " mcp-wire--idle"}`}
            vectorEffect="non-scaling-stroke"
          />
        ))}
      </svg>

      {agents.map((agent, index) => (
        <div
          key={agent.key}
          className="mcp-node mcp-node--agent"
          style={{ left: "24%", top: `${place(index, agents.length)}%` }}
          title={agent.connected ? agent.label : `${agent.label} — MCP is switched off`}
        >
          <span className={`mcp-node-badge${agent.connected ? "" : " mcp-node-badge--idle"}`}>
            {agent.initials}
          </span>
          <span className="mcp-node-label">{agent.label}</span>
        </div>
      ))}

      <div className="mcp-gateway-hub">
        <span className="mcp-gateway-hub-icon" aria-hidden="true">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.3">
            <circle cx="12" cy="5" r="2.2" />
            <circle cx="5" cy="19" r="2.2" />
            <circle cx="19" cy="19" r="2.2" />
            <path d="M12 7.2v3.8M12 11H5.7a.7.7 0 0 0-.7.7V16M12 11h6.3a.7.7 0 0 1 .7.7V16" />
          </svg>
        </span>
        <span className="mcp-gateway-hub-title">Gateway</span>
        <span className="mcp-gateway-hub-sub">--mcp-config</span>
      </div>

      {servers.map((server, index) => (
        <button
          key={server.key}
          type="button"
          className={`mcp-node mcp-node--server${server.key === selectedServer ? " selected" : ""}`}
          style={{ left: "78%", top: `${place(index, servers.length)}%` }}
          onClick={() => onSelectServer(server.key)}
          title={
            server.builtIn
              ? "This control plane's own tools — always reachable"
              : server.connected
                ? "Composed into every agent's config"
                : "Registered but withheld from agents"
          }
        >
          <span className="mcp-node-label mcp-node-label--mono">{server.name}</span>
          <span
            className={`mcp-node-badge mcp-node-badge--server${server.connected ? "" : " mcp-node-badge--idle"}`}
          >
            <span
              className={`mcp-node-dot mcp-node-dot--${
                server.healthy === null ? "unknown" : server.healthy ? "ok" : "bad"
              }`}
            />
          </span>
        </button>
      ))}
    </div>
  );
}
