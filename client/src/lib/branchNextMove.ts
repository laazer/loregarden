import { CLEANUP_CATEGORIES } from "./branchCleanup";
import type { BranchTriageEntry } from "./branchTriageApi";

/**
 * What to do with a branch, as the list's filter offers it.
 *
 * Each is a reason to look, not a state: a merged branch can also be stale and
 * have no ticket, so the views overlap and their counts do not add up to the
 * total. "Merged" uses the cleanup dialog's own two merged reasons, so the list
 * and the dialog never disagree about which branches are merged.
 */
export type NextMove = "all" | "merged" | "ticket_done" | "stale" | "no_ticket";

const MERGED_REASONS = CLEANUP_CATEGORIES.filter((category) =>
  category.id === "pr_merged" || category.id === "merged_into_base",
);

const MATCHES: Record<Exclude<NextMove, "all">, (entry: BranchTriageEntry) => boolean> = {
  // The base and the checked-out branch are never deletable, so never "merged" work to clear.
  merged: (entry) =>
    !entry.is_base && !entry.is_current && MERGED_REASONS.some((category) => category.matches(entry)),
  // Its work is accounted for: every ticket it carries is done, though git may still hold it.
  ticket_done: (entry) =>
    !entry.is_base &&
    !entry.is_current &&
    entry.linked_tickets.length > 0 &&
    entry.linked_tickets.every((ticket) => ticket.state === "done"),
  stale: (entry) => entry.issues.some((issue) => issue.code === "stale"),
  no_ticket: (entry) => !entry.is_base && entry.linked_tickets.length === 0,
};

export const NEXT_MOVES: readonly { id: NextMove; label: string; hint: string }[] = [
  { id: "all", label: "All", hint: "Every branch in the repo" },
  { id: "merged", label: "Merged", hint: "Its work is in the base branch: safe to delete" },
  { id: "ticket_done", label: "Ticket done", hint: "Every ticket on it is done: check it landed, then let it go" },
  { id: "stale", label: "Stale", hint: "No commits for a while: finish it or let it go" },
  { id: "no_ticket", label: "No ticket", hint: "Work no ticket accounts for" },
];

export function matchesNextMove(entry: BranchTriageEntry, move: NextMove): boolean {
  return move === "all" ? true : MATCHES[move](entry);
}

export function nextMoveCounts(branches: readonly BranchTriageEntry[]): Record<NextMove, number> {
  return {
    all: branches.length,
    merged: branches.filter(MATCHES.merged).length,
    ticket_done: branches.filter(MATCHES.ticket_done).length,
    stale: branches.filter(MATCHES.stale).length,
    no_ticket: branches.filter(MATCHES.no_ticket).length,
  };
}

/** Past this many branches sharing a name stem, the family folds into one row. */
export const FAMILY_FOLD_AT = 12;

/** A branch's family: its name without a trailing ticket number ("lg-x-12" → "lg-x"). */
export function branchFamily(name: string): string | null {
  const match = /^(.+)-\d+$/.exec(name);
  return match ? match[1] : null;
}

export type BranchRow<T> = { kind: "branch"; branch: T } | { kind: "family"; key: string; branches: T[] };

/**
 * The table's rows: each branch, except that a family of more than
 * FAMILY_FOLD_AT branches (an agent's ticket branches, say) becomes one row at
 * the place of its first member, so forty "lg-milestone-that-N" rows read as
 * one line with a count.
 */
export function branchRows<T extends { name: string }>(branches: readonly T[]): BranchRow<T>[] {
  const members = new Map<string, T[]>();
  for (const branch of branches) {
    const key = branchFamily(branch.name);
    if (key) members.set(key, [...(members.get(key) ?? []), branch]);
  }
  const rows: BranchRow<T>[] = [];
  const placed = new Set<string>();
  for (const branch of branches) {
    const key = branchFamily(branch.name);
    const family = key ? members.get(key) : undefined;
    if (key && family && family.length > FAMILY_FOLD_AT) {
      if (!placed.has(key)) {
        placed.add(key);
        rows.push({ kind: "family", key, branches: family });
      }
      continue;
    }
    rows.push({ kind: "branch", branch });
  }
  return rows;
}
