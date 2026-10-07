/**
 * The tickets holding execution slots right now, one entry each.
 *
 * Read from the queue's `activeRuns`, which `QueueStatusProvider` subscribes to
 * once for the whole app. A lane holds its slot across every stage, so the same
 * ticket can appear behind more than one run row; the longest-running row is
 * the one that describes the slot.
 */

import type { ActiveRun } from "./queueSocket";

/** Statuses where an agent is still on the work, matching the queue's cards. */
export const LIVE_RUN_STATUSES = new Set(["running", "awaiting_permission"]);

export function runningTickets(activeRuns: ActiveRun[]): ActiveRun[] {
  const byTicket = new Map<string, ActiveRun>();
  for (const run of activeRuns) {
    if (!run.ticket_id) continue;
    const seen = byTicket.get(run.ticket_id);
    if (!seen || run.elapsed_seconds > seen.elapsed_seconds) byTicket.set(run.ticket_id, run);
  }
  return [...byTicket.values()].sort((a, b) => a.slot_number - b.slot_number);
}
