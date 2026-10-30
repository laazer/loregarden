/**
 * What Home's "Needs you" list holds, and the numbers in Home's header.
 *
 * Pure, so the grouping is tested without a page. Four kinds of row, in the
 * order the operator should take them:
 *
 *   1. Approvals waiting on a person, grouped by what they ask. Fourteen
 *      "Resolve Playtest exit actions" on one ticket were fourteen identical
 *      rows; they are one row, ×14, and reviewing one shows you all of them.
 *   2. Done tickets whose ship is not green, grouped by PR (or carrying
 *      branch when the published ref never reached the remote).
 *   3. Runs that failed today, grouped per ticket and stage, so a stage that
 *      failed four times in a minute is one row saying 4×.
 *   4. Tickets waiting on a person: parked, or blocked on someone.
 *
 * Tickets blocked on other tickets, and in-progress tickets nobody is running,
 * are not here: they do not need *you*, and the header counts them.
 */

import type { Approval } from "../api/chatTypes";
import type { ShipReason, ShipViolation, TicketSummary } from "../api/types";
import type { ChipStatus } from "./statusTone";

/** A run as the run list sends it: only the fields this reads. */
export interface RecentRun {
  id: string;
  ticket_id?: string;
  ticket_title?: string;
  ticket_external_id?: string;
  status: string;
  agent_id: string;
  stage_key: string;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export type NeedsYouRow =
  | {
      kind: "approval";
      key: string;
      title: string;
      chip: ChipStatus;
      count: number;
      /** The one ticket every approval in the group belongs to, or null when they span tickets. */
      ticketId: string | null;
      /** The ticket's id, or "N tickets". */
      where: string;
    }
  | {
      kind: "failed";
      key: string;
      title: string;
      chip: ChipStatus;
      count: number;
      ticketId: string;
      where: string;
    }
  | {
      kind: "person";
      key: string;
      title: string;
      chip: ChipStatus;
      ticketId: string;
      where: string;
    }
  | {
      kind: "ship";
      key: string;
      title: string;
      chip: ChipStatus;
      count: number;
      ticketId: string;
      where: string;
    };

/** What each approval kind asks of the operator, as a chip word. */
const APPROVAL_WORDS: Record<Approval["kind"], string> = {
  workflow_gate: "Gate",
  cli_permission: "Permission",
  cli_question: "Question",
  human_action: "Action",
  rework_pause: "Rework",
  stage_park: "Parked",
  block_decision: "Decision",
};

export function pendingApprovals(approvals: readonly Approval[] | undefined): Approval[] {
  return (approvals ?? []).filter((a) => !a.status || a.status === "pending");
}

/** When a run last did something: finished, else started, else was created. */
export function runTime(run: RecentRun): Date | null {
  const stamp = run.finished_at || run.started_at || run.created_at;
  if (!stamp) return null;
  const at = new Date(stamp);
  return Number.isNaN(at.getTime()) ? null : at;
}

function startOfDay(now: Date): Date {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

/**
 * Today's failures, and whether that count is complete.
 *
 * The run list is the most recent `limit` runs. When even its oldest run is
 * from today, failures before it are unseen, so the count is a floor and says
 * so ("3+") rather than claiming a day it did not read.
 */
export function failedToday(
  runs: readonly RecentRun[],
  now: Date,
  limit: number,
): { runs: RecentRun[]; complete: boolean } {
  const midnight = startOfDay(now);
  const today = (run: RecentRun) => {
    const at = runTime(run);
    return at !== null && at >= midnight;
  };
  const oldest = runs.length ? runs[runs.length - 1] : null;
  const complete = runs.length < limit || oldest === null || !today(oldest);
  return { runs: runs.filter((run) => run.status === "failed" && today(run)), complete };
}

/** Parked, or blocked with a person as the one who can unblock it. */
export function waitsOnPerson(ticket: TicketSummary): boolean {
  return ticket.state === "parked" || ticket.blocked_on === "person";
}

/** What an approval asks, in the words its card uses: its title, else its tool, else its kind. */
export function approvalLabel(approval: Approval): string {
  return approval.title || approval.tool_name || APPROVAL_WORDS[approval.kind] || approval.kind;
}

/**
 * The probe fields a ship row reads. The API's `ShipViolation` is a superset:
 * `ci_run_result` is evidence the ticket's PR tab shows, and no row needs it.
 */
export type ShipRowInput = Pick<
  ShipViolation,
  "ticket_id" | "external_id" | "title" | "reason" | "pr_number" | "check_names" | "carrying_branch"
>;

const SHIP_REASON_RANK: Record<ShipReason, number> = {
  failing: 0,
  pending: 1,
  never_reached_remote: 2,
};

function shipChip(reason: ShipReason): ChipStatus {
  if (reason === "failing") return { tone: "bad", label: "Ship" };
  if (reason === "pending") return { tone: "wait", label: "Ship" };
  return { tone: "you", label: "Ship" };
}

function checkNamesSuffix(names: readonly string[]): string {
  if (names.length === 0) return "";
  const head = names.slice(0, 2).join(", ");
  const extra = names.length > 2 ? ` and ${names.length - 2} more` : "";
  return ` · ${head}${extra}`;
}

function lowestExternal(group: readonly ShipRowInput[]): ShipRowInput {
  return [...group].sort((a, b) => a.external_id.localeCompare(b.external_id))[0];
}

function shipRows(ships: readonly ShipRowInput[]): NeedsYouRow[] {
  const prGroups = new Map<number, ShipRowInput[]>();
  const nrrGroups = new Map<string, ShipRowInput[]>();
  for (const ship of ships) {
    if (ship.reason === "never_reached_remote") {
      nrrGroups.set(ship.carrying_branch, [...(nrrGroups.get(ship.carrying_branch) ?? []), ship]);
      continue;
    }
    if (ship.pr_number == null) continue;
    prGroups.set(ship.pr_number, [...(prGroups.get(ship.pr_number) ?? []), ship]);
  }

  const groups = [
    ...[...prGroups.entries()].map(([pr, members]) => ({
      reason: (lowestExternal(members).reason === "pending" ? "pending" : "failing") as ShipReason,
      prNumber: pr,
      branch: lowestExternal(members).carrying_branch,
      members,
    })),
    ...[...nrrGroups.entries()].map(([branch, members]) => ({
      reason: "never_reached_remote" as const,
      prNumber: null as number | null,
      branch,
      members,
    })),
  ];
  groups.sort(
    (a, b) =>
      SHIP_REASON_RANK[a.reason] - SHIP_REASON_RANK[b.reason] ||
      (a.prNumber ?? Number.POSITIVE_INFINITY) - (b.prNumber ?? Number.POSITIVE_INFINITY) ||
      a.branch.localeCompare(b.branch),
  );

  return groups.map(({ reason, prNumber, branch, members }) => {
    const pick = lowestExternal(members);
    const grouped = members.length > 1;
    if (reason === "never_reached_remote") {
      return {
        kind: "ship" as const,
        key: `ship-nrr-${branch}`,
        title: grouped ? "never reached remote" : pick.title,
        chip: shipChip(reason),
        count: members.length,
        ticketId: pick.ticket_id,
        where: grouped ? `${members.length} tickets` : "never reached remote",
      };
    }
    const pr = prNumber as number;
    return {
      kind: "ship" as const,
      key: `ship-pr-${pr}`,
      title: grouped ? `PR #${pr} ${reason}` : pick.title,
      chip: shipChip(reason),
      count: members.length,
      ticketId: pick.ticket_id,
      where: grouped ? `${members.length} tickets` : `PR #${pr} ${reason}${checkNamesSuffix(pick.check_names)}`,
    };
  });
}

export function needsYouRows(
  approvals: readonly Approval[],
  failures: readonly RecentRun[],
  tickets: readonly TicketSummary[],
  ships: readonly ShipRowInput[] = [],
): NeedsYouRow[] {
  const groups = new Map<string, Approval[]>();
  for (const approval of approvals) {
    const key = approvalLabel(approval);
    groups.set(key, [...(groups.get(key) ?? []), approval]);
  }
  const approvalRows: NeedsYouRow[] = [...groups.entries()]
    .map(([title, group]) => {
      const ticketIds = new Set(group.map((a) => a.ticket_id));
      const single = ticketIds.size === 1 ? group[0] : null;
      return {
        kind: "approval" as const,
        key: `approval-${title}`,
        title,
        chip: { tone: "you" as const, label: APPROVAL_WORDS[group[0].kind] ?? "Approve" },
        count: group.length,
        ticketId: single ? single.ticket_id : null,
        where: single ? single.ticket_external_id || "" : `${ticketIds.size} tickets`,
      };
    })
    // The biggest pile first: it is the one a single review clears most of.
    .sort((a, b) => b.count - a.count || a.title.localeCompare(b.title));

  const failedGroups = new Map<string, RecentRun[]>();
  for (const run of failures) {
    if (!run.ticket_id) continue;
    const key = `${run.ticket_id}:${run.stage_key}`;
    failedGroups.set(key, [...(failedGroups.get(key) ?? []), run]);
  }
  const failedRows: NeedsYouRow[] = [...failedGroups.entries()].map(([key, group]) => {
    const run = group[0];
    return {
      kind: "failed" as const,
      key: `failed-${key}`,
      title: run.ticket_title || run.ticket_external_id || "A run",
      chip: { tone: "bad" as const, label: "Failed" },
      count: group.length,
      ticketId: run.ticket_id as string,
      where: run.stage_key || run.agent_id,
    };
  });

  const personRows: NeedsYouRow[] = tickets.filter(waitsOnPerson).map((ticket) => ({
    kind: "person" as const,
    key: `person-${ticket.id}`,
    title: ticket.title,
    chip: { tone: "you" as const, label: ticket.state === "parked" ? "Parked" : "Person" },
    ticketId: ticket.id,
    where: ticket.external_id,
  }));

  return [...approvalRows, ...shipRows(ships), ...failedRows, ...personRows];
}
