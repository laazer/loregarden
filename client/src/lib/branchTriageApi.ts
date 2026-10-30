import type { AdapterCapabilities,
  ChatMode, ChatIntent, ChatWait, DiffArtifact } from "../api/client";
import type { ChatPart } from "../components/chat/primitives/types";
import { VITE_API_BASE } from "../api/viteEnv";

export interface BranchDiffComment {
  id: string;
  workspace_id: string;
  branch: string;
  file_path: string;
  line_index: number;
  line_kind: string;
  content: string;
  resolved: boolean;
  created_at: string;
  created_by: string;
  updated_at: string;
}

export interface BranchTriageIssue {
  code: string;
  severity: "high" | "medium" | "low";
  message: string;
}

export interface BranchTriageLinkedTicket {
  id: string;
  external_id: string;
  title: string;
  state: string;
}

export interface BranchTriageWorktree {
  path: string;
  label: string;
  dirty: boolean;
  is_primary: boolean;
}

export interface BranchTriagePrStatus {
  state: "open" | "closed" | "merged" | string;
  is_draft: boolean;
  url: string;
  number: number | string;
  title: string;
}

export type BranchDiffMode = "base" | "remote" | "unstaged" | "uncommitted";

export interface BranchDiffOption {
  mode: BranchDiffMode;
  label: string;
  ref: string;
}

export interface BranchTriageEntry {
  name: string;
  is_current: boolean;
  is_base: boolean;
  /** Landed on the base branch as a squash, so `ahead` reads 0 despite the commits. */
  squash_merged: boolean;
  /**
   * Commits no other branch, tag or remote has, on a branch whose work is not
   * in the base: what deleting it would lose. Null when git could not say,
   * which counts as "some".
   */
  commits_only_here: number | null;
  ahead: number;
  behind: number;
  dirty: boolean;
  upstream: string | null;
  diff_options: BranchDiffOption[];
  worktrees: BranchTriageWorktree[];
  linked_tickets: BranchTriageLinkedTicket[];
  last_commit: { date: string; message: string };
  issues: BranchTriageIssue[];
  /** Null when the branch has no PR, or when its PR status is not known yet. */
  pr: BranchTriagePrStatus | null;
}

/**
 * What the receipt gate did, as a field rather than as the absence of an error.
 *
 * A removal can come back 200 and have deleted nothing: `refused` means a
 * premise did not hold and the checkout is untouched, `parked` means every
 * premise held, the rename succeeded and the delete did not — so the checkout
 * exists under `parked_path` and is still owned. The client branches on this;
 * it never reads "the request did not throw" as a deletion.
 */
export type BranchRemovalOutcome = "deleted" | "refused" | "parked";

export interface BranchRemovalVerdict {
  outcome: BranchRemovalOutcome;
  /**
   * The gate's enum-backed reason key (`no_receipt`, `cleanup_failed`, …), or
   * `""` on a deletion. Group and branch on this, never on `detail`: a
   * grouping keyed on prose breaks the first time anyone edits a message.
   */
  reason: string;
  /** The gate's own sentence, which says what was not deleted. */
  detail: string;
  /** Where the checkout is sitting on a `parked` outcome; `""` otherwise. */
  parked_path: string;
}

export interface BranchDeleteResult extends BranchRemovalVerdict {
  deleted: string;
  already_gone?: boolean;
  removed_worktrees?: boolean;
}

export interface BranchWorktreeRemovalResult extends BranchRemovalVerdict {
  branch: string;
  removed_path: string;
}

/** A branch as the snapshot sends it: local git only, so it paints without GitHub. */
export type BranchTriageLocalEntry = Omit<BranchTriageEntry, "pr">;

export interface BranchTriageSnapshot {
  workspace_id: string;
  workspace_slug: string;
  base_branch: string;
  current_branch: string;
  branches: BranchTriageLocalEntry[];
  issue_count: number;
}

/**
 * Whether the PR listing is every PR the repository has, as GitHub reported it.
 * Only `complete` lets a branch missing from `pull_requests` read as "no PR";
 * otherwise its status is unknown.
 */
export type BranchPrListingState = "complete" | "incomplete" | "unavailable";

export interface BranchPullRequests {
  workspace_slug: string;
  state: BranchPrListingState;
  listed_count: number;
  total_count: number | null;
  reason: string;
  pull_requests: Record<string, BranchTriagePrStatus>;
}

