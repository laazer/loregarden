import { useQuery } from "@tanstack/react-query";
import { Fragment, useMemo, useState } from "react";

import { api } from "../../../api/client";
import type { InitiativePlan, MilestoneSchedule, NodeStatus } from "../../../api/initiativeApi";
import { boardQueryKey, childrenByParent } from "../../../lib/initiativeBoard";
import { peekTicket } from "../../../state/ticketPeekStore";
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
import { describeError } from "../../../state/toastStore";
import { Button } from "../../ui/Button";
import { PhaseTree } from "./PhaseTree";
import { StatusChip } from "./ScheduleSummary";
import { TargetDateField } from "./TargetDateField";

const COUNT_LABEL: [NodeStatus, string][] = [
  ["running", "running"],
  ["ready", "ready"],
  ["needs_person", "for a person"],
  ["blocked", "blocked"],
  ["waiting", "waiting"],
];

function TimelineBar({ row, range, paced }: { row: MilestoneSchedule; range: TimelineRange; paced: boolean }) {
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
      {row.target_date && !paced ? (
        <span className="plan-timeline-target" style={{ left: `${timelinePercent(row.target_date, range)}%` }} />
      ) : null}
    </div>
  );
}

/** A phase that is not a milestone the initiative owns says what it is, and where it lives. */
function phaseKind(row: MilestoneSchedule, home: string | undefined): string {
  if (row.member) return `tracked ${row.work_item_type}${home ? ` in ${home}` : ""} · `;
  return row.work_item_type === "milestone" ? "" : `${row.work_item_type} · `;
}

function workSummary(row: MilestoneSchedule): string {
  const parts = COUNT_LABEL.flatMap(([status, label]) =>
    row.counts[status] ? [`${row.counts[status]} ${label}`] : [],
  );
  return parts.join(" · ") || (row.status === "done" ? "all done" : "nothing open");
}

function MilestoneRow({
  row,
  home,
  childCount,
  open,
  onToggle,
  range,
  paced,
  first,
  last,
  busy,
  untracking,
  onMove,
  onTarget,
  onUntrack,
}: {
  row: MilestoneSchedule;
  /** The milestone a tracked ticket stays in. */
  home: string | undefined;
  /** Tickets directly under it; null until the tree has loaded. */
  childCount: number | null;
  open: boolean;
  onToggle: () => void;
  range: TimelineRange | null;
  /** Pace mode: no targets in force, so no target column or marker. */
  paced: boolean;
  first: boolean;
  last: boolean;
  busy: boolean;
  untracking: boolean;
  onMove: (direction: -1 | 1) => void;
  onTarget: (date: string | null) => Promise<unknown>;
  onUntrack: () => void;
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
        <div className="plan-phase-name">
          {childCount ? (
            <Button
              variant="plain"
              className="plan-icon-btn plan-tree-toggle"
              aria-expanded={open}
              aria-controls={`plan-phase-tree-${row.id}`}
              aria-label={`${open ? "Hide" : "Show"} the ${childCount} under ${row.external_id}`}
              onClick={onToggle}
            >
              {open ? "▾" : "▸"}
            </Button>
          ) : (
            <span className="plan-tree-spacer" aria-hidden />
          )}
          <div>
            <Button
              variant="plain"
              className="plan-link"
              title={`Open ${row.external_id}`}
              onClick={() => peekTicket(row.id)}
            >
              <span className="plan-mono">{row.external_id}</span> {row.title}
            </Button>
            <div className="plan-muted">
              {row.workspace_slug} · {phaseKind(row, home)}
              {workSummary(row)}
              {row.member ? (
                <Button
                  variant="plain"
                  className="plan-inline-btn"
                  aria-label={`Stop tracking ${row.external_id} in this initiative`}
                  title="Leaves the ticket where it is; only drops it from this plan"
                  disabled={busy || untracking}
                  onClick={onUntrack}
                >
                  {untracking ? "Removing…" : "Stop tracking"}
                </Button>
              ) : null}
            </div>
          </div>
        </div>
      </th>
      <td className="plan-progress">
        {row.total - row.remaining}/{row.total}
      </td>
      {paced ? null : (
        <td>
          {done ? (
            formatDay(row.target_date)
          ) : (
            <TargetDateField value={row.target_date} label={`Target date for ${row.title}`} onCommit={onTarget} />
          )}
        </td>
      )}
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
          <TimelineBar row={row} range={range} paced={paced} />
        </td>
      ) : null}
    </tr>
  );
}

