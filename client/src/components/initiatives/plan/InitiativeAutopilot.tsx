import { useState } from "react";

import type { InitiativePlan, PlanNode } from "../../../api/initiativeApi";
import { AUTOPILOT_ACTION_LABEL, AUTOPILOT_PARALLEL_CHOICES, formatWhen } from "../../../lib/scheduleFormat";
import { Button } from "../../ui/Button";
import { Select } from "../../ui/Select";
import { InitiativeNow } from "./InitiativeNow";
import { useInitiativeNow } from "./useInitiativeNow";
import { PlanTicketLink } from "./PlanTicketLink";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

type PilotState = "on" | "off" | "stopped";
const STATE_LABEL: Record<PilotState, string> = { on: "On", off: "Off", stopped: "Stopped itself" };

function stateOf(plan: InitiativePlan): PilotState {
  if (plan.autopilot.paused_reason) return "stopped";
  return plan.autopilot.enabled ? "on" : "off";
}

/** The header's one sentence: what the autopilot is doing, from where the work stands. */
function statusLine(plan: InitiativePlan): string {
  const { autopilot } = plan;
  const held = autopilot.in_flight;
  if (autopilot.enabled) {
    return `${autopilot.in_flight} of ${autopilot.max_parallel} lanes busy. It queues ready work every minute, critical path first.`;
  }
  const byHand = held > 0 ? `${plural(held, "ticket", "tickets")} started by hand ${held === 1 ? "is" : "are"} running. ` : "";
  return `${byHand}Turn it on to queue ready work as prerequisites land — critical path first, never work marked for a person.`;
}

/**
 * Why nothing starts next, in the plan's own numbers. "Nothing is ready" read
 * false beside 54 ready tickets whose only obstacle was a full lane.
 */
function emptyNextReason(plan: InitiativePlan, ready: number): string {
  const { in_flight: busy, max_parallel: cap } = plan.autopilot;
  if (ready > 0 && busy >= cap) {
    return `${cap === 1 ? "Its one lane is" : `All ${cap} lanes are`} busy; ${plural(ready, "ready ticket waits", "ready tickets wait")} for one to free. Raise the cap to run more at once.`;
  }
  if (ready === 0) return "Nothing is ready: every open ticket waits on prerequisites or a person.";
  return "Nothing is ready with a lane free.";
}

/**
 * The initiative's work in motion, and the switch that drives it.
 *
 * Answers "what is this initiative doing right now, what happens next, and
 * what needs me?" in that order, left to right: Now (tickets holding a lane,
 * with the queue's live card where one holds a slot), Up next (what the
 * autopilot would start, or why nothing), and Needs you (work marked for a
 * person, and blocked work). It replaces two sections that answered halves of
 * that question at opposite ends of the page — "Running now" above the
 * schedule and "Autopilot" under a 60-row table at the bottom of one tab.
 *
 * The actions are the switch, the parallel cap (while off, a pick waits for
 * "Turn on", which sends it; while on, it saves at once), opening any ticket,
 * and "Agent can do it" to clear a ticket's mark for a person.
 */
