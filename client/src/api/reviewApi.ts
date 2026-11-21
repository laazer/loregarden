/**
 * The review page: pending human gates grouped by the build that holds them,
 * the build launcher, and the endpoint try-it proxy. Mirrors `api/review.py`.
 */

import type { Approval } from "./client";
import { API_BASE, request } from "./http";

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

/** Mirrors `ReviewScreenshot` in `services/review_kit.py`. */
export interface ReviewScreenshot {
  capture_id: string;
  caption: string;
}

export interface ReviewLink {
  url: string;
  label: string;
}

/** Mirrors `ReviewRequest`: a path on whichever local server the reviewer picks. */
export interface ReviewRequest {
  name: string;
  method: HttpMethod;
  path: string;
  body: string;
  expect_status: number | null;
}

export interface TicketReviewKit {
  screenshots: ReviewScreenshot[];
  links: ReviewLink[];
  requests: ReviewRequest[];
  /** Evidence artifacts whose kit no longer parses. */
  unreadable: string[];
}

export interface ReviewTicket {
  id: string;
  external_id: string;
  title: string;
  description: string;
  revision: number;
  acceptance_criteria: string[];
  checked_criteria: string[];
}

export interface ReviewItem {
  approval: Approval;
  ticket: ReviewTicket;
  /** Scene files the ticket changes; null when git could not say. */
  scenes: string[] | null;
  kit: TicketReviewKit;
}

export interface ReviewBuild {
  workspace_slug: string;
  ref: string;
  /** An integration branch several tickets landed on. */
  combined: boolean;
  items: ReviewItem[];
}

export interface ReviewQueue {
  builds: ReviewBuild[];
  total: number;
}

/** Mirrors `LaunchPhase` in `services/review_launcher.py`. */
export type LaunchPhase = "preparing" | "running" | "exited" | "failed" | "stopped";

export interface LaunchState {
  workspace_slug: string;
  ref: string;
  sha: string;
  scene: string;
  worktree: string;
  phase: LaunchPhase;
  pid: number | null;
  exit_code: number | null;
  error: string;
  log_tail: string[];
  started_at: string;
}

export interface LaunchView {
  config: { configured: boolean; path: string; error: string };
  state: LaunchState | null;
}

export interface TryResult {
  ok: boolean;
  status: number | null;
  elapsed_ms: number;
  content_type: string;
  body: string;
  truncated: boolean;
  error: string;
}

export const reviewApi = {
  queue: (workspaceSlug?: string): Promise<ReviewQueue> =>
    request<ReviewQueue>(
      workspaceSlug ? `/api/review/queue?workspace_slug=${encodeURIComponent(workspaceSlug)}` : "/api/review/queue",
    ),
  launchStatus: (workspaceSlug: string): Promise<LaunchView> =>
    request<LaunchView>(`/api/review/launch/${encodeURIComponent(workspaceSlug)}`),
  launch: (workspaceSlug: string, body: { ref: string; scene?: string }): Promise<LaunchState> =>
    request<LaunchState>(`/api/review/launch/${encodeURIComponent(workspaceSlug)}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  stop: (workspaceSlug: string): Promise<LaunchView> =>
    request<LaunchView>(`/api/review/launch/${encodeURIComponent(workspaceSlug)}/stop`, { method: "POST" }),
  tryRequest: (body: { base_url: string; method: HttpMethod; path: string; body: string }): Promise<TryResult> =>
    request<TryResult>("/api/review/try", { method: "POST", body: JSON.stringify(body) }),
};

export function captureUrl(ticketId: string, captureId: string): string {
  return `${API_BASE}/api/review/captures/${encodeURIComponent(ticketId)}/${encodeURIComponent(captureId)}`;
}

/** Phases a launch is still moving through, worth re-reading. */
export function launchIsLive(state: LaunchState | null | undefined): boolean {
  return state?.phase === "preparing" || state?.phase === "running";
}