/**
 * Every phase with its target, forecast and the state of its work, in the
 * order phases get free lanes: the milestones the initiative owns and the
 * tickets it tracks, one table. Reordering is a planning move: an earlier
 * phase's ready work is started first, and ↑/↓ make it. A phase with work
 * under it opens into that work's tree.
 */
export function ScheduleTable({
  plan,
  busy,
  homes,
  untracking,
  onReorder,
  onTarget,
  onUntrack,
}: {
  plan: InitiativePlan;
  busy: boolean;
  /** Tracked ticket id -> the milestone it stays in. */
  homes: ReadonlyMap<string, string>;
  /** The tracked ticket being removed, if any. */
  untracking: string | null;
  /** The full new order, first phase first. */
  onReorder: (ordered: MilestoneSchedule[]) => void;
  onTarget: (row: MilestoneSchedule, date: string | null) => Promise<unknown>;
  onUntrack: (row: MilestoneSchedule) => void;
}) {
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set());
  // The board's query: one fetch serves both tabs.
  const tree = useQuery({
    queryKey: boardQueryKey(plan.id),
    queryFn: () => api.tickets({ ancestor_ticket_id: plan.id }),
    staleTime: 10_000,
    enabled: plan.milestones.length > 0,
    // Rendered under the table, with a retry; the schedule itself still works.
    meta: { suppressErrorToast: true },
  });
  const childrenOf = useMemo(() => childrenByParent(tree.data ?? []), [tree.data]);
  const nodeById = useMemo(() => new Map(plan.nodes.map((n) => [n.id, n])), [plan.nodes]);

  const rows = plan.milestones;
  if (rows.length === 0) {
    return (
      <div className="plan-empty">
        <p>
          This initiative has no milestones yet. Attach milestones from the Initiatives list, or track a ticket below —
          each one becomes a phase here, and its forecast follows from its tickets and how fast work is closing.
        </p>
      </div>
    );
  }

  const paced = plan.mode === "pace";
  // In pace mode a kept target is not drawn, so it must not stretch the axis either.
  const drawn = paced ? rows.map((row) => ({ target_date: null, forecast_date: row.forecast_date })) : rows;
  const range = hasTimelineDates(drawn) ? timelineRange(drawn, todayIso(new Date(plan.generated_at))) : null;
  const assumed = rows.some((row) => row.assumed > 0 && row.status !== "done");
  // Order, name, done, forecast, status — plus target and timeline when drawn.
  const columns = 5 + (paced ? 0 : 1) + (range ? 1 : 0);
  return (
    <div className="plan-groups">
      {range || assumed ? (
        <p className="plan-muted plan-legend">
          {range ? (
            <>
              {paced ? null : (
                <span aria-hidden>
                  <span className="plan-legend-target" /> target
                </span>
              )}
              <span aria-hidden>
                <span className="plan-legend-forecast" /> {paced ? "likely done at the current pace" : "forecast"}
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
            {paced ? null : <th scope="col">Target</th>}
            <th scope="col">{paced ? "Likely done" : "Forecast"}</th>
            <th scope="col">Status</th>
            {range ? <th scope="col">Timeline</th> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const expanded = open.has(row.id);
            return (
              <Fragment key={row.id}>
                <MilestoneRow
                  row={row}
                  home={homes.get(row.id)}
                  childCount={tree.isSuccess ? (childrenOf.get(row.id)?.length ?? 0) : null}
                  open={expanded}
                  onToggle={() =>
                    setOpen((current) => {
                      const next = new Set(current);
                      if (next.has(row.id)) next.delete(row.id);
                      else next.add(row.id);
                      return next;
                    })
                  }
                  range={range}
                  paced={paced}
                  first={index === 0}
                  last={index === rows.length - 1}
                  busy={busy}
                  untracking={untracking === row.id}
                  onTarget={(date) => onTarget(row, date)}
                  onUntrack={() => onUntrack(row)}
                  onMove={(direction) => {
                    const next = [...rows];
                    const [moved] = next.splice(index, 1);
                    next.splice(index + direction, 0, moved);
                    onReorder(next);
                  }}
                />
                {expanded ? (
                  <tr className="plan-tree-row">
                    <td colSpan={columns}>
                      <PhaseTree
                        id={`plan-phase-tree-${row.id}`}
                        root={row}
                        childrenOf={childrenOf}
                        nodeById={nodeById}
                      />
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      {tree.isError ? (
        <p className="plan-alert" role="alert">
          The work under each phase could not be loaded: {describeError(tree.error)}{" "}
          <Button variant="plain" className="plan-inline-btn" onClick={() => void tree.refetch()}>
            Retry
          </Button>
        </p>
      ) : null}
    </div>
  );
}
