import { render, screen } from "@testing-library/react";

import type { McpServerView } from "../../api/client";
import { McpHealthBadge } from "../mcp/McpHealthBadge";

function server(overrides: Partial<McpServerView> = {}): McpServerView {
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
    last_checked_at: "",
    last_health_ok: false,
    last_health_latency_ms: 0,
    last_health_error: "",
    created_at: "2026-07-20T00:00:00",
    updated_at: "2026-07-20T00:00:00",
    ...overrides,
  };
}

it("says never checked rather than guessing a verdict", () => {
  // A registered server defaults to last_health_ok=false. Rendering that as
  // "failing" would accuse a server nobody has tried.
  render(<McpHealthBadge server={server()} />);
  expect(screen.getByText("never checked")).toBeInTheDocument();
});

it("reports a passing check with its latency", () => {
  render(
    <McpHealthBadge
      server={server({
        last_checked_at: new Date().toISOString(),
        last_health_ok: true,
        last_health_latency_ms: 143,
      })}
    />,
  );
  expect(screen.getByText(/answered · 143ms/)).toBeInTheDocument();
});

it("shows the server's own reason for failing", () => {
  render(
    <McpHealthBadge
      server={server({
        last_checked_at: new Date().toISOString(),
        last_health_ok: false,
        last_health_error: "GITHUB_MCP_TOKEN is not set where Loregarden runs",
      })}
    />,
  );
  // The operator needs the cause, not a red dot.
  expect(screen.getByText(/GITHUB_MCP_TOKEN is not set/)).toBeInTheDocument();
});

it("ages the result, because a stale pass is not a current one", () => {
  const twoHoursAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
  render(
    <McpHealthBadge
      server={server({ last_checked_at: twoHoursAgo, last_health_ok: true, last_health_latency_ms: 90 })}
    />,
  );
  expect(screen.getByText(/2h ago/)).toBeInTheDocument();
});
