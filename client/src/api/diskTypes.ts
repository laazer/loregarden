/** Shapes of `/api/machine/disk` (server/loregarden/api/machine_disk.py). */

export type DiskKind =
  | "dependencies"
  | "build_output"
  | "cache"
  | "worktree"
  | "workspace"
  | "git_store"
  | "loregarden_data"
  | "directory"
  | "file"
  | "other";

export interface DiskNode {
  name: string;
  path: string;
  size_bytes: number;
  kind: DiskKind;
  is_dir: boolean;
  file_count: number;
  children: DiskNode[];
  /** On an `other` node: how many entries it stands for. */
  folded_count: number;
}

export type DiskScanState = "idle" | "running" | "done" | "failed";

export interface DiskScanStatus {
  state: DiskScanState;
  started_at: string | null;
  entries: number;
  current_root: string;
  error: string;
}

export interface DiskRootSummary {
  path: string;
  label: string;
  origin: "workspace" | "worktrees" | "added";
  workspace_slug: string;
}

export interface DiskScanSummary {
  started_at: string;
  finished_at: string;
  entries: number;
  unreadable: number;
  unreadable_samples: string[];
  truncated: boolean;
  unreadable_workspaces: string[];
  missing_added: string[];
  total_bytes: number;
  roots: DiskRootSummary[];
}

export interface DiskVolume {
  path: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
}

export interface DiskOverview {
  volume: DiskVolume;
  status: DiskScanStatus;
  scan: DiskScanSummary | null;
  added_roots: string[];
}

export type DiskCleanupMethod = "trash" | "trash_worktree";

export interface DiskVerdict {
  path: string;
  kind: DiskKind;
  allowed: boolean;
  method: DiskCleanupMethod | null;
  reasons: string[];
  notes: string[];
  fingerprint: string;
  worktree_repo: string;
}

export interface DiskCleanupPlan {
  plan_id: string;
  expires_at: string;
  items: { verdict: DiskVerdict; size_bytes: number }[];
  allowed_bytes: number;
  allowed_count: number;
  refused_count: number;
  covered: string[];
}

export type DiskCleanupOutcome = "trashed" | "refused" | "failed";

export interface DiskCleanupResult {
  results: {
    path: string;
    outcome: DiskCleanupOutcome;
    size_bytes: number;
    detail: string;
  }[];
  trashed_bytes: number;
  trashed_count: number;
  refused_count: number;
  failed_count: number;
}

export interface DiskCleanupEvent {
  path: string;
  size_bytes: number;
  kind: DiskKind;
  method: DiskCleanupMethod;
  outcome: DiskCleanupOutcome;
  detail: string;
  created_at: string;
}
