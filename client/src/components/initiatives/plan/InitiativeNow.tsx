/**
 * What of the initiative holds a lane this minute: the autopilot panel's "Now".
 *
 * Two sources, because neither alone is the answer. The queue's lanes know
 * which ticket holds a slot and what its agent is doing, so a ticket with a
 * slot gets the Queue screen's running card. The plan knows which tickets hold
 * a lane at all (`status: running`): running, queued, or parked at a gate. A
 * ticket of the second kind with no slot used to be counted "1 in flight" by
 * the autopilot while "Running now" showed nothing, so it is listed too.
 *
 * The data is the app-wide queue subscription plus the plan already loaded,
 * so it is live and costs no request (`useInitiativeNow`).
 */

import { runningChildFromLabels } from "../../../lib/queueRunFormat";
import { runStatusLabel } from "../../../lib/ticketStates";
import { LaneTicketMenu, RunningSlotBody } from "../../QueueRunningSlot";
import { PlanTicketLink } from "./PlanTicketLink";
import type { InitiativeNowState } from "./useInitiativeNow";

/** The "Now" column's body; the caller owns the heading and the count. */
export function InitiativeNow({ now }: { now: InitiativeNowState }) {
  if (now.slots.length === 0 && now.waiting.length === 0) {
    return <p className="plan-muted">Nothing of this initiative is running or queued.</p>;
  }
  return (
    <>
      {now.slots.length > 0 ? (
        <ul className="plan-running-grid">
          {now.slots.map(({ slotNumber, run }) => {
            const label = run.running_descendant?.code || run.ticket_code || run.ticket_title || run.ticket_id;
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
      ) : null}
      {now.waiting.length > 0 ? (
        <ul className="plan-pilot-list" aria-label="Holding a lane without a slot">
          {now.waiting.map((node) => (
            <li key={node.id}>
              <PlanTicketLink node={node} />
              <span className="plan-pilot-note">Queued or waiting at a gate</span>
            </li>
          ))}
        </ul>
      ) : null}
    </>
  );
}
