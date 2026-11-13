/**
 * The merge queue (lg-milestone-that-970): what is landing on the base branch.
 *
 * Own module, like `dockerApi`: `client.ts` does not need to grow for it. The
 * writes are also MCP tools (loregarden_dequeue_merge) and agent actions
 * (`merge_queue.retry`, `merge_queue.withdraw`), so an agent never needs this.
 */

import { request } from "./http";
import type { PullRequestCheckOutcome, PullRequestLookup, PullRequestStatus } from "./ticketPullRequestApi";

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
  /** Null for a pull request admitted by number, with no ticket behind it. */
  ticket_id: string | null;
  ticket_external_id: string;
  ticket_title: string;
  /** Lifetime sequence number in the workspace; not a place in line. */
  position: number;
  /** Live entries in line before this one; 0 for the head and a finished one. */
  ahead: number;
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

/** Mirrors `QueueOverview`: each workspace whose queue has ever held an entry. */
export interface MergeQueueOverview {
  queues: MergeQueueView[];
}

/** Mirrors `TicketQueueView`: a ticket's tree in the queue (a tree lands as one PR, under its root). */
export interface TicketMergeQueueView {
  root_ticket_id: string;
  root_external_id: string;
  /** Null when the tree has never been queued. */
  entry: MergeQueueEntry | null;
  /** Live entries in line before `entry`; 0 for the head and a finished one. */
  ahead: number;
}

/** Mirrors `services/ticket_pull_request.PullRequestRead`: "none" and "failed" stay apart. */
export interface PullRequestRead {
  lookup: PullRequestLookup;
  pull_request: PullRequestStatus | null;
  /** `gh`'s words; empty unless `lookup` is "failed". */
  error: string;
}

/** Mirrors `services/pr_cycle.PrCycleView`: a branch's PR, live, and its latest queue entry. */
export interface PrCycleView {
  branch: string;
  pull_request: PullRequestRead;
  entry: MergeQueueEntry | null;
}

/** Mirrors `api/merge_queue.EnqueueResponse`. */
export interface EnqueueResponse {
  outcome: string;
  detail: string;
  entry: MergeQueueEntry;
}

const entryPath = (entryId: string) => `/api/merge-queue/entries/${encodeURIComponent(entryId)}`;

export const mergeQueueKeys = {
  /** Every merge-queue read: what an action that moves an entry invalidates. */
  all: ["merge-queue"] as const,
  queue: (workspaceSlug: string) => ["merge-queue", "workspace", workspaceSlug] as const,
  overview: ["merge-queue", "overview"] as const,
  ticket: (ticketId: string) => ["merge-queue", "ticket", ticketId] as const,
  entry: (entryId: string, live = true) => ["merge-queue", "entry", entryId, live] as const,
  /** Under `all`, so anything that moves an entry refreshes the cycle card too. */
  prCycle: (workspaceSlug: string, branch: string, prNumber: number | null) =>
    ["merge-queue", "pr-cycle", workspaceSlug, branch, prNumber] as const,
};

export const mergeQueueApi = {
  queue: (workspaceSlug: string): Promise<MergeQueueView> =>
    request<MergeQueueView>(`/api/merge-queue/${encodeURIComponent(workspaceSlug)}`),
  overview: (): Promise<MergeQueueOverview> => request<MergeQueueOverview>("/api/merge-queue"),
  forTicket: (ticketId: string): Promise<TicketMergeQueueView> =>
    request<TicketMergeQueueView>(`/api/merge-queue/tickets/${encodeURIComponent(ticketId)}`),
  /**
   * `live` reads a testing entry's checks from GitHub now, and that read failing
   * is a 502, not an empty list; otherwise they are what the loop recorded on
   * its last cycle (no GitHub call).
   */
  entry: (entryId: string, live = true): Promise<MergeQueueEntryDetail> =>
    request<MergeQueueEntryDetail>(live ? entryPath(entryId) : `${entryPath(entryId)}?live=false`),
  /** A branch's PR read live from GitHub (a `gh` call), plus its latest queue entry. */
  prCycle: (workspaceSlug: string, branch: string, prNumber: number | null): Promise<PrCycleView> => {
    const params = new URLSearchParams({ branch });
    if (prNumber) params.set("pr_number", String(prNumber));
    return request<PrCycleView>(`/api/merge-queue/${encodeURIComponent(workspaceSlug)}/pr-cycle?${params}`);
  },
  /** Queue an open PR by number; a refusal is a 409 carrying the queue's reason. */
  enqueuePullRequest: (workspaceSlug: string, prNumber: number): Promise<EnqueueResponse> =>
    request<EnqueueResponse>(`/api/merge-queue/${encodeURIComponent(workspaceSlug)}/entries`, {
      method: "POST",
      body: JSON.stringify({ pr_number: prNumber }),
    }),
  retry: (entryId: string): Promise<MergeQueueEntry> =>
    request<MergeQueueEntry>(`${entryPath(entryId)}/retry`, { method: "POST" }),
  withdraw: (entryId: string, reason = ""): Promise<MergeQueueEntry> =>
    request<MergeQueueEntry>(`${entryPath(entryId)}/withdraw`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
};
