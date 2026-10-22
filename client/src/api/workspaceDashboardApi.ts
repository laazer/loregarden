import { request } from "./http";
import type { PullRequestStatus } from "./ticketPullRequestApi";
import type { TicketState } from "./types";

/** Mirrors `services/workspace_remote.py`. */
export interface WorkspaceRemote {
  workspace_slug: string;
  repo_root: string;
  remote_url: string;
  /** The browsable https URL; "" when origin is missing or unreadable. */
  web_url: string;
  /** Why there is no `web_url`; "" when there is one. */
  error: string;
}

/** Mirrors `PullRequestReviewTier`, in ranking order. */
export type PullRequestReviewTier = "ready_to_merge" | "to_review" | "needs_work" | "conflicting" | "draft";
export type CIStatus = "pending" | "passing" | "failing" | "partial" | "skipped";

export interface LinkedTicket {
  id: string;
  external_id: string;
  title: string;
  state: TicketState;
}

/** Mirrors `services/workspace_pull_requests.py`. */
export interface WorkspacePullRequest {
  pull_request: PullRequestStatus;
  ci: CIStatus;
  author: string;
  created_at: string;
  updated_at: string;
  tier: PullRequestReviewTier;
  /** 1-based position in the review order. */
  review_rank: number;
  /** GitHub returned fewer files than the PR changes, so overlap is a lower bound. */
  files_truncated: boolean;
  tickets: LinkedTicket[];
  overlaps: { number: number; shared_files: number }[];
}

export interface WorkspacePullRequests {
  workspace_slug: string;
  /** Every open PR, already in review order. */
  pull_requests: WorkspacePullRequest[];
  /**
   * GitHub's own `hasNextPage` on the last page read. `incomplete` means there
   * are open PRs this list does not hold. A listing that could not be read is
   * an error response, never an empty `complete` one.
   */
  listing_state: "complete" | "incomplete";
}

/** Mirrors `ActivityHighlightKind`, in display order. */
export const ACTIVITY_KINDS = [
  "created",
  "moved",
  "landed",
  "landing_failed",
  "stage_rejected",
  "run_failed",
  "gate_failed",
  "approval_requested",
] as const;
export type ActivityHighlightKind = (typeof ACTIVITY_KINDS)[number];

export interface ActivityHighlight {
  kind: ActivityHighlightKind;
  detail: string;
  count: number;
}

export interface TicketActivityDigest {
  ticket_id: string;
  external_id: string;
  title: string;
  state: TicketState;
  last_event_at: string;
  highlights: ActivityHighlight[];
}

/** Mirrors `services/workspace_activity.py`. */
export interface WorkspaceActivity {
  workspace_slug: string;
  since: string;
  kinds: ActivityHighlightKind[];
  /** The whole window's highlights, whatever `kinds` narrowed the list to. */
  totals: Partial<Record<ActivityHighlightKind, number>>;
  tickets: TicketActivityDigest[];
  more_tickets: number;
  events_truncated: boolean;
}

const slugPath = (slug: string) => `/api/workspaces/${encodeURIComponent(slug)}`;

export const workspaceDashboardKeys = {
  remote: (slug: string) => ["workspace-remote", slug] as const,
  /** Its own prefix: each refetch is a `gh` call to GitHub. */
  pullRequests: (slug: string) => ["workspace-pull-requests", slug] as const,
  activity: (slug: string, since: string, kinds: readonly ActivityHighlightKind[]) =>
    ["workspace-activity", slug, since, [...kinds]] as const,
};

export const workspaceDashboardApi = {
  remote: (slug: string) => request<WorkspaceRemote>(`${slugPath(slug)}/remote`),
  pullRequests: (slug: string) => request<WorkspacePullRequests>(`${slugPath(slug)}/pull-requests`),
  activity: (slug: string, since: string, kinds: readonly ActivityHighlightKind[]) => {
    const params = new URLSearchParams({ since });
    for (const kind of kinds) params.append("kind", kind);
    return request<WorkspaceActivity>(`${slugPath(slug)}/activity?${params.toString()}`);
  },
};
