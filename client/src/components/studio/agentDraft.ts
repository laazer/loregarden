/**
 * The agent editor's draft, and the body a save is allowed to send.
 *
 * The draft carries `slug` because the create branch needs one. The update
 * branch must not send it: the server's `StudioAgentUpdate` sets
 * `extra="forbid"` and rejects a slug change with 422, since the slug is a
 * by-string key named in workflow templates, drafts, ticket pins and an
 * append-only run ledger, with no FK and no single owner to migrate. Spreading
 * the whole draft into the PATCH is what would 422 every Studio agent save
 * while every backend test stayed green, so the body is built field by field
 * here rather than by subtraction at the call site.
 */

import type {
  StudioAgent,
  StudioAgentToolGrants,
  StudioAgentUpdateBody,
  StudioGateCheck,
  StudioHandoffCheck,
} from "../../api/client";

export const EMPTY_AGENT = {
  slug: "",
  name: "",
  description: "",
  role_body: "",
  adapter: "claude",
  default_model: "",
  timeout: 600,
  default_skill: "",
  mcp_enabled: true,
  mcp_tools: [] as string[],
  gate_checks: [] as StudioGateCheck[],
  handoff_checks: [] as StudioHandoffCheck[],
  tool_grants: {
    posture: "inherit",
    allowed_tools: [],
    disallowed_tools: [],
    mcp_servers: [],
  } as StudioAgentToolGrants,
};

export type AgentDraft = typeof EMPTY_AGENT;

/** The writable half of the draft: every field the PATCH accepts, and no other.
 *
 * `mcp_tools` is cleared when MCP is off, so turning it off actually withdraws
 * the grants rather than leaving them stored and unused.
 */
export function agentUpdateBody(draft: AgentDraft): StudioAgentUpdateBody {
  return {
    name: draft.name,
    description: draft.description,
    role_body: draft.role_body,
    adapter: draft.adapter,
    default_model: draft.default_model,
    timeout: draft.timeout,
    default_skill: draft.default_skill,
    mcp_enabled: draft.mcp_enabled,
    mcp_tools: draft.mcp_enabled ? draft.mcp_tools : [],
    gate_checks: draft.gate_checks,
    handoff_checks: draft.handoff_checks,
    tool_grants: draft.tool_grants,
  };
}

/** The draft the editor shows for a stored agent.
 *
 * One builder for both sites that need it — hydrating the editor on a
 * selection, and seeding a duplicate — because two copies of this list is how a
 * field reaches the editor and then quietly fails to survive a duplicate.
 */
export function draftFromAgent(agent: StudioAgent): AgentDraft {
  return {
    slug: agent.slug,
    name: agent.name,
    description: agent.description,
    role_body: agent.role_body,
    adapter: agent.adapter,
    default_model: agent.default_model,
    timeout: agent.timeout,
    default_skill: agent.default_skill,
    mcp_enabled: agent.mcp_enabled,
    mcp_tools: agent.mcp_tools,
    gate_checks: agent.gate_checks,
    handoff_checks: agent.handoff_checks,
    tool_grants: agent.tool_grants,
  };
}
