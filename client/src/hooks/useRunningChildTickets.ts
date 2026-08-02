/**
 * Which queued tickets have a child that is running right now.
 *
 * A parent sitting in a lane is usually waiting on its own children, and the
 * thing you actually want to look at is the child. The queue snapshot cannot
 * answer that on its own — it carries no parent link — so this joins the
 * in-progress tickets against the ids the board reports as running.
 */

import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import type { TicketSummary } from "../api/types";

export interface RunningChildTicket {
  id: string;
  title: string;
  code: string;
}

/**
 * @param parentIds Ticket ids to resolve a running child for.
 * @param runningTicketIds Tickets the queue reports as occupying a slot.
 */
export function useRunningChildTickets(
  parentIds: string[],
  runningTicketIds: string[],
): Map<string, RunningChildTicket> {
  const parentKey = [...new Set(parentIds)].sort().join(",");
  const runningKey = [...new Set(runningTicketIds)].sort().join(",");
  const [inProgress, setInProgress] = useState<TicketSummary[]>([]);

  useEffect(() => {
    if (!parentKey) {
      setInProgress([]);
      return;
    }

    let alive = true;
    api
      .tickets({ state: "in_progress" })
      .then((tickets) => {
        if (alive) setInProgress(tickets);
      })
      .catch(() => {
        // A menu entry is not worth surfacing an error for: without the answer
        // the item simply does not appear.
        if (alive) setInProgress([]);
      });

    return () => {
      alive = false;
    };
    // Refetch when the board's membership changes, not on every snapshot tick.
  }, [parentKey, runningKey]);

  return useMemo(() => {
    const parents = new Set(parentKey ? parentKey.split(",") : []);
    const running = new Set(runningKey ? runningKey.split(",") : []);
    const byParent = new Map<string, RunningChildTicket>();

    for (const ticket of inProgress) {
      const parent = ticket.parent_ticket_id;
      if (!parent || !parents.has(parent) || byParent.has(parent)) continue;
      // Running means occupying a slot, or mid-stage outside the queue.
      if (!running.has(ticket.id) && ticket.workflow_stage_status !== "running") continue;
      byParent.set(parent, {
        id: ticket.id,
        title: ticket.title,
        code: ticket.external_id,
      });
    }

    return byParent;
  }, [inProgress, parentKey, runningKey]);
}
