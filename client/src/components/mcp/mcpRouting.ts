import type { McpPolicy, McpServerView, McpTelemetry, StudioAgent } from "../../api/client";

/**
 * Who reaches which server, derived from what the control plane actually does.
 *
 * The shape of the answer is not symmetric, and the page would lie if it drew
 * it as though it were:
 *
 * - **Registered servers** are composed into an agent's `--mcp-config` from
 *   `mcp_registry.cli_server_entries`. By default that is every enabled server
 *   with every tool; an agent whose Studio tool grants name specific servers
 *   gets only those. The grant is Claude-only — no other adapter receives the
 *   server list as argv — so a grant configured elsewhere is reported as
 *   ineffective rather than applied.
 * - **Loregarden's own server** is scoped per agent regardless: an agent's
 *   `mcp_tools` becomes `--tools`, so different agents genuinely see different
 *   tools.
 *
 * Everything here is derived rather than fetched — there is no routing table in
 * the database to read, and inventing one would show grants an operator could
 * not change.
 */

export const LOREGARDEN_SERVER = "loregarden";

/** How a call to this server is resolved when an agent makes it. */
export type RoutingPolicy = "prompt" | "auto" | "disabled";

export interface RoutingRule {
  key: string;
  /** Agent display name, or "Every agent" where reach is not per-agent. */
  agent: string;
  server: string;
  /** What the agent may call, in the operator's terms. */
  scope: string;
  policy: RoutingPolicy;
  policyLabel: string;
  /** Why the policy reads the way it does, for the row's tooltip. */
  policyTitle: string;
}

export interface SwitchboardAgent {
  key: string;
  label: string;
  initials: string;
  /** False for an agent with MCP switched off — it reaches nothing. */
  connected: boolean;
}

export interface SwitchboardServer {
  key: string;
  name: string;
  /** Registered-and-enabled, or the built-in server. */
  connected: boolean;
  healthy: boolean | null;
  builtIn: boolean;
}

