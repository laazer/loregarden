import type { McpServerView, McpTelemetry, McpToolCallView, StudioAgent } from "../api/client";

/** A registered server, defaulted to the state one has right after registering:
 *  reachable in principle, never checked, no tools listed. */
export function mcpServer(overrides: Partial<McpServerView> = {}): McpServerView {
  return {
    id: "s1",
    name: "github",
    description: "",
    transport: "http",
    url: "https://mcp.example/sse",
    command: "",
    args: [],
    auth_env_var: "",
    auth_present: false,
    enabled: true,
    tool_policy: "prompt",
    rate_limit_per_min: 0,
    last_checked_at: "",
    last_health_ok: false,
    last_health_latency_ms: 0,
    last_health_error: "",
    tools: [],
    tools_listed_at: "",
    created_at: "2026-07-20T00:00:00",
    updated_at: "2026-07-20T00:00:00",
    ...overrides,
  };
}

export function mcpToolCall(overrides: Partial<McpToolCallView> = {}): McpToolCallView {
  return {
    id: "c1",
    run_id: "run-1",
    ticket_id: "t-1",
    agent_id: "planner",
    tool_name: "mcp__github__create_issue",
    server_name: "github",
    decision: "auto_server",
    decision_ms: 0,
    created_at: "2026-07-20T10:00:00",
    ...overrides,
  };
}

export function mcpTelemetry(overrides: Partial<McpTelemetry> = {}): McpTelemetry {
  return {
    by_server: {},
    by_decision: {},
    per_server: {},
    window_minutes: 60,
    calls_per_min: 0,
    recent: [],
    ...overrides,
  };
}

export function studioAgent(overrides: Partial<StudioAgent> = {}): StudioAgent {
  return {
    id: "a1",
    slug: "planner",
    name: "Planner",
    description: "",
    role_body: "",
    adapter: "claude",
    default_model: "",
    timeout: 600,
    default_skill: "",
    mcp_enabled: true,
    mcp_tools: [],
    gate_checks: [],
    handoff_checks: [],
    tool_grants: { posture: "inherit", allowed_tools: [], disallowed_tools: [], mcp_servers: [] },
    tool_grant_warnings: [],
    built_in: true,
    created_at: "2026-07-20T00:00:00",
    updated_at: "2026-07-20T00:00:00",
    ...overrides,
  };
}