export function InitiativeAutopilot({
  plan,
  busy,
  onAutopilot,
  onNeedsPerson,
}: {
  plan: InitiativePlan;
  busy: boolean;
  onAutopilot: (change: { enabled?: boolean; max_parallel?: number }) => void;
  onNeedsPerson: (ticketIds: string[], needsPerson: boolean) => void;
}) {
  const { autopilot } = plan;
  const now = useInitiativeNow(plan);
  const byId = new Map(plan.nodes.map((n) => [n.id, n]));
  const own = plan.nodes.filter((n) => !n.external);
  const waitingOnYou = own.filter((n) => n.status === "needs_person");
  const blocked = own.filter((n) => n.status === "blocked");
  const ready = own.filter((n) => n.status === "ready").length;
  const nextUp = autopilot.next_up.map((id) => byId.get(id)).filter((n): n is PlanNode => Boolean(n));
  const critical = plan.critical_path.map((id) => byId.get(id)).filter((n): n is PlanNode => Boolean(n));
  const criticalStartsOutside = critical[0]?.external ?? false;
  const nowCount = now.slots.length + now.waiting.length;
  const needsCount = waitingOnYou.length + blocked.length;

  // Off, a picked number waits for "Turn on", which sends it with the switch.
  // Running, a pick saves at once. A draft kept while off and dropped by "Turn
  // on" is what showed 1 while the autopilot ran 2.
  const [draftParallel, setDraftParallel] = useState<number | null>(null);
  const parallel = autopilot.enabled ? autopilot.max_parallel : (draftParallel ?? autopilot.max_parallel);
  const unavailable = autopilot.available ? undefined : "The autopilot does not run on a sandbox server";
  const state = stateOf(plan);

  return (
    <section className="plan-pilot" aria-labelledby="plan-autopilot-title">
      <header className="plan-pilot-head">
        <div className="plan-pilot-heading">
          <h2 id="plan-autopilot-title" className="plan-section-title">
            Autopilot
          </h2>
          <span className={`plan-pilot-state plan-pilot-state--${state}`}>{STATE_LABEL[state]}</span>
        </div>
        <div className="plan-autopilot-controls">
          <Select
            aria-label="Most tickets the autopilot runs at once"
            className="plan-parallel-select"
            value={String(parallel)}
            disabled={busy}
            onChange={(e) => {
              const picked = Number(e.target.value);
              if (autopilot.enabled) onAutopilot({ max_parallel: picked });
              else setDraftParallel(picked);
            }}
          >
            {AUTOPILOT_PARALLEL_CHOICES.map((n) => (
              <option key={n} value={n}>
                {n} at once
              </option>
            ))}
          </Select>
          {autopilot.enabled ? (
            <Button
              variant="secondary"
              compact
              disabled={busy}
              title="Finish what is running, then stop: queued tickets that have not started wait until it is turned back on"
              onClick={() => {
                setDraftParallel(null);
                onAutopilot({ enabled: false });
              }}
            >
              Turn off
            </Button>
          ) : (
            <Button
              variant="primary"
              compact
              disabled={busy || !autopilot.available}
              title={unavailable ?? `Start ready work, up to ${parallel} at once`}
              onClick={() => onAutopilot({ enabled: true, max_parallel: parallel })}
            >
              Turn on
            </Button>
          )}
        </div>
        <p className="plan-pilot-status">{statusLine(plan)}</p>
      </header>

      {autopilot.available ? null : (
        <p className="plan-hint">
          This is a sandbox server: it runs on a copy of the database, so the autopilot is off and no agent runs are
          started here.
        </p>
      )}
      {autopilot.paused_reason ? (
        <p className="plan-alert" role="alert">
          Stopped itself: {autopilot.paused_reason}
        </p>
      ) : null}
      {plan.cyclic.length > 0 ? (
        <p className="plan-alert" role="alert">
          {plan.cyclic.length} tickets wait on each other in a loop and cannot be scheduled:{" "}
          {plan.cyclic.map((id) => byId.get(id)?.external_id ?? id).join(", ")}.
        </p>
      ) : null}

      <div className="plan-pilot-flow">
        <div className="plan-pilot-col" role="group" aria-labelledby="plan-pilot-now">
          <h3 id="plan-pilot-now" className="plan-subtitle">
            Now <span className="count-pill">{nowCount}</span>
          </h3>
          <InitiativeNow now={now} />
        </div>

        <div className="plan-pilot-col" role="group" aria-labelledby="plan-pilot-next">
          <h3 id="plan-pilot-next" className="plan-subtitle">
            Up next <span className="count-pill">{nextUp.length}</span>
          </h3>
          {nextUp.length > 0 ? (
            <>
              <ol className="plan-pilot-list plan-pilot-list--numbered">
                {nextUp.map((node) => (
                  <li key={node.id}>
                    <PlanTicketLink node={node} />
                    <span className="plan-pilot-note">{node.lane}</span>
                  </li>
                ))}
              </ol>
              <p className="plan-muted">
                {autopilot.enabled ? "Starts as lanes free." : "Starts when the autopilot is turned on."}
                {ready > nextUp.length ? ` ${ready - nextUp.length} more ready after these.` : ""}
              </p>
            </>
          ) : (
            <p className="plan-muted">{emptyNextReason(plan, ready)}</p>
          )}
        </div>

        <div className="plan-pilot-col" role="group" aria-labelledby="plan-pilot-needs">
          <h3 id="plan-pilot-needs" className="plan-subtitle">
            Needs you <span className="count-pill">{needsCount}</span>
          </h3>
          {waitingOnYou.length > 0 ? (
            <>
              <p className="plan-pilot-kicker">Waiting on a person ({waitingOnYou.length})</p>
              <ul className="plan-pilot-list">
                {waitingOnYou.map((node) => (
                  <li key={node.id}>
                    <PlanTicketLink node={node} />
                    <Button
                      variant="plain"
                      className="plan-inline-btn"
                      disabled={busy}
                      title="An agent can do this one: let the autopilot start it"
                      onClick={() => onNeedsPerson([node.id], false)}
                    >
                      Agent can do it
                    </Button>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {blocked.length > 0 ? (
            <>
              <p className="plan-pilot-kicker">Blocked ({blocked.length})</p>
              <ul className="plan-pilot-list">
                {blocked.map((node) => (
                  <li key={node.id}>
                    <PlanTicketLink node={node} />
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {needsCount === 0 ? (
            <p className="plan-muted">
              Nothing needs you.
              {autopilot.enabled
                ? ""
                : " Before turning the autopilot on, mark decisions, research and physical work from the Board so it is never handed to an agent."}
            </p>
          ) : null}
        </div>
      </div>

      {criticalStartsOutside ? (
        <p className="plan-hint">
          The critical path starts with work outside this initiative. Nothing started here brings the end date in until{" "}
          {critical[0].external_id} moves.
        </p>
      ) : null}

      {critical.length > 0 || autopilot.recent.length > 0 ? (
        <div className="plan-pilot-foot">
          {critical.length > 0 ? (
            <details className="plan-log">
              <summary>
                Critical path: {plural(critical.length, "ticket", "tickets")}, from {critical[0].external_id} to{" "}
                {critical[critical.length - 1].external_id}
              </summary>
              <ol className="plan-pilot-list plan-pilot-list--numbered plan-pilot-critical">
                {critical.map((node) => (
                  <li key={node.id}>
                    <PlanTicketLink node={node} />
                    {node.external ? <span className="plan-pilot-note">outside</span> : null}
                  </li>
                ))}
              </ol>
            </details>
          ) : null}
          {autopilot.recent.length > 0 ? (
            <details className="plan-log">
              <summary>What it did recently ({autopilot.recent.length})</summary>
              <ul className="plan-list">
                {autopilot.recent.map((event, i) => (
                  <li key={`${event.created_at}-${i}`}>
                    <span className="plan-muted">{formatWhen(event.created_at)}</span>{" "}
                    {AUTOPILOT_ACTION_LABEL[event.action]}
                    {event.ticket_external_id ? <span className="plan-mono"> {event.ticket_external_id}</span> : null}
                    {event.detail ? <span className="plan-muted"> — {event.detail}</span> : null}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
