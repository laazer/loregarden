import type { Approval } from "../api/client";

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
