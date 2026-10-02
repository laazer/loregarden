import type { InitiativePlan, MilestoneSchedule, NodeStatus } from "../../../api/initiativeApi";
import { navigateToTicket } from "../../../lib/useAppNavigation";
import {
  SCHEDULE_STATUS_COLOR,
  formatDay,
  formatDrift,
  forecastExplanation,
  hasTimelineDates,
  timelinePercent,
  timelineRange,
  todayIso,
  type TimelineRange,
} from "../../../lib/scheduleFormat";
import { Button } from "../../ui/Button";
import { StatusChip } from "./ScheduleSummary";
import { TargetDateField } from "./TargetDateField";

const COUNT_LABEL: [NodeStatus, string][] = [
  ["running", "running"],
  ["ready", "ready"],
  ["needs_person", "for a person"],
  ["blocked", "blocked"],
  ["waiting", "waiting"],
];

function TimelineBar({ row, range }: { row: MilestoneSchedule; range: TimelineRange }) {
  // Decorative: every date it draws is in the row's own columns as text.
  return (
    <div className="plan-timeline" aria-hidden>
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
        <span className="plan-timeline-target" style={{ left: `${timelinePercent(row.target_date, range)}%` }} />
      ) : null}
    </div>
  );
}

function workSummary(row: MilestoneSchedule): string {
  const parts = COUNT_LABEL.flatMap(([status, label]) =>
    row.counts[status] ? [`${row.counts[status]} ${label}`] : [],
  );
  return parts.join(" · ") || (row.status === "done" ? "all done" : "nothing open");
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
  range: TimelineRange | null;
  first: boolean;
  last: boolean;
  busy: boolean;
  onMove: (direction: -1 | 1) => void;
  onTarget: (date: string | null) => Promise<unknown>;
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
        <div className="plan-muted">
          {row.workspace_slug} · {workSummary(row)}
        </div>
      </th>
      <td className="plan-progress">
        {row.total - row.remaining}/{row.total}
      </td>
      <td>
        {done ? (
          formatDay(row.target_date)
        ) : (
          <TargetDateField value={row.target_date} label={`Target date for ${row.title}`} onCommit={onTarget} />
        )}
      </td>
      <td title={forecastExplanation(row)}>
        {done ? "—" : row.forecast_date ? formatDay(row.forecast_date) : "Unknown"}
        {row.assumed > 0 && !done ? (
          <span className="plan-assumed" aria-label={`${row.assumed} items estimated without a measurement`}>
            {" "}
            ~
          </span>
        ) : null}
        {drift && !done ? <div className="plan-muted">{drift}</div> : null}
      </td>
      <td>
        <StatusChip status={row.status} />
      </td>
      {range ? (
        <td className="plan-timeline-cell">
          <TimelineBar row={row} range={range} />
        </td>
      ) : null}
    </tr>
  );
}

/**
 * Every milestone (phase) with its target, forecast and the state of its work,
 * in the order phases get free lanes. Reordering is a planning move: an earlier
 * phase's ready work is started first, and ↑/↓ make it.
 */
export function ScheduleTable({
  plan,
  busy,
  onReorder,
  onTarget,
}: {
  plan: InitiativePlan;
  busy: boolean;
  /** The full new order, first phase first. */
  onReorder: (ordered: MilestoneSchedule[]) => void;
  onTarget: (row: MilestoneSchedule, date: string | null) => Promise<unknown>;
}) {
  const rows = plan.milestones;
  if (rows.length === 0) {
    return (
      <div className="plan-empty">
        <p>
          This initiative has no milestones yet. Attach milestones from the Initiatives list — each one becomes a phase
          here, and its forecast follows from its tickets and how fast work is closing.
        </p>
      </div>
    );
  }

  const range = hasTimelineDates(rows) ? timelineRange(rows, todayIso(new Date(plan.generated_at))) : null;
  const assumed = rows.some((row) => row.assumed > 0 && row.status !== "done");
  return (
    <div className="plan-groups">
      {range || assumed ? (
        <p className="plan-muted plan-legend">
          {range ? (
            <>
              <span aria-hidden>
                <span className="plan-legend-target" /> target
              </span>
              <span aria-hidden>
                <span className="plan-legend-forecast" /> forecast
              </span>
              <span>timeline to {formatDay(new Date(range.end).toISOString().slice(0, 10))}</span>
            </>
          ) : null}
          {assumed ? <span>~ includes work with no measurement, priced at the plan's median</span> : null}
        </p>
      ) : null}
      <table className="plan-table">
        <caption className="visually-hidden">Milestones in phase order</caption>
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
            {range ? <th scope="col">Timeline</th> : null}
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
    </div>
  );
}
