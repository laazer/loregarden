import type { InitiativePlan, MilestoneSchedule } from "../../../api/initiativeApi";
import { navigateToTicket } from "../../../lib/useAppNavigation";
import {
  SCHEDULE_STATUS_COLOR,
  formatDay,
  formatDrift,
  forecastExplanation,
  groupByWorkspace,
  timelinePercent,
  timelineRange,
  todayIso,
  type TimelineRange,
} from "../../../lib/scheduleFormat";
import { Button } from "../../ui/Button";
import { StatusChip } from "./ScheduleSummary";
import { TargetDateField } from "./TargetDateField";

function TimelineBar({ row, range }: { row: MilestoneSchedule; range: TimelineRange }) {
  // Decorative: every date it draws is in the row's own columns as text.
  return (
    <div className="plan-timeline" aria-hidden>
      {row.earliest_date ? (
        <span
          className="plan-timeline-floor"
          style={{ left: `${timelinePercent(row.earliest_date, range)}%` }}
        />
      ) : null}
      {row.forecast_date ? (
        <span
          className="plan-timeline-forecast"
          style={{
            left: `${timelinePercent(row.forecast_date, range)}%`,
            background: SCHEDULE_STATUS_COLOR[row.status],
          }}
        />
      ) : null}
      {row.target_date ? (
        <span
          className="plan-timeline-target"
          style={{ left: `${timelinePercent(row.target_date, range)}%` }}
        />
      ) : null}
    </div>
  );
}

function MilestoneRow({
  row,
  range,
  first,
  last,
  busy,
  onMove,
  onTarget,
}: {
  row: MilestoneSchedule;
  range: TimelineRange;
  first: boolean;
  last: boolean;
  busy: boolean;
  onMove: (direction: -1 | 1) => void;
  onTarget: (date: string | null) => void;
}) {
  const done = row.status === "done";
  const drift = formatDrift(row.drift_days);
  return (
    <tr className={done ? "plan-row-done" : undefined}>
      <td className="plan-order">
        <Button
          variant="plain"
          className="plan-icon-btn"
          aria-label={`Move ${row.title} earlier`}
          disabled={busy || first}
          onClick={() => onMove(-1)}
        >
          ↑
        </Button>
        <Button
          variant="plain"
          className="plan-icon-btn"
          aria-label={`Move ${row.title} later`}
          disabled={busy || last}
          onClick={() => onMove(1)}
        >
          ↓
        </Button>
      </td>
      <th scope="row">
        <Button
          variant="plain"
          className="plan-link"
          title={`Open ${row.external_id}`}
          onClick={() => navigateToTicket(row.id)}
        >
          <span className="plan-mono">{row.external_id}</span> {row.title}
        </Button>
      </th>
      <td className="plan-progress">
        {row.total - row.remaining}/{row.total}
      </td>
      <td>
        {done ? (
          formatDay(row.target_date)
        ) : (
          <TargetDateField
            value={row.target_date}
            label={`Target date for ${row.title}`}
            disabled={busy}
            onCommit={onTarget}
          />
        )}
      </td>
      <td title={forecastExplanation(row)}>
        {done ? "—" : row.forecast_date ? formatDay(row.forecast_date) : "Unknown"}
        {drift && !done ? <div className="plan-muted">{drift}</div> : null}
      </td>
      <td>
        <StatusChip status={row.status} />
      </td>
      <td className="plan-timeline-cell">
        <TimelineBar row={row} range={range} />
      </td>
    </tr>
  );
}

/**
 * Every milestone with its target, forecast and standing, one table per
 * workspace — milestones in a workspace are forecast one after another, so
 * reordering them is a planning move, and ↑/↓ make it.
 */
export function ScheduleTable({
  plan,
  busy,
  onReorder,
  onTarget,
}: {
  plan: InitiativePlan;
  busy: boolean;
  /** The workspace's full new order, earliest first. */
  onReorder: (ordered: MilestoneSchedule[]) => void;
  onTarget: (row: MilestoneSchedule, date: string | null) => void;
}) {
  if (plan.milestones.length === 0) {
    return (
      <div className="plan-empty">
        <p>
          This initiative has no milestones yet. Attach milestones from the Initiatives list — each one gets a target
          here, and its forecast follows from how fast its work is closing.
        </p>
      </div>
    );
  }

  const range = timelineRange(plan.milestones, todayIso(new Date(plan.generated_at)));
  return (
    <div className="plan-groups">
      <p className="plan-muted plan-legend" aria-hidden>
        <span className="plan-legend-target" /> target <span className="plan-legend-forecast" /> forecast{" "}
        <span className="plan-legend-floor" /> earliest possible · timeline from today to{" "}
        {formatDay(new Date(range.end).toISOString().slice(0, 10))}
      </p>
      {groupByWorkspace(plan.milestones).map(([workspace, rows]) => (
        <table key={workspace} className="plan-table">
          <caption>{workspace}</caption>
          <thead>
            <tr>
              <th scope="col">
                <span className="visually-hidden">Order</span>
              </th>
              <th scope="col">Milestone</th>
              <th scope="col">Done</th>
              <th scope="col">Target</th>
              <th scope="col">Forecast</th>
              <th scope="col">Status</th>
              <th scope="col">Timeline</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <MilestoneRow
                key={row.id}
                row={row}
                range={range}
                first={index === 0}
                last={index === rows.length - 1}
                busy={busy}
                onTarget={(date) => onTarget(row, date)}
                onMove={(direction) => {
                  const next = [...rows];
                  const [moved] = next.splice(index, 1);
                  next.splice(index + direction, 0, moved);
                  onReorder(next);
                }}
              />
            ))}
          </tbody>
        </table>
      ))}
    </div>
  );
}
