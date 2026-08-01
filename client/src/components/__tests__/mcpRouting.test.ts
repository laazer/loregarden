import { mcpServer, mcpTelemetry, studioAgent } from "../../test/mcpFixtures";
import {
  gatewayMetrics,
  initials,
  MAX_AGENT_NODES,
  routingRules,
  switchboardAgents,
  switchboardServers,
} from "../mcp/mcpRouting";

const POLICY = { auto_approved: ["loregarden_get_ticket"], orchestrated_denied: [] };

it("routes a registered server to every agent, because that is what happens", () => {
  // cli_server_entries composes registered servers into every agent's config;
  // a per-agent row here would show a grant nobody can revoke.
  const rules = routingRules([mcpServer()], [studioAgent()], POLICY);
  const serverRule = rules.find((r) => r.server === "github");

  expect(serverRule?.agent).toBe("Every agent");
});

it("splits an agent's loregarden grant into what runs and what asks", () => {
  const rules = routingRules(
    [],
    [studioAgent({ mcp_tools: ["loregarden_get_ticket", "loregarden_complete_stage"] })],
    POLICY,
  );

  expect(rules[0].policyLabel).toBe("1/2 auto");
  // A mixed grant is not "auto" — something in it still stops for a human.
  expect(rules[0].policy).toBe("prompt");
});

it("calls a wholly allowlisted grant allowlisted", () => {
  const rules = routingRules([], [studioAgent({ mcp_tools: ["loregarden_get_ticket"] })], POLICY);

  expect(rules[0].policyLabel).toBe("allowlisted");
  expect(rules[0].policy).toBe("auto");
});

it("says an agent with MCP off reaches nothing", () => {
  const rules = routingRules([], [studioAgent({ mcp_enabled: false })], POLICY);

  expect(rules[0].policy).toBe("disabled");
  expect(rules[0].scope).toMatch(/switched off/i);
});

it("distinguishes a server that listed nothing from one never listed", () => {
  const never = routingRules([mcpServer()], [], POLICY)[0];
  const empty = routingRules(
    [mcpServer({ tools: [], tools_listed_at: "2026-07-20T00:00:00" })],
    [],
    POLICY,
  )[0];

  expect(never.scope).toMatch(/not listed/i);
  expect(empty.scope).toMatch(/no tools/i);
});

it("marks a withheld server as withheld rather than as asking", () => {
  const rules = routingRules([mcpServer({ enabled: false })], [], POLICY);

  expect(rules[0].policy).toBe("disabled");
});

it("counts only tools a server actually reported, and flags the gap", () => {
  // One server checked, one never. A bare total would present the checked
  // server's count as the whole gateway's.
  const metrics = gatewayMetrics(
    [
      mcpServer({ id: "a", name: "github", tools: ["x", "y"], tools_listed_at: "2026-07-20" }),
      mcpServer({ id: "b", name: "linear" }),
    ],
    [],
    mcpTelemetry(),
  );

  expect(metrics.find((m) => m.label === "Tools")?.value).toBe("2+");
});

it("reports a bare count once every enabled server has been listed", () => {
  const metrics = gatewayMetrics(
    [mcpServer({ tools: ["x", "y"], tools_listed_at: "2026-07-20" })],
    [],
    mcpTelemetry(),
  );

  expect(metrics.find((m) => m.label === "Tools")?.value).toBe("2");
});

it("shows no rate at all until telemetry has loaded", () => {
  // A zero here would read as "nothing is calling anything", which is a
  // different claim from "we have not been told yet".
  const metrics = gatewayMetrics([], [], undefined);

  expect(metrics.find((m) => m.label === "Calls/min")?.value).toBe("—");
});

it("counts only agents that have MCP switched on", () => {
  const metrics = gatewayMetrics(
    [],
    [studioAgent({ slug: "a" }), studioAgent({ slug: "b", mcp_enabled: false })],
    mcpTelemetry(),
  );

  expect(metrics.find((m) => m.label === "Agents")?.value).toBe("1");
});

it("puts the built-in server on the board even though nothing registered it", () => {
  const servers = switchboardServers([mcpServer()]);

  expect(servers[0]).toMatchObject({ name: "loregarden", builtIn: true, connected: true });
  // Never health-checked reads as unknown, not as healthy.
  expect(servers[1].healthy).toBeNull();
});

it("draws an agent with MCP off as present but unconnected", () => {
  const agents = switchboardAgents([studioAgent({ mcp_enabled: false })]);

  expect(agents[0].connected).toBe(false);
});

it("derives initials from a name without assuming its shape", () => {
  expect(initials("Frontend Implementer")).toBe("FI");
  expect(initials("planner")).toBe("PL");
  expect(initials("branch_triage")).toBe("BT");
  expect(initials("")).toBe("?");
});

it("counts the agents it could not draw instead of dropping them", () => {
  // A board that quietly showed the first eight of twenty-five would read as
  // the whole registry.
  const agents = Array.from({ length: 25 }, (_, i) =>
    studioAgent({ slug: `a${i}`, name: `Agent ${i}` }),
  );

  const nodes = switchboardAgents(agents);

  expect(nodes).toHaveLength(MAX_AGENT_NODES);
  expect(nodes[nodes.length - 1].label).toBe("+18 more");
});
