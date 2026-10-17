/**
 * How a queue lane's card words its timings, and who it names as the running
 * child. Plain functions, shared by the Queue screen and the initiative page.
 */

import type { RunningChildTicket } from '../hooks/useRunningChildTickets';
import type { TicketHierarchyNode, TicketTreeEstimate } from './queueSocket';

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  // Whole ticket trees run for hours, and "218m 4s" is a number nobody reads.
  if (seconds >= 3600) {
    const hours = Math.floor(seconds / 3600);
    const mins = Math.round((seconds % 3600) / 60);
    return mins ? `${hours}h ${mins}m` : `${hours}h`;
  }
  const minutes = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${minutes}m ${secs}s`;
}

/**
 * "~2h 10m", or nothing at all.
 *
 * The tilde is the point: every one of these is a median projection, and a
 * bare figure reads as a deadline the queue never promised.
 */
export function formatEstimate(seconds: number | null | undefined): string | null {
  if (seconds === null || seconds === undefined) return null;
  if (seconds <= 0) return 'now';
  return `~${formatDuration(seconds)}`;
}

/**
 * What a lane card says about the tree behind it.
 *
 * The count matters as much as the time: "3 tickets" is why the estimate is
 * an hour and not four minutes, and `unknown_tickets` is the admission that
 * part of the subtree has no history to price it — the figure is then a floor,
 * and saying so is the whole difference from making one up.
 */
export function treeSummary(estimate: TicketTreeEstimate | null | undefined): string | null {
  if (!estimate || estimate.ticket_count <= 1) return null;
  const parts = [`${estimate.ticket_count} tickets`];
  if (estimate.stage_count) parts.push(`${estimate.stage_count} stages left`);
  if (estimate.unknown_tickets) parts.push(`${estimate.unknown_tickets} unestimated`);
  return parts.join(' · ');
}

export function runningChildFromLabels(
  descendant: TicketHierarchyNode | null | undefined,
  fallback?: RunningChildTicket,
): RunningChildTicket | undefined {
  if (descendant) {
    return { id: descendant.id, title: descendant.title, code: descendant.code };
  }
  return fallback;
}
