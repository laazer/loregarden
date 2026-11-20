import type { Approval } from "../api/client";
import { hasHumanCriteria } from "../utils/approvalCriteria";

/**
 * Combine approval lists from several sources, first occurrence winning.
 *
 * The triage snapshot and the ticket approvals query overlap: an approval
 * raised mid-run appears in both, and rendering it twice reads as two separate
 * things needing a decision.
 */
export function mergeApprovals(...lists: Array<Approval[] | undefined>): Approval[] {
  const seen = new Set<string>();
  const merged: Approval[] = [];
  for (const list of lists) {
    for (const item of list ?? []) {
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      merged.push(item);
    }
  }
  return merged;
}

/**
 * Whether an approval belongs in a list narrowed to `workspaceSlug`.
 *
 * `""` means every workspace. The slug is compared as stored — no trimming,
 * no case folding — because the server already normalized it, and softening
 * the comparison here would make a bad filter look like an empty workspace.
 */
export function inWorkspaceScope(approval: Approval, workspaceSlug: string): boolean {
  return workspaceSlug === "" || approval.workspace_slug === workspaceSlug;
}

/** One workspace's pending approvals, in the order they should be rendered. */
export interface ApprovalGroup {
  workspaceSlug: string;
  approvals: Approval[];
}

/**
 * The scoped approvals, gathered under one group per workspace.
 *
 * Two orderings, and only one of them is a choice:
 *
 * - **Inside a group**, gates come first and API order survives within each
 *   rank. That is the drawer's existing rule (lg-workflow-integrity-107), and
 *   `Array.prototype.sort` is stable per ES2019, so ranking alone preserves it.
 * - **Between groups**, first appearance in the *sorted* list. Walking the
 *   already-ranked array into a `Map` is the whole mechanism: the workspace
 *   whose most urgent card is most urgent leads, and two gate-led groups fall
 *   back to which gate the API returned first. A group can therefore change
 *   position on a poll, which is the accepted trade against burying a gate
 *   that just arrived.
 *
 * The caller's array is never mutated (the filter already copies), a group is
 * never emitted empty, and the approvals are the caller's own objects by
 * reference — a group is a view, not a copy.
 */
export function groupApprovalsByWorkspace(
  approvals: Approval[],
  workspaceSlug: string,
): ApprovalGroup[] {
  const scoped = approvals.filter((approval) => inWorkspaceScope(approval, workspaceSlug));
  scoped.sort((a, b) => Number(hasHumanCriteria(b)) - Number(hasHumanCriteria(a)));

  const byWorkspace = new Map<string, Approval[]>();
  for (const approval of scoped) {
    const existing = byWorkspace.get(approval.workspace_slug);
    if (existing) existing.push(approval);
    else byWorkspace.set(approval.workspace_slug, [approval]);
  }
  return Array.from(byWorkspace, ([slug, grouped]) => ({
    workspaceSlug: slug,
    approvals: grouped,
  }));
}
