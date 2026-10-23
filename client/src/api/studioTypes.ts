/**
 * The Workflow Studio's wire types: agents, their versions, workflow drafts and
 * their stages.
 *
 * Split out of `types.ts` when that file reached its 1200-line cap. One
 * cohesive group — everything under `/api/studio/**` sends or accepts — and
 * `types.ts` re-exports it, so no import anywhere else moves.
 */

import type { WorkflowTransition } from "./types";

export interface StudioGateCheck {
  kind: string;
  title: string;
  impact: string;
}

export interface StudioHandoffCheck {
  kind: string;
  prompt: string;
}

export interface StudioAgent {
  id: string;
  slug: string;
  name: string;
  description: string;
  role_body: string;
  role_file?: string;
  adapter: string;
  default_model: string;
  timeout: number;
  default_skill: string;
  mcp_enabled: boolean;
  mcp_tools: string[];
  gate_checks: StudioGateCheck[];
  handoff_checks: StudioHandoffCheck[];
  tool_grants: StudioAgentToolGrants;
  /** Ways the configured grants will not take effect. Empty means checked and clean. */
  tool_grant_warnings: ToolGrantWarning[];
  built_in: boolean;
  read_only?: boolean;
  version?: number;
  created_at: string;
  updated_at: string;
}

/**
 * What `PATCH /api/studio/agents/{slug}` accepts, and nothing else.
 *
 * Mirrors the server's `StudioAgentUpdate`, which sets `extra="forbid"`: any
 * other key is a 422, `slug` included — the slug is fixed after creation, since
 * it is a by-string key with no FK and no single owner, so a rename either
 * rewrites run history or orphans it. The body used to be typed
 * `Partial<StudioAgent>`, the *view* type, which let `tsc` wave through `id`,
 * `version`, `built_in`, `read_only`, `role_file`, `tool_grant_warnings` and
 * both timestamps. Spelling the writable fields out is what makes the next such
 * key a build failure rather than a runtime 422 on the operator's save.
 */
export interface StudioAgentUpdateBody {
  name?: string;
  description?: string;
  role_body?: string;
  adapter?: string;
  default_model?: string;
  timeout?: number;
  default_skill?: string;
  mcp_enabled?: boolean;
  mcp_tools?: string[];
  gate_checks?: StudioGateCheck[];
  handoff_checks?: StudioHandoffCheck[];
  tool_grants?: StudioAgentToolGrants;
  /** Goes on the new version row, not on the agent. */
  change_note?: string;
}

/** How an agent's tool access is decided. Mirrors the server `ToolPosture` enum. */
export type ToolPosture = "inherit" | "allowlist" | "unrestricted";

export interface StudioAgentToolGrants {
  posture: ToolPosture;
  allowed_tools: string[];
  disallowed_tools: string[];
  /** Registered MCP server names; empty grants every enabled server. */
  mcp_servers: string[];
}

export interface ToolGrantWarning {
  code: string;
  message: string;
  tools: string[];
}

export interface StudioAgentVersion {
  version: number;
  created_by: string;
  change_note: string;
  created_at: string;
  snapshot?: StudioAgent | null;
}

export interface StudioWorkflowVersion {
  version: number;
  created_by: string;
  change_note: string;
  created_at: string;
  snapshot?: StudioWorkflow | null;
}

export interface StudioMcpToolGuide {
  name: string;
  description: string;
  when_to_use: string;
  example: string;
  orchestrator_only: boolean;
  stage_agent: boolean;
}

export interface StudioAgentPreviewProfile {
  description: string;
  model: string;
  provider: string;
  default_skill: string;
  timeout: number;
  always_apply: boolean | null;
}

export interface StudioAgentPreview {
  name: string;
  markdown: string;
  sections: string[];
  profile: StudioAgentPreviewProfile;
}

export interface StudioDefaults {
  mcp_tools: string[];
  gate_checks: StudioGateCheck[];
  handoff_checks: StudioHandoffCheck[];
  /** Allowed `skip_when` values, served so the client never mirrors the vocabulary. */
  skip_conditions?: string[];
}

export interface StudioGeneratedAgent {
  name: string;
  slug: string;
  description: string;
  role_body: string;
  adapter: string;
  default_skill: string;
  mcp_tools: string[];
}

export interface StudioGeneratedWorkflow {
  name: string;
  slug: string;
  description: string;
  stages: StudioWorkflowStage[];
}

export interface ClassifyRoute {
  languages: string[];
  specialties: string[];
  agent_id: string;
  skill_name: string;
  default: boolean;
  /** Stage this route branches to. Empty means continue to the next stage in order. */
  to_stage?: string;
}

export interface ParallelAgentSpec {
  agent_id: string;
  skill_name: string;
}

export type StudioExitActionRequirement =
  | { kind: "runtime_capability"; capability_id: string }
  | { kind: "credential"; credential_key: string }
  | { kind: "authority"; authority_scope: string }
  | { kind: "operator_judgment"; decision_prompt: string };

export interface StudioExitActionRequirementCatalog {
  requirement_kinds: StudioExitActionRequirement["kind"][];
  capability_ids: string[];
  credential_keys: string[];
  authority_scopes: string[];
}

export interface StudioExitAction {
  key: string;
  label: string;
  description?: string;
  requirement: StudioExitActionRequirement;
}

export interface StudioWorkflowStage {
  key: string;
  name: string;
  stage_type: "agent" | "classify" | "gate" | "parallel";
  agent_id: string;
  skill_name: string;
  optional: boolean;
  order: number;
  /** When true, authored exit_actions are evaluated before leaving the stage. */
  exit_actions_enabled: boolean;
  /** Typed requirements; empty when exit_actions_enabled is false. */
  exit_actions: StudioExitAction[];
  /** Reaching this stage ends the workflow. */
  terminal?: boolean;
  /** Condition under which this stage is passed over; values from StudioDefaults.skip_conditions. */
  skip_when?: string;
  classify_routes: ClassifyRoute[];
  parallel_agents: ParallelAgentSpec[];
  model: string;
}

export interface StudioWorkflow {
  id: string;
  slug: string;
  name: string;
  description: string;
  stages: StudioWorkflowStage[];
  transitions: WorkflowTransition[];
  stripped_skills?: string[];
  published_template_id: string | null;
  published_template_slug: string;
  built_in?: boolean;
  source_path?: string;
  read_only?: boolean;
  version?: number;
  created_at: string;
  updated_at: string;
}

/** One stage field that differs between a Studio draft and its published template. */
export interface StageFieldDrift {
  field: string;
  draft: string;
  template: string;
}

/**
 * What publishing a Studio draft would change about the workflow that runs.
 *
 * `published: false` means the draft has never been published — not drift.
 * `stranded` counts live tickets currently sitting on a stage the publish would
 * remove; those instances read the live template, so their cursor would point at
 * a stage that no longer exists.
 */
export interface StudioWorkflowDrift {
  slug: string;
  published_template_slug: string;
  published: boolean;
  drifted: boolean;
  stages_added: string[];
  stages_removed: string[];
  stages_changed: Record<string, StageFieldDrift[]>;
  draft_transition_count: number;
  template_transition_count: number;
  template_version: number;
  stranded: { count: number; stage_keys: string[]; ticket_ids: string[] };
}

export interface StudioExitActionRequirementCatalog {
  requirement_kinds: StudioExitActionRequirement["kind"][];
  capability_ids: string[];
  credential_keys: string[];
  authority_scopes: string[];
}
