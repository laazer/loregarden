import { request } from "./http";

/** Mirrors `services/ticket_pull_request.py`. */
export type PullRequestLookup = "found" | "none" | "failed";
export type PullRequestState = "open" | "merged" | "closed";
export type PullRequestCheckOutcome = "passing" | "failing" | "pending" | "skipped";
export type PullRequestReview = "approved" | "changes_requested" | "review_required" | "not_required";

export interface PullRequestCheck {
  name: string;
  outcome: PullRequestCheckOutcome;
  url: string;
}

export interface PullRequestStatus {
  number: number;
  url: string;
  title: string;
  state: PullRequestState;
  is_draft: boolean;
  base: string;
  head: string;
  additions: number;
  deletions: number;
  changed_files: number;
  review: PullRequestReview;
  has_conflicts: boolean;
  checks: PullRequestCheck[];
  body: string;
  /** The commit GitHub would merge; "Merge and clean up" is pinned to it. */
  head_sha: string;
  /** GitHub's own "can merge now": open and `mergeStateStatus` CLEAN. */
  mergeable_now: boolean;
  /** "<short sha> <subject>" per commit GitHub cannot verify; asked only while the PR is blocked. */
  unsigned_commits: string[];
}

export interface MergeStep {
  step: string;
  ok: boolean;
  detail: string;
}

/** Mirrors `services/pull_request_merge.py`. */
export interface MergeAndCleanUp {
  number: number;
  merge_commit: string;
  steps: MergeStep[];
}

export interface TicketPullRequest {
  lookup: PullRequestLookup;
  branch: string;
  pull_request: PullRequestStatus | null;
  /** Why the lookup failed; empty unless `lookup` is "failed". */
  error: string;
  /** The PR Loregarden's own "Open PR" recorded, for when GitHub cannot be asked. */
  recorded: { url: string; number: string; title: string } | null;
  /** The integration branch a tree member ships on; "" when the ticket ships its own branch. */
  ships_with: string;
}

/** Its own key, not under ["ticket", id]: that prefix is invalidated on every
 * ticket event, and each refetch here is a `gh` call to GitHub. */
export const ticketPullRequestKey = (ticketId: string) => ["ticket-pull-request", ticketId] as const;

export const ticketPullRequestApi = {
  /** Asks GitHub (via `gh`) for the PR on the ticket's branch, live. */
  ticketPullRequest: (id: string) => request<TicketPullRequest>(`/api/tickets/${id}/pull-request`),
  /** Squash-merges at `head_sha` (refused if the PR moved), then removes its branch everywhere. */
  mergeTicketPullRequest: (id: string, body: { number: number; head_sha: string }) =>
    request<MergeAndCleanUp>(`/api/tickets/${id}/pull-request/merge`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
