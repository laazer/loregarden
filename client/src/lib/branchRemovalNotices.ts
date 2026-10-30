import type { BranchRemovalVerdict, BranchTriageLinkedTicket } from "./branchTriageApi";

/** One thing the receipt gate would not delete, ready to render. */
export interface BranchRemovalNotice {
  /** What the operator tried to delete: a branch name, or a worktree path. */
  label: string;
  /** The gate's enum-backed reason key — what rows are grouped on. */
  reason: string;
  /** The gate's own sentence. */
  detail: string;
  /** Set only on a `parked` outcome. */
  parkedPath: string;
  /**
   * The ticket this branch is linked to, when it has one. That is the screen
   * where the operator lands the branch or publishes the tree — the two verbs
   * that write the receipt the refusal is missing.
   */
  ticket: BranchTriageLinkedTicket | null;
}

export function removalNotice(
  label: string,
  verdict: BranchRemovalVerdict,
  ticket: BranchTriageLinkedTicket | null,
): BranchRemovalNotice {
  return {
    label,
    reason: verdict.reason,
    detail: verdict.detail,
    parkedPath: verdict.parked_path,
    ticket,
  };
}

/** Grouped on the reason KEY, in first-seen order — never on the sentence. */
export function groupNoticesByReason(
  notices: BranchRemovalNotice[],
): { reason: string; items: BranchRemovalNotice[] }[] {
  const groups: { reason: string; items: BranchRemovalNotice[] }[] = [];
  const index = new Map<string, { reason: string; items: BranchRemovalNotice[] }>();
  for (const notice of notices) {
    const existing = index.get(notice.reason);
    if (existing) {
      existing.items.push(notice);
      continue;
    }
    const group = { reason: notice.reason, items: [notice] };
    index.set(notice.reason, group);
    groups.push(group);
  }
  return groups;
}

/** `no_receipt` → `No receipt`, for a group heading. */
export function reasonLabel(reason: string): string {
  if (!reason) return "Refused";
  const words = reason.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
