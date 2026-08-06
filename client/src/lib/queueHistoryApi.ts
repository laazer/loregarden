import { API_BASE } from "../api/client";

/**
 * A lane entry that already ran.
 *
 * `status` is the raw queue position and is a trap to read directly — `started`
 * is the terminal "lane released" state, not a running one. `outcome` is what
 * actually happened to the ticket, derived server-side from the orchestration
 * run; render that.
 */
export interface QueueHistoryEntry {
  entry_id: string;
  workspace_id: string;
  workspace_slug: string;
  workspace_name: string;
  slot_number: number;
  entry_kind: string;
  stage_key: string;
  status: string;
  outcome: QueueHistoryOutcome;
  ticket_id: string;
  ticket_external_id: string;
  ticket_title: string;
  ticket_state: string;
  orchestration_run_id: string | null;
  run_code: string;
  last_stage_key: string;
  failure_reason: string;
  retry_count: number;
  created_at: string | null;
  promoted_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
}

export type QueueHistoryOutcome =
  | "succeeded"
  | "blocked"
  | "failed"
  | "cancelled"
  | "running"
  | "unknown";

export interface QueueHistoryPage {
  entries: QueueHistoryEntry[];
  total: number;
  limit: number;
  offset: number;
}

export async function listQueueHistory(options?: {
  workspaceId?: string;
  outcome?: string;
  slotNumber?: number;
  ticketId?: string;
  limit?: number;
}): Promise<QueueHistoryPage> {
  const params = new URLSearchParams();
  if (options?.workspaceId) params.set("workspace_id", options.workspaceId);
  if (options?.outcome) params.set("outcome", options.outcome);
  if (options?.slotNumber !== undefined) params.set("slot_number", String(options.slotNumber));
  if (options?.ticketId) params.set("ticket_id", options.ticketId);
  params.set("limit", String(options?.limit ?? 25));

  const res = await fetch(`${API_BASE}/api/parallel/lanes/history?${params.toString()}`);
  if (!res.ok) {
    throw new Error((await res.text()) || res.statusText);
  }
  return res.json() as Promise<QueueHistoryPage>;
}
