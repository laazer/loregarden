/**
 * The review page: pending human gates grouped by the build that holds them,
 * the build launcher, and the endpoint try-it proxy. Mirrors `api/review.py`.
 */

import type { Approval } from "./client";
import { API_BASE, request } from "./http";
import type { LocalInstanceState } from "./localInstancesTypes";

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

/** Mirrors `ReviewScreenshot` in `services/review_kit.py`. */
export interface ReviewScreenshot {
  capture_id: string;
  caption: string;
  /** The scene pictured; empty when it is a page, or nobody said. */
  scene: string;
}

/** Mirrors `ReviewStep`: one thing the reviewer does and what they should see. */
export interface ReviewStep {
  action: string;
  expect: string;
  wrong_if: string;
}

/**
 * Mirrors `ReviewLink`: exactly one of `path` (a page of the app, opened on the
 * running build) or `url` (a page outside it) is set.
 */
export interface ReviewLink {
  url: string;
  path: string;
  label: string;
}

/** Mirrors `PreviewInstance` in `services/review_preview.py`. */
export interface PreviewInstance {
  id: string;
  url: string;
  state: LocalInstanceState;
  worktree: string;
}

/** Which running instance shows a build, and whether Start preview can launch one. */
export interface PreviewView {
  ref: string;
  client: PreviewInstance | null;
  server: PreviewInstance | null;
  can_start: boolean;
  unavailable: string;
}

export interface CapturedPage {
  path: string;
  captured: boolean;
  note: string;
}

export interface CaptureResult {
  base_url: string;
  pages: CapturedPage[];
  artifact_id: string;
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
  /** The scene the steps happen in; empty when they start from the build's own start. */
  scene: string;
  steps: ReviewStep[];
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

/** Mirrors `LaunchKind` in `services/review_config.py`: a check's exit code is its answer. */
export type LaunchKind = "app" | "check";

/** Mirrors `LaunchOffer`: which of the workspace's targets run this ticket's changes. */
export interface LaunchOffer {
  match: "matched" | "no_match" | "unknown" | "unconfigured";
  targets: string[];
}

export interface ReviewItem {
  approval: Approval;
  ticket: ReviewTicket;
  /** Scene files the ticket changes; null when git could not say. */
  scenes: string[] | null;
  launch: LaunchOffer;
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
  target: string;
  kind: LaunchKind;
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
  config: {
    configured: boolean;
    path: string;
    error: string;
    targets: { name: string; kind: LaunchKind }[];
  };
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
  launch: (workspaceSlug: string, body: { ref: string; scene?: string; target?: string }): Promise<LaunchState> =>
    request<LaunchState>(`/api/review/launch/${encodeURIComponent(workspaceSlug)}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  stop: (workspaceSlug: string): Promise<LaunchView> =>
    request<LaunchView>(`/api/review/launch/${encodeURIComponent(workspaceSlug)}/stop`, { method: "POST" }),
  tryRequest: (body: { base_url: string; method: HttpMethod; path: string; body: string }): Promise<TryResult> =>
    request<TryResult>("/api/review/try", { method: "POST", body: JSON.stringify(body) }),
  preview: (workspaceSlug: string, ref: string): Promise<PreviewView> =>
    request<PreviewView>(`/api/review/preview/${encodeURIComponent(workspaceSlug)}?ref=${encodeURIComponent(ref)}`),
  startPreview: (workspaceSlug: string, ref: string): Promise<PreviewView> =>
    request<PreviewView>(`/api/review/preview/${encodeURIComponent(workspaceSlug)}?ref=${encodeURIComponent(ref)}`, {
      method: "POST",
    }),
  capture: (
    workspaceSlug: string,
    body: { ref: string; ticket_id: string; paths: string[] },
  ): Promise<CaptureResult> =>
    request<CaptureResult>(`/api/review/capture/${encodeURIComponent(workspaceSlug)}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

/** A preview whose instances are still coming up, worth re-reading. */
export function previewIsStarting(view: PreviewView | undefined): boolean {
  return view?.client?.state === "starting" || view?.server?.state === "starting";
}

export function captureUrl(ticketId: string, captureId: string): string {
  return `${API_BASE}/api/review/captures/${encodeURIComponent(ticketId)}/${encodeURIComponent(captureId)}`;
}

/** Phases a launch is still moving through, worth re-reading. */
export function launchIsLive(state: LaunchState | null | undefined): boolean {
  return state?.phase === "preparing" || state?.phase === "running";
}
