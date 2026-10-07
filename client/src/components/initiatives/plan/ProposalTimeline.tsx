import type { InitiativePlan, MilestoneSchedule, ScheduleProposal } from "../../../api/initiativeApi";
import {
  SCHEDULE_STATUS_COLOR,
  formatDay,
  proposedInitiativeTarget,
  proposedSequence,
  timelinePercent,
  timelineRange,
  todayIso,
  type TimelineRange,
} from "../../../lib/scheduleFormat";

function Marks({
  forecast,
  current,
  proposed,
  color,
  range,
}: {
  forecast: string | null;
  current: string | null;
  proposed: string | null;
  color: string;
  range: TimelineRange;
}) {
  // Decorative: every date it draws is in the row's own columns as text.
  return (
    <div className="plan-timeline" aria-hidden>
      {forecast ? (
        <span
          className="plan-timeline-forecast"
          style={{ left: `${timelinePercent(forecast, range)}%`, background: color }}
        />
      ) : null}
      {current && current !== proposed ? (
        <span
          className="plan-timeline-target plan-timeline-target-was"
          style={{ left: `${timelinePercent(current, range)}%` }}
        />
      ) : null}
      {proposed ? (
        <span
          className="plan-timeline-target plan-timeline-target-proposed"
          style={{ left: `${timelinePercent(proposed, range)}%` }}
        />
      ) : null}
    </div>
  );
}

function TargetCell({ current, proposed }: { current: string | null; proposed: string | null }) {
  if (current === proposed) return <td>{formatDay(proposed)}</td>;
  return (
    <td>
      <strong>{formatDay(proposed)}</strong>
      <div className="plan-muted">was {formatDay(current)}</div>
    </td>
  );
}

function OrderCell({ milestone, position }: { milestone: MilestoneSchedule; position: number }) {
  const moved = milestone.plan_order !== position;
  return (
    <td className="plan-order plan-progress">
      #{position + 1}
      {moved ? <div className="plan-muted">was #{milestone.plan_order + 1}</div> : null}
    </td>
  );
}

/**
 * The proposal drawn as the plan it would become: every phase in the order
 * Accept leaves it, its proposed target against its forecast on one axis.
 *
 * Answers "if I accept this, does the plan make sense?" — which a list of
 * changed cells cannot, because it shows neither the phases left alone nor
 * how the dates sit against what the work will actually take.
 */
export function ProposalTimeline({ plan, proposal }: { plan: InitiativePlan; proposal: ScheduleProposal }) {
  const rows = proposedSequence(plan, proposal);
  const initiativeTarget = proposedInitiativeTarget(plan, proposal);
  const range = timelineRange(
    [
      ...plan.milestones,
      ...rows.map((row) => ({ target_date: row.target, forecast_date: null })),
      { target_date: initiativeTarget, forecast_date: plan.forecast_date },
      { target_date: plan.target_date, forecast_date: null },
    ],
    todayIso(new Date(plan.generated_at)),
  );

  return (
    <div className="plan-groups">
      <p className="plan-muted plan-legend">
        <span aria-hidden>
          <span className="plan-legend-target plan-timeline-target-proposed" /> proposed target
        </span>
        <span aria-hidden>
          <span className="plan-legend-target plan-timeline-target-was" /> current target
        </span>
        <span aria-hidden>
          <span className="plan-legend-forecast" /> forecast
        </span>
        <span>timeline to {formatDay(new Date(range.end).toISOString().slice(0, 10))}</span>
      </p>
      <table className="plan-table">
        <caption className="visually-hidden">Proposed schedule in phase order</caption>
        <thead>
          <tr>
            <th scope="col">Order</th>
            <th scope="col">Milestone</th>
            <th scope="col">Target</th>
            <th scope="col">Forecast</th>
            <th scope="col">Timeline</th>
          </tr>
        </thead>
        <tbody>
          <tr className="plan-timeline-total">
            <td />
            <th scope="row">{plan.external_id} (whole initiative)</th>
            <TargetCell current={plan.target_date} proposed={initiativeTarget} />
            <td>{formatDay(plan.forecast_date)}</td>
            <td className="plan-timeline-cell">
              <Marks
                forecast={plan.forecast_date}
                current={plan.target_date}
                proposed={initiativeTarget}
                color={SCHEDULE_STATUS_COLOR[plan.status]}
                range={range}
              />
            </td>
          </tr>
          {rows.map(({ milestone, position, target }) => {
            const done = milestone.status === "done";
            return (
              <tr key={milestone.id} className={done ? "plan-row-done" : undefined}>
                <OrderCell milestone={milestone} position={position} />
                <th scope="row">
                  <span className="plan-mono plan-muted">{milestone.external_id}</span>
                  <div>{milestone.title}</div>
                </th>
                <TargetCell current={milestone.target_date} proposed={target} />
                <td>{done ? "Done" : formatDay(milestone.forecast_date)}</td>
                <td className="plan-timeline-cell">
                  <Marks
                    forecast={done ? null : milestone.forecast_date}
                    current={milestone.target_date}
                    proposed={target}
                    color={SCHEDULE_STATUS_COLOR[milestone.status]}
                    range={range}
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