export function initials(name: string): string {
  const words = name.trim().split(/[\s_-]+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

/** Servers this agent's own tool grants name, keyed by server. */
function agentToolsByServer(agent: StudioAgent): Map<string, string[]> {
  const byServer = new Map<string, string[]>();
  for (const tool of agent.mcp_tools ?? []) {
    // Studio stores loregarden tools bare (`loregarden_get_ticket`) and any
    // other server's fully qualified (`mcp__github__create_issue`).
    const qualified = /^mcp__(.+?)__(.+)$/.exec(tool);
    const server = qualified ? qualified[1] : LOREGARDEN_SERVER;
    const name = qualified ? qualified[2] : tool;
    byServer.set(server, [...(byServer.get(server) ?? []), name]);
  }
  return byServer;
}

function serverPolicy(server: McpServerView): RoutingPolicy {
  if (!server.enabled) return "disabled";
  return server.tool_policy === "auto" ? "auto" : "prompt";
}

const POLICY_LABELS: Record<RoutingPolicy, string> = {
  prompt: "ask me",
  auto: "auto-run",
  disabled: "withheld",
};

const SERVER_POLICY_TITLES: Record<RoutingPolicy, string> = {
  prompt: "Every call stops for an approval",
  auto: "This server is trusted; its tools run unattended",
  disabled: "Registered but withheld from agents",
};

/**
 * The routing table, tool-level where routing is tool-level and honest about
 * where it is not.
 */
export function routingRules(
  servers: McpServerView[],
  agents: StudioAgent[],
  policy: McpPolicy | undefined,
): RoutingRule[] {
  const autoApproved = new Set(policy?.auto_approved ?? []);
  const rules: RoutingRule[] = [];

  for (const agent of agents) {
    if (!agent.mcp_enabled) {
      rules.push({
        key: `${agent.slug}:none`,
        agent: agent.name,
        server: "—",
        scope: "MCP switched off for this agent",
        policy: "disabled",
        policyLabel: POLICY_LABELS.disabled,
        policyTitle: "This agent runs without any MCP tools",
      });
      continue;
    }
    const granted = agentToolsByServer(agent).get(LOREGARDEN_SERVER) ?? [];
    // Loregarden's own tools split: reads and bookkeeping writes are
    // allowlisted, workflow-state writes still stop for a human. Saying
    // "allowlisted" for the whole grant would overstate what runs unattended.
    const unattended = granted.filter((tool) => autoApproved.has(tool)).length;
    const mixed = unattended > 0 && unattended < granted.length;
    rules.push({
      key: `${agent.slug}:${LOREGARDEN_SERVER}`,
      agent: agent.name,
      server: LOREGARDEN_SERVER,
      scope: granted.length ? granted.join(", ") : "workspace default set",
      policy: mixed || unattended === 0 ? "prompt" : "auto",
      policyLabel: policy
        ? mixed
          ? `${unattended}/${granted.length} auto`
          : unattended === 0
            ? "ask me"
            : "allowlisted"
        : "—",
      policyTitle: policy
        ? "Reads and bookkeeping writes run unattended; workflow-state writes stop for an approval"
        : "Policy could not be loaded",
    });
  }

  for (const server of servers) {
    const serverRule = serverPolicy(server);
    rules.push({
      key: `all:${server.id}`,
      agent: "Every agent",
      server: server.name,
      // No per-agent grant exists for registered servers, and saying otherwise
      // would show a control the operator does not have.
      scope: server.tools.length
        ? server.tools.join(", ")
        : server.tools_listed_at
          ? "this server exposes no tools"
          : "tools not listed yet — check the server",
      policy: serverRule,
      policyLabel: POLICY_LABELS[serverRule],
      policyTitle: SERVER_POLICY_TITLES[serverRule],
    });
  }

  return rules;
}

/** Agent nodes the board can draw before the labels collide. */
export const MAX_AGENT_NODES = 8;

/**
 * The agent side of the board.
 *
 * A registry of two dozen agents will not fit as two dozen labelled nodes, and
 * the board's claim — everyone reaches everything — does not need every node to
 * land. The overflow is drawn as its own node and counted in its label rather
 * than dropped: a silently truncated board would read as a complete one.
 */
export function switchboardAgents(agents: StudioAgent[]): SwitchboardAgent[] {
  const nodes = agents.map((agent) => ({
    key: agent.slug,
    label: agent.name,
    initials: initials(agent.name),
    connected: agent.mcp_enabled,
  }));
  if (nodes.length <= MAX_AGENT_NODES) return nodes;

  const shown = nodes.slice(0, MAX_AGENT_NODES - 1);
  const rest = nodes.slice(MAX_AGENT_NODES - 1);
  return [
    ...shown,
    {
      key: "__more__",
      label: `+${rest.length} more`,
      initials: `+${rest.length}`,
      connected: rest.some((node) => node.connected),
    },
  ];
}

export function switchboardServers(servers: McpServerView[]): SwitchboardServer[] {
  return [
    {
      key: LOREGARDEN_SERVER,
      name: LOREGARDEN_SERVER,
      connected: true,
      // The control plane's own server is reached in-process; a health check
      // that always answered would be a check of nothing.
      healthy: null,
      builtIn: true,
    },
    ...servers.map((server) => ({
      key: server.id,
      name: server.name,
      connected: server.enabled,
      healthy: server.last_checked_at ? server.last_health_ok : null,
      builtIn: false,
    })),
  ];
}

export interface GatewayMetric {
  label: string;
  value: string;
  tone: "accent" | "neutral" | "warn";
  title: string;
}

export function gatewayMetrics(
  servers: McpServerView[],
  agents: StudioAgent[],
  telemetry: McpTelemetry | undefined,
): GatewayMetric[] {
  const enabled = servers.filter((s) => s.enabled);
  const listed = enabled.filter((s) => s.tools_listed_at);
  const toolCount = listed.reduce((n, s) => n + s.tools.length, 0);
  const reaching = agents.filter((a) => a.mcp_enabled).length;
  const window = telemetry?.window_minutes ?? 60;

  return [
    {
      label: "Servers",
      value: `${enabled.length}`,
      tone: "accent",
      title:
        servers.length === enabled.length
          ? "Registered and available to agents"
          : `${servers.length - enabled.length} registered but withheld`,
    },
    {
      label: "Tools",
      value: listed.length === enabled.length ? `${toolCount}` : `${toolCount}+`,
      tone: "neutral",
      title:
        listed.length === enabled.length
          ? "Tools reported by every enabled server"
          : `${enabled.length - listed.length} server(s) never listed their tools — check them to find out`,
    },
    {
      label: "Agents",
      value: `${reaching}`,
      tone: "neutral",
      title: "Agents with MCP enabled — each reaches every enabled server",
    },
    {
      label: "Calls/min",
      value:
        telemetry?.calls_per_min != null ? telemetry.calls_per_min.toFixed(2) : "—",
      tone: "neutral",
      title: `Permission decisions per minute over the last ${window} minutes. Runs with permissions bypassed are not counted.`,
    },
  ];
}
