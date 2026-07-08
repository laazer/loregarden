import type { HiveStationId } from "./skins";

const RESEARCH_AGENTS = new Set([
  "retriever",
  "learning",
  "research_librarian",
  "blog_post",
]);

const PLANNER_AGENTS = new Set([
  "planner",
  "spec",
  "ticket_scoper",
  "triage",
]);

const CODING_AGENTS = new Set([
  "backend_implementer",
  "frontend_implementer",
  "core_simulation",
  "gameplay_systems",
  "presentation",
  "engine_integration",
  "implementation_frontend",
]);

const TESTING_AGENTS = new Set([
  "static_qa",
  "test_designer",
  "test_breaker",
]);

const DEPLOY_AGENTS = new Set([
  "gatekeeper",
  "ac_gatekeeper",
  "gdscript_reviewer",
  "architecture_reviewer",
]);

/** Cast variant within a skin's agent sheet (index into sheet / tint family). */
export type HiveCastVariant = "worker" | "planner" | "implementer" | "tester" | "reviewer";

export interface HiveRoleMapping {
  station: HiveStationId;
  cast: HiveCastVariant;
}

function normalizeAgentId(agentId: string): string {
  return agentId.replace(/·.*/, "").trim().toLowerCase();
}

export function mapAgentToRole(agentId: string): HiveRoleMapping {
  const id = normalizeAgentId(agentId);

  if (!id) {
    return { station: "coding", cast: "worker" };
  }

  if (PLANNER_AGENTS.has(id) || id.includes("planner") || id.includes("spec")) {
    return { station: "planner_hq", cast: "planner" };
  }

  if (RESEARCH_AGENTS.has(id) || id.includes("retriev") || id.includes("research")) {
    return { station: "research", cast: "worker" };
  }

  if (
    TESTING_AGENTS.has(id) ||
    id.includes("test") ||
    id.includes("qa") ||
    id.includes("breaker")
  ) {
    return { station: "testing", cast: "tester" };
  }

  if (
    DEPLOY_AGENTS.has(id) ||
    id.includes("gatekeeper") ||
    id.includes("reviewer") ||
    id.includes("deploy")
  ) {
    return { station: "deploy", cast: "reviewer" };
  }

  if (
    CODING_AGENTS.has(id) ||
    id.includes("implement") ||
    id.includes("simulation") ||
    id.includes("gameplay") ||
    id.includes("presentation") ||
    id.includes("engine")
  ) {
    return { station: "coding", cast: "implementer" };
  }

  return { station: "coding", cast: "implementer" };
}
