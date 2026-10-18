/**
 * The workspace dashboard's pure readings of its data: labels, the PR
 * headline, staleness, highlight wording, milestone progress. Kept out of the
 * section components so each is testable without rendering.
 */

import type { TicketState, TicketTreeNode } from "../api/types";
import type {
  ActivityHighlight,
  ActivityHighlightKind,
  PullRequestReviewTier,
  WorkspacePullRequest,
} from "../api/workspaceDashboardApi";

export const TIER_LABELS: Record<PullRequestReviewTier, string> = {
  ready_to_merge: "Ready to merge",
  to_review: "To review",
  needs_work: "Needs work",
  conflicting: "Needs rebase",
  draft: "Draft",
};

/** A PR untouched this long reads as stale: it is drifting from its base. */
const STALE_DAYS = 7;
const DAY_MS = 24 * 60 * 60 * 1000;

export function isStale(updatedAt: string, now: Date): boolean {
  return now.getTime() - new Date(updatedAt).getTime() > STALE_DAYS * DAY_MS;
}

/**
 * The sentence over the PR table: what is wrong with the open PRs as a set.
 * The header already says how many are open and approved, so this does not.
 * "All 8 are idle over 7 days; 4 need a rebase and 3 have no checks."
 */
export function pullRequestHeadline(rows: WorkspacePullRequest[], now: Date): string {
  if (rows.length === 0) return "";
  const stale = rows.filter((row) => isStale(row.updated_at, now)).length;
  const conflicting = rows.filter((row) => row.tier === "conflicting").length;
  const unchecked = rows.filter((row) => row.pull_request.checks.length === 0).length;
  const idle =
    stale === 0
      ? ""
      : stale === rows.length
        ? `${rows.length === 1 ? "It has" : `All ${rows.length} have`} been idle over ${STALE_DAYS} days`
        : `${stale} ${stale === 1 ? "has" : "have"} been idle over ${STALE_DAYS} days`;
  const problems = [
    conflicting ? `${conflicting} ${conflicting === 1 ? "needs" : "need"} a rebase` : "",
    unchecked ? `${unchecked} ${unchecked === 1 ? "has" : "have"} no checks` : "",
  ].filter(Boolean);
  const tail = problems.join(" and ");
  if (!idle && !tail) return "Every open PR is current and checked; review them in this order.";
  const sentence = [idle, tail].filter(Boolean).join("; ");
  return `${sentence.charAt(0).toUpperCase()}${sentence.slice(1)}.`;
}

export const KIND_LABELS: Record<ActivityHighlightKind, string> = {
  created: "Created",
  moved: "Moved",
  landed: "Landed",
  landing_failed: "Landing failed",
  stage_rejected: "Stage rejected",
  run_failed: "Run failed",
  gate_failed: "Gate failed",
  approval_requested: "Approval asked",
};

/** "Landed on main ×10", "Moved to done", "Run failed ×2". */
export function highlightText(highlight: ActivityHighlight): string {
  const times = highlight.count > 1 ? ` ×${highlight.count}` : "";
  if (!highlight.detail) return `${KIND_LABELS[highlight.kind]}${times}`;
  switch (highlight.kind) {
    case "moved":
      return `Moved to ${highlight.detail.replaceAll("_", " ")}${times}`;
    case "landed":
    case "landing_failed":
      return `${KIND_LABELS[highlight.kind]} on ${highlight.detail}${times}`;
    default:
      return `${KIND_LABELS[highlight.kind]} at ${highlight.detail}${times}`;
  }
}

export const FINISHED: ReadonlySet<TicketState> = new Set(["done", "wont_do"]);

export interface MilestoneProgress {
  total: number;
  finished: number;
  inProgress: number;
  blocked: number;
}

/** Every ticket below ``node``, counted once each. Member-link copies point at
 * a row that is counted under its real parent, so they are skipped. */
export function milestoneProgress(node: TicketTreeNode): MilestoneProgress {
  const progress: MilestoneProgress = { total: 0, finished: 0, inProgress: 0, blocked: 0 };
  const walk = (children: TicketTreeNode[]) => {
    for (const child of children) {
      if (child.member_link) continue;
      progress.total += 1;
      if (FINISHED.has(child.state)) progress.finished += 1;
      else if (child.state === "in_progress") progress.inProgress += 1;
      else if (child.state === "blocked" || child.state === "parked") progress.blocked += 1;
      walk(child.children);
    }
  };
  walk(node.children);
  return progress;
}


/**
 * Every milestone in a workspace's ticket tree, at any depth (one may sit
 * under an initiative). Read from the unfiltered tree: filtering the tree to
 * milestones drops their children, and with them every progress count.
 */
export function collectMilestones(roots: TicketTreeNode[]): TicketTreeNode[] {
  const found: TicketTreeNode[] = [];
  const walk = (nodes: TicketTreeNode[]) => {
    for (const node of nodes) {
      if (node.member_link) continue;
      if (node.work_item_type === "milestone") found.push(node);
      walk(node.children);
    }
  };
  walk(roots);
  return found;
}

interface Standing {
  approvals: number | undefined;
  blocked: number | undefined;
  pullRequests: WorkspacePullRequest[] | undefined;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/**
 * The header's one sentence: what is waiting on a person, then where the PRs
 * stand. "14 approvals and 11 blocked tickets are waiting on you. 8 pull
 * requests are open and none is approved." Parts still loading are left out.
 */
export function standingSentence({ approvals, blocked, pullRequests }: Standing): string {
  const waiting = [
    approvals ? plural(approvals, "approval", "approvals") : "",
    blocked ? plural(blocked, "blocked ticket", "blocked tickets") : "",
  ].filter(Boolean);
  const first =
    approvals === undefined || blocked === undefined
      ? ""
      : waiting.length === 0
        ? "Nothing is waiting on you."
        : `${waiting.join(" and ")} ${approvals + blocked === 1 ? "is" : "are"} waiting on you.`;

  let second = "";
  if (pullRequests) {
    const open = pullRequests.length;
    const approved = pullRequests.filter((row) => row.tier === "ready_to_merge").length;
    if (open === 0) second = "No pull requests are open.";
    else {
      const verb = open === 1 ? "is" : "are";
      const tail = approved === 0 ? (open === 1 ? "it is not approved" : "none is approved") : `${approved} approved`;
      second = `${plural(open, "pull request", "pull requests")} ${verb} open and ${tail}.`;
    }
  }
  return [first, second].filter(Boolean).join(" ");
}

/** Every ticket in a tree by id, so a card can show a title where only an id was known. */
export function indexTree(roots: TicketTreeNode[]): Map<string, TicketTreeNode> {
  const index = new Map<string, TicketTreeNode>();
  const walk = (nodes: TicketTreeNode[]) => {
    for (const node of nodes) {
      if (!node.member_link) index.set(node.id, node);
      walk(node.children);
    }
  };
  walk(roots);
  return index;
}
