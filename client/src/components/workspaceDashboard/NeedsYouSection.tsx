import type { UseQueryResult } from "@tanstack/react-query";

import type { Approval } from "../../api/chatTypes";
import type { BlockedOn, TicketSummary, TicketTreeNode } from "../../api/types";
import type { WorkspacePullRequests } from "../../api/workspaceDashboardApi";
import { useUiStore } from "../../state/uiStore";
import { Button } from "../ui/Button";
import { DashboardSection } from "./DashboardSection";
import { MiniTicketGrid } from "./MiniTicketCard";

interface ApprovalGroup {
  key: string;
  title: string;
  kind: Approval["kind"];
  stageName: string;
  approvals: Approval[];
}

/**
 * One row per kind of ask, naming the tickets it is on. Blobert, measured
 * 2026-10-17: 14 pending, every one "Resolve Playtest exit actions" on its own
 * ticket. As 14 rows that is one sentence repeated; as one row it says what
 * the job is and how many tickets it covers.
 */
function groupApprovals(approvals: Approval[]): ApprovalGroup[] {
  const groups = new Map<string, ApprovalGroup>();
  for (const approval of approvals) {
    const key = `${approval.kind}\u0000${approval.title}\u0000${approval.stage_name}`;
    const group = groups.get(key) ?? {
      key,
      title: approval.title,
      kind: approval.kind,
      stageName: approval.stage_name,
      approvals: [],
    };
    group.approvals.push(approval);
    groups.set(key, group);
  }
  return [...groups.values()].sort((a, b) => b.approvals.length - a.approvals.length);
}

const APPROVAL_KIND_LABELS: Record<Approval["kind"], string> = {
  workflow_gate: "stage sign-off",
  cli_permission: "tool permission",
  cli_question: "agent question",
  human_action: "action for a person",
  rework_pause: "rework pause",
  stage_park: "parked stage",
  block_decision: "decision",
};

/** Who has to act, most urgent first: a person, then a stopped agent, then other tickets. */
const BLOCK_ORDER: (BlockedOn | "parked" | "unknown")[] = ["person", "parked", "agent", "tickets", "unknown"];

const BLOCK_LABELS: Record<(typeof BLOCK_ORDER)[number], string> = {
  person: "Needs a person",
  parked: "Parked for a person",
  agent: "Agent stopped",
  tickets: "Waiting on other tickets",
  unknown: "Blocked",
};

function blockGroup(ticket: TicketSummary): (typeof BLOCK_ORDER)[number] {
  if (ticket.state === "parked") return "parked";
  return ticket.blocked_on ?? "unknown";
}

function waitingLine(ticket: TicketSummary): string {
  const waits = ticket.waiting_on ?? [];
  if (waits.length === 0) return "";
  const first = waits[0].title || waits[0].external_id;
  return waits.length > 1 ? `${first} +${waits.length - 1}` : first;
}

interface NeedsYouSectionProps {
  /** Every ticket in the workspace by id: approvals carry only an id, and a card wants the title. */
  ticketIndex: Map<string, TicketTreeNode>;
  /** Pending approvals in this workspace (the inbox's own query, filtered). */
  approvals: UseQueryResult<Approval[]>;
  stuck: UseQueryResult<TicketSummary[]>;
  pullRequests: UseQueryResult<WorkspacePullRequests>;
}

/**
 * "What can only I unblock, right now?"
 *
 * Pending approvals one line per ticket, blocked and parked tickets grouped by
 * who has to act, and the PRs that cannot merge until someone fixes them. A PR
 * read that failed is reported by the Pull requests section, not here twice.
 *
 * Approvals are listed, not resolved, here: the inbox's cards run to a screen
 * each, and 14 of them buried the rest of this section on real data. Each row
 * opens its ticket's Approvals tab; "Open inbox" resolves them in bulk.
 */
