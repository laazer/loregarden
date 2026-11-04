/**
 * The merge queue (lg-milestone-that-970): what is landing on the base branch.
 *
 * Own module, like `dockerApi`: `client.ts` does not need to grow for it. The
 * writes are also MCP tools (loregarden_dequeue_merge) and agent actions
 * (`merge_queue.retry`, `merge_queue.withdraw`), so an agent never needs this.
 */

import { request } from "./http";
import type { PullRequestCheckOutcome } from "./ticketPullRequestApi";

/** Mirrors `MergeQueueState`. */
export type MergeQueueState =
  | "queued"
  | "staging"
  | "testing"
  | "merging"
  | "merged"
  | "failed"
  | "timed_out"
  | "withdrawn";

/** Mirrors `MergeQueueWait`; "" when nothing holds the entry. */
export type MergeQueueWait =
  | ""
  | "behind"
  | "checks_unreported"
  | "checks_running"
  | "github_unreachable"
  | "base_moved"
  | "conflict_artifact_open";

/** Mirrors `services/merge_queue_requests.EntryView`. */
export interface MergeQueueEntry {
  id: string;
  ticket_id: string;
  ticket_external_id: string;
  ticket_title: string;
  position: number;
  state: MergeQueueState;
  wait: MergeQueueWait;
  is_head: boolean;
  pr_number: number;
  pr_url: string;
  source_branch: string;
  head_sha: string;
  staged_sha: string;
  staged_tree: string;
  merge_commit: string;
  landed_tree: string;
  attempts: number;
  detail: string;
  /** The failing check's log tail, on a failed entry. */
  log_tail: string;
  conflicted_files: string[];
  artifact_ref: string;
  can_withdraw: boolean;
  can_retry: boolean;
  enqueued_at: string;
  state_changed_at: string;
}

export interface MergeQueueCheck {
  name: string;
  outcome: PullRequestCheckOutcome;
  url: string;
}

/** Mirrors `EntryDetail`. */
export interface MergeQueueEntryDetail extends MergeQueueEntry {
  checks: MergeQueueCheck[];
  checks_live: boolean;
}

export interface MergeQueueView {
  workspace_slug: string;
  entries: MergeQueueEntry[];
  head_id: string | null;
}

const entryPath = (entryId: string) => `/api/merge-queue/entries/${encodeURIComponent(entryId)}`;

export const mergeQueueKeys = {
  queue: (workspaceSlug: string) => ["merge-queue", workspaceSlug] as const,
  entry: (entryId: string) => ["merge-queue", "entry", entryId] as const,
};

export const mergeQueueApi = {
  queue: (workspaceSlug: string): Promise<MergeQueueView> =>
    request<MergeQueueView>(`/api/merge-queue/${encodeURIComponent(workspaceSlug)}`),
  /** A testing entry's checks are read from GitHub; that read failing is a 502, not an empty list. */
  entry: (entryId: string): Promise<MergeQueueEntryDetail> => request<MergeQueueEntryDetail>(entryPath(entryId)),
  retry: (entryId: string): Promise<MergeQueueEntry> =>
    request<MergeQueueEntry>(`${entryPath(entryId)}/retry`, { method: "POST" }),
  withdraw: (entryId: string, reason = ""): Promise<MergeQueueEntry> =>
    request<MergeQueueEntry>(`${entryPath(entryId)}/withdraw`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
};