export interface BranchActivityCommit {
  sha: string;
  short_sha: string;
  date: string;
  author: string;
  message: string;
  pushed: boolean;
}

export interface BranchActivity {
  branch: string;
  upstream: string | null;
  commits: BranchActivityCommit[];
}

export interface CommitSnapshot extends BranchActivityCommit {
  body: string;
  files_changed: number;
  insertions: number;
  deletions: number;
}

export interface BranchTriageChatMessage {
  id: string;
  role: string;
  content: string;
  /** Resolved at write time and stored, so cards survive a reload. */
  parts?: ChatPart[];
  created_at: string;
}

export interface BranchTriageChatSnapshot {
  workspace_id: string;
  branch: string;
  linked_ticket_id: string | null;
  linked_ticket_external_id: string | null;
  messages: BranchTriageChatMessage[];
  runtime: {
    cli_adapter: string;
    claude_model: string;
    cursor_model: string;
    codex_model?: string;
    lmstudio_base_url: string;
    lmstudio_model: string;
  };
  adapter_capabilities?: AdapterCapabilities;
  chat_mode?: ChatMode;
  chat_intent?: ChatIntent;
  run_status: "idle" | "running" | "awaiting_input" | "waiting";
  active_turn_id: string | null;
  wait?: ChatWait | null;
}