export function NeedsYouSection({ ticketIndex, approvals, stuck, pullRequests }: NeedsYouSectionProps) {
  const setInboxOpen = useUiStore((s) => s.setInboxOpen);
  const pending = approvals.data ?? [];
  const approvalGroups = groupApprovals(pending);
  const approvalCount = pending.length;
  const tickets = [...(stuck.data ?? [])].sort(
    (a, b) => BLOCK_ORDER.indexOf(blockGroup(a)) - BLOCK_ORDER.indexOf(blockGroup(b)) || b.priority - a.priority,
  );
  const brokenPrs = (pullRequests.data?.pull_requests ?? []).filter(
    (row) => row.tier === "conflicting" || row.tier === "needs_work",
  );
  const total = approvalCount + tickets.length + brokenPrs.length;

  return (
    <DashboardSection
      id="wsd-needs-you"
      title="Needs you now"
      count={String(total)}
      queries={[approvals, stuck]}
      errorTitle="Could not load what needs you"
      empty={total === 0}
      emptyMessage="Nothing is waiting on you: no approvals, no blocked tickets, no pull requests that cannot merge."
      actions={
        approvalCount > 0 ? (
          <Button variant="secondary" compact onClick={() => setInboxOpen(true)}>
            Open inbox
          </Button>
        ) : null
      }
    >
      {approvalCount > 0 ? (
        <div className="wsd-group">
          <h3 className="wsd-group-title">Approvals</h3>
          <ul className="wsd-rows">
            {approvalGroups.map((group) => (
              <li key={group.key} className="wsd-row wsd-row--stack">
                <div className="wsd-row-line">
                  <span className="wsd-row-title">{group.title}</span>
                  <span className="wsd-row-aside">
                    {APPROVAL_KIND_LABELS[group.kind]}
                    {group.stageName ? ` at ${group.stageName}` : ""}
                  </span>
                </div>
                <MiniTicketGrid
                  label={`${group.title}: ${group.approvals.length} tickets`}
                  tab="approvals"
                  tickets={group.approvals.map((approval) => {
                    const node = ticketIndex.get(approval.ticket_id);
                    return {
                      id: approval.ticket_id || approval.id,
                      externalId: approval.ticket_external_id || "workspace",
                      title: node?.title ?? "",
                      state: node?.state ?? (approval.ticket_state || undefined),
                      where: node?.workflow_stage_name,
                    };
                  })}
                />
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {tickets.length > 0 ? (
        <div className="wsd-group">
          <h3 className="wsd-group-title">Blocked or parked</h3>
          <MiniTicketGrid
            label="Blocked or parked tickets"
            initial={9}
            tickets={tickets.map((ticket) => {
              const waiting = waitingLine(ticket);
              const group = blockGroup(ticket);
              return {
                id: ticket.id,
                externalId: ticket.external_id,
                title: ticket.title,
                state: ticket.state,
                where: waiting ? `on ${waiting}` : ticket.workflow_stage_name ? `at ${ticket.workflow_stage_name}` : "",
                tag: <span className={`wsd-tag wsd-tag--block-${group}`}>{BLOCK_LABELS[group]}</span>,
              };
            })}
          />
        </div>
      ) : null}

      {brokenPrs.length > 0 ? (
        <div className="wsd-group">
          <h3 className="wsd-group-title">Pull requests that cannot merge</h3>
          <ul className="wsd-rows">
            {brokenPrs.map((row) => (
              <li key={row.pull_request.number} className="wsd-row">
                <a href={row.pull_request.url} target="_blank" rel="noreferrer" className="wsd-row-link">
                  <span className="wsd-id">#{row.pull_request.number}</span>{" "}
                  <span className="wsd-row-title">{row.pull_request.title}</span>
                </a>
                <span className="wsd-row-aside">
                  <span className={`wsd-tag wsd-tag--${row.tier}`}>
                    {row.tier === "conflicting"
                      ? `Conflicts with ${row.pull_request.base}`
                      : row.ci === "failing"
                        ? "Check failing"
                        : "Changes requested"}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </DashboardSection>
  );
}
