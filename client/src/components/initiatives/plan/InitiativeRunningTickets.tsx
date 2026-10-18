/**
 * The initiative's tickets that hold a queue slot right now.
 *
 * The question it answers is "what of this initiative is being worked on this
 * minute?" — which the schedule cannot say, because a plan node reads
 * `running` for queued work and for a parent whose child is the one moving.
 * The queue's own lanes know which ticket holds a slot, so this draws the
 * Queue screen's running card for each lane whose ticket belongs here, and
 * nothing of the lane's backlog or controls: queuing work is the Queue
 * screen's job, and the initiative's next work is the autopilot's.
 *
 * The data is the app-wide queue subscription, so it is live and costs no
 * request. With nothing of this initiative running, the section is absent.
 */

import { useMemo } from "react";

import type { InitiativePlan } from "../../../api/initiativeApi";
import type { ActiveRun } from "../../../lib/queueSocket";
import { runStatusLabel } from "../../../lib/ticketStates";
import { useQueueStatus } from "../../../state/QueueStatusContext";
import { runningChildFromLabels } from "../../../lib/queueRunFormat";
import { LaneTicketMenu, RunningSlotBody } from "../../QueueRunningSlot";

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

/**
 * A run is the initiative's when the slot holder, its live descendant, or any
 * ancestor of the holder is. Ancestry is what catches a child of a milestone
 * the graph has not listed; the descendant is what catches a lane held by a
 * parent above the initiative while one of its tickets does the work.
 */
function belongsTo(run: ActiveRun, ids: Set<string>): boolean {
  if (ids.has(run.ticket_id)) return true;
  if (run.running_descendant && ids.has(run.running_descendant.id)) return true;
  return (run.ticket_ancestry ?? []).some((node) => ids.has(node.id));
}

export function InitiativeRunningTickets({ plan }: { plan: InitiativePlan }) {
  const { lanes } = useQueueStatus();
  const ids = useMemo(() => initiativeTicketIds(plan), [plan]);
  const running = useMemo(
    () =>
      (lanes ?? []).flatMap((lane) =>
        lane.running && belongsTo(lane.running, ids)
          ? [{ slotNumber: lane.slot_number, run: lane.running }]
          : [],
      ),
    [lanes, ids],
  );

  if (running.length === 0) return null;

  return (
    <section className="plan-running" aria-labelledby="plan-running-title">
      <h2 id="plan-running-title" className="plan-section-title">
        Running now <span className="count-pill">{running.length}</span>
      </h2>
      <ul className="plan-running-grid">
        {running.map(({ slotNumber, run }) => {
          const label =
            run.running_descendant?.code || run.ticket_code || run.ticket_title || run.ticket_id;
          return (
            <li
              key={slotNumber}
              className="queue-slot queue-slot--busy"
              data-testid={`initiative-running-${run.ticket_id}`}
            >
              <div className="queue-slot-head">
                <span className="queue-slot-dot" aria-hidden />
                <span className="queue-slot-name">Slot {slotNumber}</span>
                <span className="queue-slot-badge" data-run-status={run.status}>
                  {runStatusLabel(run.status)}
                </span>
                <LaneTicketMenu
                  label={label}
                  ticketId={run.ticket_id}
                  runningChild={runningChildFromLabels(run.running_descendant)}
                />
              </div>
              <div className="queue-slot-body">
                <RunningSlotBody run={run} slotNumber={slotNumber} />
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
