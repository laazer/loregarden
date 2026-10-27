/** The Editor's wire shapes: browsing, refs, files and what the branch changed. Re-exported from ./types. */

import type { BrowseImportEntry } from "./types";

export interface EditorBrowseEntry extends BrowseImportEntry {}

export interface EditorBrowseResponse {
  current_path: string;
  repo_path: string;
  parent_path: string | null;
  parent_repo_path: string | null;
  context_root: string;
  context_path: string;
  entries: EditorBrowseEntry[];
}

export interface EditorBranchRef {
  name: string;
  current: boolean;
}

export interface EditorWorktreeRef {
  path: string;
  branch: string;
  label: string;
  current: boolean;
  repo_path: string;
}

export interface EditorRefsResponse {
  workspace_root: string;
  context_root: string;
  context_path: string;
  current_branch: string;
  branches: EditorBranchRef[];
  worktrees: EditorWorktreeRef[];
}

/** How a file differs from the merge-base (`EditorChangeStatus` on the server). */
export type EditorChangeStatus = "added" | "modified" | "deleted" | "untracked";

export interface EditorChange {
  path: string;
  status: EditorChangeStatus;
  /** Null for a binary file, or an untracked one git has no counts for. */
  additions: number | null;
  deletions: number | null;
}

/** Files the open branch or worktree changed since its merge-base with the base branch. */
export interface EditorChangesResponse {
  branch: string;
  base: string;
  merge_base: string;
  files: EditorChange[];
  /** Every changed file, including those past the listed cap. */
  total: number;
}

export interface EditorFileResponse {
  path: string;
  content: string;
  language: string;
  size: number;
}
