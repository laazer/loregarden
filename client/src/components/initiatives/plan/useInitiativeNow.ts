import { useMemo } from "react";

import type { InitiativePlan, PlanNode } from "../../../api/initiativeApi";
import type { ActiveRun } from "../../../lib/queueSocket";
import { useQueueStatus } from "../../../state/QueueStatusContext";

/**
 * Every ticket id that counts as this initiative's: itself, its milestones
 * (children and tracked members), and every work item in its graph. A
 * prerequisite outside the initiative is in the graph too, and is not its work.
 */
function initiativeTicketIds(plan: InitiativePlan): Set<string> {
  const ids = new Set<string>([plan.id]);
  for (const milestone of plan.milestones) ids.add(milestone.id);
  for (const node of plan.nodes) if (!node.external) ids.add(node.id);
  return ids;
}

/** Every ticket a lane's run stands for: the holder, its live descendant, and the holder's ancestors. */
function runTicketIds(run: ActiveRun): string[] {
  return [
    run.ticket_id,
    ...(run.running_descendant ? [run.running_descendant.id] : []),
    ...(run.ticket_ancestry ?? []).map((node) => node.id),
  ];
}

/**
 * A run is the initiative's when the slot holder, its live descendant, or any
 * ancestor of the holder is. Ancestry is what catches a child of a milestone
 * the graph has not listed; the descendant is what catches a lane held by a
 * parent above the initiative while one of its tickets does the work.
 */
function belongsTo(run: ActiveRun, ids: Set<string>): boolean {
  return runTicketIds(run).some((id) => ids.has(id));
}

export interface InitiativeNowState {
  slots: { slotNumber: number; run: ActiveRun }[];
  /** Tickets holding a lane that no slot's run stands for: queued, or parked at a gate. */
  waiting: PlanNode[];
}

export function useInitiativeNow(plan: InitiativePlan): InitiativeNowState {
  const { lanes } = useQueueStatus();
  const ids = useMemo(() => initiativeTicketIds(plan), [plan]);
  return useMemo(() => {
    const slots = (lanes ?? []).flatMap((lane) =>
      lane.running && belongsTo(lane.running, ids) ? [{ slotNumber: lane.slot_number, run: lane.running }] : [],
    );
    const covered = new Set(slots.flatMap(({ run }) => runTicketIds(run)));
    const waiting = plan.nodes.filter((node) => node.status === "running" && !node.external && !covered.has(node.id));
    return { slots, waiting };
  }, [lanes, ids, plan.nodes]);
}
