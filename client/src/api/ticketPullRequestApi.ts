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
};
