// The conversation shapes, split out of `types.ts`.
//
// Every Baxter rail — ticket triage, branch triage, Home — speaks these, and
// they were the block of `types.ts` that grew whenever a chat surface gained a
// field. Re-exported from `types.ts`, so importers are unaffected.

import type { ChatPart } from "../components/chat/primitives/types";
import type { WorkspaceRuntimeSettings } from "./types";

export interface AgentQuestionOption {
  label: string;
  description?: string;
}

export interface AgentQuestion {
  question: string;
  header?: string;
  multiSelect?: boolean;
  options: AgentQuestionOption[];
}

/**
 * What an agent did about a step it could not finish, handed over with the
 * block rather than described in it.
 *
 * `tier` is a ladder the agent works down: it ran the thing itself
 * (`agent_attempted`), it committed a script the control plane can run
 * (`one_click`), or a person has to be present (`manual`).
 */
export interface PreparedAction {
  tier: "agent_attempted" | "one_click" | "manual";
  attempted: string;
  prepared: string;
  command: string;
  script_path: string;
  captures: string[];
}

export interface Approval {
  id: string;
  title: string;
  level: string;
  workspace_slug: string;
  stage_key: string;
  stage_name: string;
  impact: string;
  checklist?: string[];
  route_options?: { key: string; name: string }[];
  ticket_id: string;
  ticket_external_id: string;
  kind: "workflow_gate" | "cli_permission" | "cli_question" | "human_action";
  /** Present on `human_action`: what the agent prepared before handing over. */
  prepared_action?: PreparedAction | null;
  status?: string;
  run_id: string;
  tool_name: string;
  tool_input_json: string;
  cli_adapter: string;
  questions?: AgentQuestion[];
  resolved_answers?: Record<string, string | string[]> | null;
  created_at?: string;
  resolved_at?: string;
}

export interface TriageMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  /** Resolved at write time and stored, so cards survive a reload. */
  parts?: ChatPart[];
  created_at: string;
}

/** One Home Baxter conversation, as the archive lists it. */
export interface BaxterChatSessionSummary {
  id: string;
  title: string;
  message_count: number;
  preview: string;
  created_at: string;
  updated_at: string;
}

export interface BaxterChatSnapshot {
  id: string;
  workspace_id: string;
  title: string;
  messages: TriageMessage[];
  /** Home-chat permission/question cards for the in-flight turn (also on the board). */
  pending_approvals?: Approval[];
  runtime: WorkspaceRuntimeSettings;
  /** What the selected adapter can do — same matrix for every chat surface. */
  adapter_capabilities?: AdapterCapabilities;
  chat_mode?: ChatMode;
  run_status: TriageRunStatus;
  active_turn_id: string | null;
  created_at: string;
  updated_at: string;
}

export type TriageRunStatus = "idle" | "running" | "awaiting_input";

/** What the resolved adapter can do. One matrix, shared by every chat surface. */
export interface AdapterCapabilities {
  adapter: string;
  permission_bridge: boolean;
  inbox_approvals: boolean;
  plan_execute: boolean;
  /** Has a write path, but only reaches it with permission bypass on. */
  requires_permission_bypass?: boolean;
  stream_thinking: boolean;
  steer: boolean;
}

/**
 * Whether a chat turn will be able to act, or can only answer.
 *
 * Resolved server-side from the adapter's capabilities. A surface may still
 * drop an individual turn to "advisory" for reasons the snapshot cannot know.
 */
export type ChatIntent = "advisory" | "execute";

/** Whether the next turn on this rail can change anything. */
export type ChatModeName = "act" | "advisory";

/**
 * Why a rail cannot act. One member per real cause — see `services/chat_mode`.
 *
 * `aside_observer` and `surface_is_read_only` are not faults: they are rails
 * that answer from the record on purpose, and the UI labels them rather than
 * offering a fix.
 */
export type ChatAdvisoryCause =
  | "adapter_cannot_execute"
  | "adapter_needs_permission_bypass"
  | "branch_not_checked_out"
  | "no_run_for_approvals"
  | "surface_is_read_only"
  | "aside_observer";

/**
 * The mode of the rail's next turn, with the reason and the way out.
 *
 * Resolved by the same server function the turn itself uses, so the badge
 * cannot promise something the turn will not do.
 */
export interface ChatMode {
  mode: ChatModeName;
  cause: ChatAdvisoryCause | null;
  reason: string;
  advice: string;
  /** Whether the operator can clear this themselves. */
  remediable: boolean;
}

export interface TriageSnapshot {
  pending_approvals: Approval[];
  recent_approvals: Approval[];
  messages: TriageMessage[];
  runtime: WorkspaceRuntimeSettings;
  adapter_capabilities?: AdapterCapabilities;
  chat_intent?: ChatIntent;
  chat_mode?: ChatMode;
  run_status: TriageRunStatus;
  active_run_id: string | null;
}

export interface TriageSendResult {
  user_message: TriageMessage;
  run_id: string;
  status: "queued";
}
