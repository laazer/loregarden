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

export interface GithubWorkspaceSyncRequest {
  /** Import unlinked open issues under this ticket. Blank imports nothing. */
  import_parent_ticket_id?: string;
  /** Only import issues carrying this label. */
  import_label?: string;
  policy?: GithubConflictPolicy;
}

export interface GithubWorkspaceSyncResult {
  workspace_slug: string;
  repo: string;
  links: GithubLinkSyncResult[];
  imported: GithubLinkSyncResult[];
}

/** A workspace's background sync. Off unless `enabled`. */
export interface GithubSyncSettings {
  workspace_slug: string;
  enabled: boolean;
  interval_minutes: number;
  /** Sync a linked ticket as soon as it is edited, not only on the schedule. */
  push_on_edit: boolean;
  import_parent_ticket_id: string;
  import_label: string;
  last_run_at: string | null;
  /** The last background run's failure; blank when it succeeded. */
  last_error: string;
}

export type GithubSyncSettingsUpdate = Pick<
  GithubSyncSettings,
  "enabled" | "interval_minutes" | "push_on_edit" | "import_parent_ticket_id" | "import_label"
>;

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
  syncWorkspace: (workspaceSlug: string, body: GithubWorkspaceSyncRequest = {}) =>
    request<GithubWorkspaceSyncResult>(`/api/workspaces/${workspaceSlug}/github-issues/sync`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  syncSettings: (workspaceSlug: string) =>
    request<GithubSyncSettings>(`/api/workspaces/${workspaceSlug}/github-issues/settings`),
  saveSyncSettings: (workspaceSlug: string, body: GithubSyncSettingsUpdate) =>
    request<GithubSyncSettings>(`/api/workspaces/${workspaceSlug}/github-issues/settings`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  unlink: (ticketId: string) =>
    request<void>(`/api/tickets/${ticketId}/github-issue`, { method: "DELETE" }),
};
