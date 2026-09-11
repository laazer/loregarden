import type { BranchTriageEntry } from "./branchTriageApi";

/**
 * Why a branch is a candidate for cleanup.
 *
 * Each id is a *reason* to delete, not a property of the branch — so the
 * categories double as the quick-select rows in the cleanup modal. A branch can
 * carry several at once (a merged PR whose branch is also stale), and selection
 * is a union: ticking a category adds its members, unticking removes them.
 */
export type CleanupCategoryId = "pr_merged" | "merged_into_base" | "pr_closed" | "stale";

export interface CleanupCategory {
  id: CleanupCategoryId;
  /** Shown on the quick-select row. `base` is the workspace's base branch. */
  label: (base: string) => string;
  hint: string;
  /** Whether members start ticked when the modal opens. */
  selectedByDefault: boolean;
  matches: (entry: BranchTriageEntry) => boolean;
}

/**
 * Merged into the base branch — by a merge commit, a squash, or by never having
 * diverged at all.
 *
 * `ahead` is already zeroed by the snapshot for squash-merged branches, so this
 * is one test rather than two; `squash_merged` only distinguishes *how* for the
 * label the row shows.
 */
function isMergedIntoBase(entry: BranchTriageEntry): boolean {
  return entry.ahead === 0;
}

export const CLEANUP_CATEGORIES: readonly CleanupCategory[] = [
  {
    id: "pr_merged",
    label: () => "Pull request merged",
    hint: "GitHub reports the branch's PR as merged.",
    selectedByDefault: true,
    matches: (entry) => entry.pr?.state === "merged",
  },
  {
    id: "merged_into_base",
    label: (base) => `Merged into ${base}`,
    hint: "No commits left that the base branch does not already have.",
    selectedByDefault: true,
    matches: isMergedIntoBase,
  },
  {
    id: "pr_closed",
    label: () => "Pull request closed unmerged",
    hint: "The PR was closed without merging — the commits are still only here.",
    selectedByDefault: false,
    matches: (entry) => entry.pr?.state === "closed",
  },
  {
    id: "stale",
    label: () => "Stale",
    hint: "No commits recently; the branch may have been abandoned.",
    selectedByDefault: false,
    matches: (entry) => entry.issues.some((issue) => issue.code === "stale"),
  },
];

export interface CleanupCandidate {
  entry: BranchTriageEntry;
  categories: CleanupCategoryId[];
  /** Uncommitted work in one of the branch's worktrees — deleting loses it. */
  dirty: boolean;
  worktreeCount: number;
}

/**
 * Every branch this modal is allowed to offer, with the reasons it carries.
 *
 * The base branch and the primary checkout's current branch are dropped: git
 * refuses to delete either, so offering them would only produce failures the
 * operator cannot act on.
 */
export function cleanupCandidates(branches: BranchTriageEntry[]): CleanupCandidate[] {
  return branches
    .filter((entry) => !entry.is_base && !entry.is_current)
    .map((entry) => ({
      entry,
      categories: CLEANUP_CATEGORIES.filter((category) => category.matches(entry)).map(
        (category) => category.id,
      ),
      dirty: entry.dirty,
      worktreeCount: entry.worktrees.length,
    }))
    .sort((a, b) => b.categories.length - a.categories.length || a.entry.name.localeCompare(b.entry.name));
}

/**
 * The selection the modal opens with: branches that are provably merged, minus
 * any with uncommitted work.
 *
 * Dirty branches stay in the list — an operator may well want them gone — but
 * nothing this modal ticks on their behalf may destroy work they never
 * committed. That has to be a deliberate click.
 */
export function defaultCleanupSelection(candidates: CleanupCandidate[]): Set<string> {
  const auto = new Set(
    CLEANUP_CATEGORIES.filter((category) => category.selectedByDefault).map((c) => c.id),
  );
  return new Set(
    candidates
      .filter((item) => !item.dirty && item.categories.some((id) => auto.has(id)))
      .map((item) => item.entry.name),
  );
}

/** The one-line reason shown under a candidate's name. */
export function candidateReason(item: CleanupCandidate, base: string): string {
  const { entry } = item;
  const parts: string[] = [];
  if (entry.pr) {
    const number = entry.pr.number ? `#${entry.pr.number}` : "PR";
    if (entry.pr.state === "merged") parts.push(`${number} merged`);
    else if (entry.pr.state === "closed") parts.push(`${number} closed unmerged`);
    else parts.push(`${number} open`);
  }
  if (entry.squash_merged) parts.push(`squash-merged into ${base}`);
  else if (isMergedIntoBase(entry)) parts.push(`nothing unmerged into ${base}`);
  else parts.push(`${entry.ahead} commit${entry.ahead === 1 ? "" : "s"} not in ${base}`);
  if (item.worktreeCount) {
    parts.push(`${item.worktreeCount} worktree${item.worktreeCount === 1 ? "" : "s"}`);
  }
  return parts.join(" · ");
}
