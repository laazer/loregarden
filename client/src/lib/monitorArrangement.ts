import type { MonitorFinding } from "../api/types";
import {
  conditionLabel,
  groupByTicket,
  groupFindings,
  type FindingGroup,
  type FindingLine,
  type TicketFindings,
} from "./monitorFindings";

/**
 * Where each finding goes on the monitor's page. A finding is listed once: a
 * condition on one stage across several tickets is a recurring row, and only
 * what is left over is listed ticket by ticket. Printing both put the live
 * database's 38 over-long tickets on screen twice, the second time as 38 cards
 * of one sentence, around the five tickets with something of their own to say.
 */

export interface RecurringTicket {
  ticket: TicketFindings;
  line: FindingLine;
}

export interface RecurringGroup {
  group: FindingGroup;
  /** The one sentence every ticket here has, or null when theirs differ. */
  sharedSummary: string | null;
  tickets: RecurringTicket[];
}

export interface MonitorArrangement {
  /** The selected ticket, with every line it has. */
  current: TicketFindings | null;
  recurring: RecurringGroup[];
  /** Tickets with findings no recurring group already names. */
  others: TicketFindings[];
  /** One row per condition: forty "timeout floor stale" rows, one per stage, said the same thing forty times. */
  workspace: FindingGroup[];
}

const RECURS_AT = 2;

export function arrangeFindings(findings: MonitorFinding[], ticketId: string | null): MonitorArrangement {
  const ticketFindings = findings.filter((finding) => finding.ticket_id);
  const byTicket = groupByTicket(ticketFindings);
  const groups = groupFindings(ticketFindings).filter((group) => group.tickets >= RECURS_AT);
  const recurringKeys = new Set(groups.map((group) => group.key));
  return {
    current: byTicket.find((ticket) => ticket.ticketId === ticketId) ?? null,
    recurring: groups.map((group) => {
      const tickets = byTicket.flatMap((ticket) => {
        const line = ticket.lines.find(({ key }) => key === group.key);
        return line ? [{ ticket, line }] : [];
      });
      const summaries = new Set(tickets.map(({ line }) => line.summary));
      return { group, sharedSummary: summaries.size === 1 ? tickets[0].line.summary : null, tickets };
    }),
    others: byTicket
      .filter((ticket) => ticket.ticketId !== ticketId)
      .map((ticket) => ({ ...ticket, lines: ticket.lines.filter(({ key }) => !recurringKeys.has(key)) }))
      .filter((ticket) => ticket.lines.length > 0),
    workspace: groupFindings(
      findings.filter((finding) => !finding.ticket_id),
      { byStage: false },
    ),
  };
}

export function isEmptyArrangement(view: MonitorArrangement): boolean {
  return !view.current && !view.recurring.length && !view.others.length && !view.workspace.length;
}

/**
 * The arrangement narrowed to what `query` names. It narrows the arranged page
 * rather than the findings, so typing one ticket's id does not turn a recurring
 * fault into a one-off. A group whose own name matches keeps every ticket.
 */
export function filterArrangement(view: MonitorArrangement, query: string): MonitorArrangement {
  const needle = query.trim().toLowerCase();
  if (!needle) return view;
  const has = (...texts: string[]) => texts.some((text) => text.toLowerCase().includes(needle));
  const names = (condition: string, stageKey: string) => has(conditionLabel(condition), condition, stageKey);
  const isTicket = (ticket: TicketFindings) =>
    has(ticket.externalId, ticket.title, ticket.workspaceSlug, ticket.ticketId);
  const narrow = (ticket: TicketFindings): TicketFindings[] => {
    if (isTicket(ticket)) return [ticket];
    const lines = ticket.lines.filter((line) => names(line.condition, line.stageKey) || has(line.summary, line.detail));
    return lines.length ? [{ ...ticket, lines }] : [];
  };
  return {
    current: view.current ? (narrow(view.current)[0] ?? null) : null,
    recurring: view.recurring.flatMap((row) => {
      if (names(row.group.condition, row.group.stageKey)) return [row];
      const tickets = row.tickets.filter(({ ticket, line }) => isTicket(ticket) || has(line.summary, line.detail));
      return tickets.length ? [{ ...row, tickets }] : [];
    }),
    others: view.others.flatMap(narrow),
    workspace: view.workspace.flatMap((group) => {
      if (names(group.condition, "")) return [group];
      const findings = group.findings.filter((finding) =>
        has(finding.stage_key, finding.summary, finding.workspace_slug),
      );
      return findings.length ? [{ ...group, findings }] : [];
    }),
  };
}
