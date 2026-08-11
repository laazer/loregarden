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
  kind: "workflow_gate" | "cli_permission" | "cli_question";
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

export interface TriageSnapshot {
  pending_approvals: Approval[];
  recent_approvals: Approval[];
  messages: TriageMessage[];
  runtime: WorkspaceRuntimeSettings;
  adapter_capabilities?: AdapterCapabilities;
  chat_intent?: ChatIntent;
  run_status: TriageRunStatus;
  active_run_id: string | null;
}

export interface TriageSendResult {
  user_message: TriageMessage;
  run_id: string;
  status: "queued";
}
