import { request } from "./http";

/** A ticket's link to its GitHub issue. */
export interface GithubIssueLink {
  ticket_id: string;
  repo: string;
  issue_number: number;
  issue_url: string;
  last_synced_at: string;
  /** The last sync's failure, verbatim; blank when it succeeded. */
  last_error: string;
}

export type GithubSyncField = "title" | "body" | "closure";
export type GithubConflictPolicy = "report" | "local" | "remote";

export interface GithubSyncConflict {
  field: GithubSyncField;
  base: string;
  local: string;
  remote: string;
}

export interface GithubLinkSyncResult {
  ticket_id: string;
  external_id: string;
  issue_number: number;
  issue_url: string;
  pushed: GithubSyncField[];
  pulled: GithubSyncField[];
  conflicts: GithubSyncConflict[];
  error: string;
}

/** Two-way sync between a ticket and a GitHub issue (server: api/github_issues.py). */
export const githubIssueApi = {
  /** Null when the ticket is not linked. */
  link: (ticketId: string) =>
    request<GithubIssueLink | null>(`/api/tickets/${ticketId}/github-issue`),
  publish: (ticketId: string) =>
    request<GithubLinkSyncResult>(`/api/tickets/${ticketId}/github-issue`, { method: "POST" }),
  sync: (ticketId: string, policy: GithubConflictPolicy = "report") =>
    request<GithubLinkSyncResult>(`/api/tickets/${ticketId}/github-issue/sync`, {
      method: "POST",
      body: JSON.stringify({ policy }),
    }),
  unlink: (ticketId: string) =>
    request<void>(`/api/tickets/${ticketId}/github-issue`, { method: "DELETE" }),
};
