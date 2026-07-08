import type { DiffArtifact } from "../api/client";

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
  ahead: number;
  behind: number;
  dirty: boolean;
  upstream: string | null;
  diff_options: BranchDiffOption[];
  worktrees: BranchTriageWorktree[];
  linked_tickets: BranchTriageLinkedTicket[];
  last_commit: { date: string; message: string };
  issues: BranchTriageIssue[];
}

export interface BranchTriageSnapshot {
  workspace_id: string;
  workspace_slug: string;
  base_branch: string;
  current_branch: string;
  branches: BranchTriageEntry[];
  issue_count: number;
}

export interface BranchTriageChatMessage {
  id: string;
  role: string;
  content: string;
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
    lmstudio_base_url: string;
    lmstudio_model: string;
  };
}

async function branchTriageRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const base =
    import.meta.env.VITE_API_BASE ??
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

export async function checkoutBranchTriage(slug: string, branch: string) {
  return branchTriageRequest(`/api/workspaces/${encodeURIComponent(slug)}/branch-triage/checkout`, {
    method: "POST",
    body: JSON.stringify({ branch }),
  });
}

export async function deleteBranchTriage(
  slug: string,
  branch: string,
  force = false,
  removeWorktrees = false,
): Promise<{ deleted: string; already_gone?: boolean; removed_worktrees?: boolean }> {
  const q = new URLSearchParams({ branch });
  return branchTriageRequest(
    `/api/workspaces/${encodeURIComponent(slug)}/branch-triage/delete?${q}`,
    {
      method: "POST",
      body: JSON.stringify({ force, remove_worktrees: removeWorktrees }),
    },
  );
}

export async function fetchBranchChat(
  slug: string,
  branch: string,
): Promise<BranchTriageChatSnapshot> {
  return branchTriageRequest(branchQueryPath(slug, branch, "/chat"));
}

export async function sendBranchChatMessage(slug: string, branch: string, content: string) {
  return branchTriageRequest(branchQueryPath(slug, branch, "/chat/messages"), {
    method: "POST",
    body: JSON.stringify({ content }),
  });
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
