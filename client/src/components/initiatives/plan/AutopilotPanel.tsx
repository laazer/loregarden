import { useState } from "react";

import type { InitiativePlan, PlanNode } from "../../../api/initiativeApi";
import { navigateToTicket } from "../../../lib/useAppNavigation";
import { AUTOPILOT_ACTION_LABEL, formatWhen } from "../../../lib/scheduleFormat";
import { Button } from "../../ui/Button";
import { Select } from "../../ui/Select";

const PARALLEL_CHOICES = [1, 2, 3, 4, 5, 6, 8, 10, 12];

function TicketLink({ node }: { node: PlanNode }) {
  return (
    <Button
      variant="plain"
      className="plan-link"
      title={`Open ${node.external_id}`}
      onClick={() => navigateToTicket(node.id)}
    >
      <span className="plan-mono">{node.external_id}</span> {node.title}
    </Button>
  );
}

/**
 * Who is driving the plan, and what is waiting on a person.
 *
 * Answers "what is running, what starts next, and what needs me?". The actions
 * are the autopilot switch, the parallel cap (applied with Update, so a
 * mis-pick on the dropdown changes nothing), and — per ticket waiting on a
 * person — opening it, or clearing the mark when an agent can do it after all.
 */
export function AutopilotPanel({
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
  const byId = new Map(plan.nodes.map((n) => [n.id, n]));
  const waitingOnYou = plan.nodes.filter((n) => n.status === "needs_person" && !n.external);
  const blocked = plan.nodes.filter((n) => n.status === "blocked" && !n.external);
  const nextUp = autopilot.next_up.map((id) => byId.get(id)).filter((n): n is PlanNode => Boolean(n));
  const critical = plan.critical_path.map((id) => byId.get(id)).filter((n): n is PlanNode => Boolean(n));
  const criticalStartsOutside = critical[0]?.external ?? false;
  // One ticket per lane at a time, so the lanes with open work cap it too.
  const openLanes = new Set(plan.nodes.filter((n) => !n.external && n.status !== "done").map((n) => n.lane)).size;
  const [draftParallel, setDraftParallel] = useState<number | null>(null);
  const parallel = draftParallel ?? autopilot.max_parallel;
  const parallelChanged = parallel !== autopilot.max_parallel;

  return (
    <section className="plan-autopilot" aria-labelledby="plan-autopilot-title">
      <header className="plan-autopilot-head">
        <div>
          <h2 id="plan-autopilot-title" className="plan-section-title">
            Autopilot
          </h2>
          <p className="plan-muted">
            {autopilot.enabled
              ? `On — ${autopilot.in_flight} of ${autopilot.max_parallel} running. It queues ready work every minute, critical path first, one ticket per lane.`
              : "Off. Turn it on to queue ready work as prerequisites land — critical path first, one ticket per lane, never work marked for a person."}
          </p>
        </div>
        <div className="plan-autopilot-controls">
          <Select
            aria-label="Most tickets the autopilot runs at once"
            className="plan-parallel-select"
            value={String(parallel)}
            disabled={busy}
            onChange={(e) => setDraftParallel(Number(e.target.value))}
          >
            {PARALLEL_CHOICES.map((n) => (
              <option key={n} value={n}>
                {n} at once
              </option>
            ))}
          </Select>
          <Button
            variant="secondary"
            compact
            disabled={busy || !parallelChanged}
            title={parallelChanged ? `Run at most ${parallel} at once` : "Pick a different number first"}
            onClick={() => onAutopilot({ max_parallel: parallel })}
          >
            Update
          </Button>
          <Button
            variant={autopilot.enabled ? "secondary" : "primary"}
            compact
            aria-pressed={autopilot.enabled}
            disabled={busy || !autopilot.available}
            title={autopilot.available ? undefined : "The autopilot does not run on a sandbox server"}
            onClick={() => onAutopilot({ enabled: !autopilot.enabled })}
          >
            {autopilot.enabled ? "Turn off" : "Turn on"}
          </Button>
        </div>
      </header>

      {openLanes > 0 && parallel > openLanes ? (
        <p className="plan-hint">
          Only {openLanes} {openLanes === 1 ? "lane has" : "lanes have"} open work, and it runs one ticket per lane — so
          at most {openLanes} run at once whatever this is set to. Tag tickets <span className="plan-mono">lane-…</span>{" "}
          to split a workspace into more lanes.
        </p>
      ) : null}

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

      {waitingOnYou.length > 0 ? (
        <div>
          <h3 className="plan-subtitle">Waiting on a person ({waitingOnYou.length})</h3>
          <ul className="plan-list">
            {waitingOnYou.map((node) => (
              <li key={node.id}>
                <TicketLink node={node} />
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
        </div>
      ) : (
        <p className="plan-muted">
          Nothing is marked for a person. Before turning the autopilot on, mark decisions, research and
          physical work from the board so it is never handed to an agent.
        </p>
      )}

      {blocked.length > 0 ? (
        <div>
          <h3 className="plan-subtitle">Blocked ({blocked.length})</h3>
          <ul className="plan-list">
            {blocked.map((node) => (
              <li key={node.id}>
                <TicketLink node={node} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div>
        <h3 className="plan-subtitle">Starts next</h3>
        {nextUp.length > 0 ? (
          <ol className="plan-list">
            {nextUp.map((node) => (
              <li key={node.id}>
                <TicketLink node={node} />
                <span className="plan-muted"> · {node.lane}</span>
              </li>
            ))}
          </ol>
        ) : (
          <p className="plan-muted">
            Nothing is ready with a lane free — every open ticket is running, waiting on prerequisites, or waiting on a
            person.
          </p>
        )}
      </div>

      {critical.length > 0 ? (
        <div>
          <h3 className="plan-subtitle">Critical path</h3>
          <p className="plan-critical">
            {critical.map((n) => n.external_id + (n.external ? " (outside)" : "")).join(" → ")}
          </p>
          {criticalStartsOutside ? (
            <p className="plan-hint">
              It starts with work outside this initiative. Nothing started here brings the end date in until{" "}
              {critical[0].external_id} moves.
            </p>
          ) : null}
        </div>
      ) : null}

      {plan.cyclic.length > 0 ? (
        <p className="plan-alert" role="alert">
          {plan.cyclic.length} tickets wait on each other in a loop and cannot be scheduled:{" "}
          {plan.cyclic.map((id) => byId.get(id)?.external_id ?? id).join(", ")}.
        </p>
      ) : null}

      {autopilot.recent.length > 0 ? (
        <details className="plan-log">
          <summary>What it did recently</summary>
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
    </section>
  );
}
