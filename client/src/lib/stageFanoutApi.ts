/**
 * The four calls a fan-out review needs.
 *
 * Kept out of `api/client.ts` for the same reason the queue and branch-triage
 * calls are: that file is already the app's largest surface, and this group is
 * self-contained.
 *
 * Launching is slow by nature — it waits for N agent runs — so callers should
 * expect minutes, not milliseconds.
 */

import { request } from "../api/http";

export interface FanoutAttempt {
  id: string;
  attempt_index: number;
  attempt_name: string;
  agent_run_id: string | null;
  worktree_id: string | null;
  branch: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  failure_details: string;
}

export interface FanoutDiff {
  attempt_id: string;
  branch: string;
  stat: string;
  patch: string;
  files_changed: number;
  truncated: boolean;
}

export interface FanoutGroup {
  id: string;
  ticket_id: string;
  stage_key: string;
  attempt_count: number;
  status: string;
  outcome: string;
  winner_attempt_id: string | null;
  declined_reason: string;
  failure_summary: string;
  attempts: FanoutAttempt[];
  diffs?: FanoutDiff[];
  discarded_attempts?: string[];
}

export interface LaunchFanoutBody {
  stage_key: string;
  attempt_count: number;
  agent_id?: string;
  skill_name?: string;
  auto_approve?: boolean;
}

export interface FanoutList {
  groups: FanoutGroup[];
  /** The one still awaiting a verdict, if any. */
  open_group_id: string | null;
}

export const stageFanoutApi = {
  list: (ticketId: string) => request<FanoutList>(`/api/tickets/${ticketId}/fanout`),

  launch: (ticketId: string, body: LaunchFanoutBody) =>
    request<FanoutGroup>(`/api/tickets/${ticketId}/fanout`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  read: (ticketId: string, groupId: string) =>
    request<FanoutGroup>(`/api/tickets/${ticketId}/fanout/${groupId}`),

  promote: (ticketId: string, groupId: string, attemptId: string) =>
    request<FanoutGroup>(`/api/tickets/${ticketId}/fanout/${groupId}/promote/${attemptId}`, {
      method: "POST",
      body: "{}",
    }),

  decline: (ticketId: string, groupId: string, reason = "") =>
    request<FanoutGroup>(`/api/tickets/${ticketId}/fanout/${groupId}/decline`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
};