async function branchTriageRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const base = VITE_API_BASE ??
    (typeof window !== "undefined" ? window.location.origin : "http://127.0.0.1:8000");
  const res = await fetch(`${base}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    if (text) {
      try {
        const payload = JSON.parse(text) as { detail?: unknown };
        if (typeof payload.detail === "string" && payload.detail.trim()) {
          throw new Error(payload.detail.trim());
        }
      } catch (error) {
        if (error instanceof Error && error.message !== text) {
          throw error;
        }
      }
    }
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

function branchQueryPath(slug: string, branch: string, suffix: string) {
  const q = new URLSearchParams({ branch });
  return `/api/workspaces/${encodeURIComponent(slug)}/branch-triage${suffix}?${q}`;
}

function branchCommentsPath(slug: string, branch: string, suffix = "") {
  return branchQueryPath(slug, branch, `/diff-comments${suffix}`);
}

export async function fetchBranchTriage(slug: string): Promise<BranchTriageSnapshot> {
  return branchTriageRequest(`/api/workspaces/${encodeURIComponent(slug)}/branch-triage`);
}

/** PR status for the branch list; fetched after it, since GitHub can take seconds. */
export async function fetchBranchPullRequests(slug: string): Promise<BranchPullRequests> {
  return branchTriageRequest(
    `/api/workspaces/${encodeURIComponent(slug)}/branch-triage/pull-requests`,
  );
}

export function withPullRequests(
  branches: BranchTriageLocalEntry[],
  prs: BranchPullRequests | undefined,
): BranchTriageEntry[] {
  return branches.map((branch) => ({ ...branch, pr: prs?.pull_requests[branch.name] ?? null }));
}

/**
 * One line on what the PR column can be trusted for, or null when it is all there.
 * Loading, partial and unavailable each say so, because each leaves branches
 * showing no PR that may well have one.
 */
export function pullRequestStatusNote(
  prs: BranchPullRequests | undefined,
  { loading, error }: { loading: boolean; error: string | null },
): string | null {
  if (error) return `Pull requests could not be loaded: ${error}`;
  if (loading || !prs) return "Loading pull request status…";
  if (prs.state === "complete") return null;
  if (prs.state === "incomplete") {
    const total = prs.total_count === null ? "?" : String(prs.total_count);
    return `Pull request status is partial (${prs.listed_count} of ${total} read): ${prs.reason}`;
  }
  return `Pull request status unavailable: ${prs.reason}`;
}

export async function fetchBranchDiffManifest(
  slug: string,
  branch: string,
  base?: string,
  mode: BranchDiffMode = "base",
): Promise<{ branch: string; base?: string; mode: BranchDiffMode; diff: DiffArtifact }> {
  return fetchBranchDiff(slug, branch, base, mode);
}

export async function fetchBranchDiffFile(
  slug: string,
  branch: string,
  filePath: string,
  base?: string,
  mode: BranchDiffMode = "base",
): Promise<{ branch: string; base?: string; mode: BranchDiffMode; file: string; diff: DiffArtifact }> {
  const q = new URLSearchParams({ branch, mode, file: filePath });
  if (base) q.set("base", base);
  return branchTriageRequest(
    `/api/workspaces/${encodeURIComponent(slug)}/branch-triage/diff?${q}`,
  );
}

export async function fetchBranchDiff(
  slug: string,
  branch: string,
  base?: string,
  mode: BranchDiffMode = "base",
): Promise<{ branch: string; base?: string; mode: BranchDiffMode; diff: DiffArtifact; file?: string | null }> {
  const q = new URLSearchParams({ branch, mode });
  if (base) q.set("base", base);
  return branchTriageRequest(
    `/api/workspaces/${encodeURIComponent(slug)}/branch-triage/diff?${q}`,
  );
}

export async function fetchBranchActivity(
  slug: string,
  branch: string,
  limit?: number,
): Promise<BranchActivity> {
  const path = branchQueryPath(slug, branch, "/activity");
  return branchTriageRequest(limit ? `${path}&limit=${limit}` : path);
}

export async function fetchCommitSnapshot(
  slug: string,
  sha: string,
): Promise<CommitSnapshot> {
  const q = new URLSearchParams({ sha });
  return branchTriageRequest(
    `/api/workspaces/${encodeURIComponent(slug)}/branch-triage/commit?${q}`,
  );
}

export async function checkoutBranchTriage(slug: string, branch: string) {
  return branchTriageRequest(`/api/workspaces/${encodeURIComponent(slug)}/branch-triage/checkout`, {
    method: "POST",
    body: JSON.stringify({ branch }),
  });
}

/**
 * `discardCommits` is the server's `force`: delete even though commits exist
 * only on this branch. Only a person's explicit choice in the delete dialog
 * sets it; the bulk cleanup never does.
 */
export async function deleteBranchTriage(
  slug: string,
  branch: string,
  discardCommits = false,
  removeWorktrees = false,
): Promise<BranchDeleteResult> {
  const q = new URLSearchParams({ branch });
  return branchTriageRequest(
    `/api/workspaces/${encodeURIComponent(slug)}/branch-triage/delete?${q}`,
    {
      method: "POST",
      body: JSON.stringify({ force: discardCommits, remove_worktrees: removeWorktrees }),
    },
  );
}

export async function removeBranchWorktree(
  slug: string,
  branch: string,
  path: string,
): Promise<BranchWorktreeRemovalResult> {
  const q = new URLSearchParams({ branch });
  return branchTriageRequest(
    `/api/workspaces/${encodeURIComponent(slug)}/branch-triage/worktrees/remove?${q}`,
    {
      method: "POST",
      body: JSON.stringify({ path }),
    },
  );
}

export async function fetchBranchChat(
  slug: string,
  branch: string,
): Promise<BranchTriageChatSnapshot> {
  return branchTriageRequest(branchQueryPath(slug, branch, "/chat"));
}

export async function sendBranchChatMessage(
  slug: string,
  branch: string,
  content: string,
): Promise<{
  user_message: BranchTriageChatMessage;
  active_turn_id: string;
  status: string;
}> {
  return branchTriageRequest(branchQueryPath(slug, branch, "/chat/messages"), {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export async function stopBranchChatTurn(
  slug: string,
  branch: string,
): Promise<BranchTriageChatSnapshot> {
  return branchTriageRequest(branchQueryPath(slug, branch, "/chat/stop"), { method: "POST" });
}

export async function listBranchDiffComments(
  slug: string,
  branch: string,
): Promise<{ comments: BranchDiffComment[]; total: number }> {
  return branchTriageRequest(branchCommentsPath(slug, branch));
}

export async function addBranchDiffComment(
  slug: string,
  branch: string,
  body: {
    file_path: string;
    line_index: number;
    line_kind: string;
    content: string;
    created_by?: string;
  },
): Promise<BranchDiffComment> {
  return branchTriageRequest(branchCommentsPath(slug, branch), {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function submitBranchDiffReviewToAgent(
  slug: string,
  branch: string,
  body?: { instructions?: string; created_by?: string },
): Promise<{
  submitted_comments: number;
  message_preview: string;
  ticket_id?: string | null;
}> {
  return branchTriageRequest(branchCommentsPath(slug, branch, "/submit-to-agent"), {
    method: "POST",
    body: JSON.stringify({
      instructions: body?.instructions ?? "",
      created_by: body?.created_by ?? "reviewer",
    }),
  });
}
