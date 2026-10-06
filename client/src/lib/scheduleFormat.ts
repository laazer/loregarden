/**
 * Words and geometry for initiative schedules — kept out of the components so
 * the plan page, the list card and tests all say the same thing.
 */

import type {
  AutopilotAction,
  ForecastBasis,
  InitiativePlan,
  MilestoneSchedule,
  ScheduleProposal,
  ScheduleStatus,
  WorkspacePace,
} from "../api/initiativeApi";

export const SCHEDULE_STATUS_LABEL: Record<ScheduleStatus, string> = {
  done: "Done",
  on_track: "On track",
  behind: "Behind",
  late: "Late",
  unscheduled: "No target",
  no_forecast: "No forecast",
};

/** Token per status, so a status reads the same wherever it is drawn. */
export const SCHEDULE_STATUS_COLOR: Record<ScheduleStatus, string> = {
  done: "var(--grn)",
  on_track: "var(--ac)",
  behind: "var(--amb)",
  late: "var(--red)",
  unscheduled: "var(--txl)",
  no_forecast: "var(--txl)",
};

export const BASIS_LABEL: Record<ForecastBasis, string> = {
  initiative_throughput: "this initiative's pace",
  workspace_throughput: "the workspace's pace",
  agent_time: "agent run-time",
  none: "nothing measured yet",
};

const MS_PER_DAY = 86_400_000;

/** `YYYY-MM-DD` as a UTC midnight timestamp — the API's dates have no zone. */
export function isoDay(iso: string): number {
  return Date.parse(`${iso}T00:00:00Z`);
}

export function todayIso(now: Date = new Date()): string {
  return now.toISOString().slice(0, 10);
}

/**
 * "Nov 26", or "Jul 7, 2027" outside the current year — plans span year ends,
 * and a bare "Jul 7" next to "Nov 26" reads as already past.
 */
export function formatDay(iso: string | null, now: Date = new Date()): string {
  if (!iso) return "—";
  const sameYear = iso.slice(0, 4) === todayIso(now).slice(0, 4);
  return new Date(isoDay(iso)).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: sameYear ? undefined : "numeric",
    timeZone: "UTC",
  });
}

/** "3d late", "2d early", "on target" — or null when either date is missing. */
export function formatDrift(days: number | null): string | null {
  if (days === null) return null;
  if (days === 0) return "on target";
  return days > 0 ? `${days}d late` : `${-days}d early`;
}

export function formatPace(pace: WorkspacePace): string {
  if (pace.per_day === null) return `${pace.workspace_slug}: no completions in the window`;
  const perWeek = pace.per_day * 7;
  return `${pace.workspace_slug}: ${perWeek.toFixed(perWeek < 10 ? 1 : 0)}/wk (${BASIS_LABEL[pace.basis]})`;
}

/** Why a milestone has the forecast it has, in one line for a tooltip. */
export function forecastExplanation(m: MilestoneSchedule): string {
  if (m.status === "done") return "Resolved.";
  if (m.forecast_date === null) {
    return "No forecast: some of its work cannot be priced — nothing measured it, and nothing else in the plan has been.";
  }
  const assumed =
    m.assumed > 0
      ? ` ${m.assumed} of them had no measurement and were priced at the plan's median.`
      : "";
  return `When the last of its ${m.remaining} open item${m.remaining === 1 ? "" : "s"} lands, following their prerequisites, one ticket per lane, at ${BASIS_LABEL[m.basis]}.${assumed}`;
}

export const AUTOPILOT_ACTION_LABEL: Record<AutopilotAction, string> = {
  enabled: "Turned on",
  disabled: "Turned off",
  dispatched: "Queued",
  refused: "Held back",
  paused: "Stopped itself",
};

/** "Oct 2, 14:05" — when an autopilot event happened. */
export function formatWhen(iso: string): string {
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return "";
  return value.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export interface TimelineRange {
  start: number;
  end: number;
}

/** The span the timeline draws: today to the latest date any row names, with a little air. */
export function timelineRange(rows: MilestoneSchedule[], today: string): TimelineRange {
  const start = isoDay(today);
  let end = start + 14 * MS_PER_DAY;
  for (const row of rows) {
    for (const iso of [row.target_date, row.forecast_date]) {
      if (iso) end = Math.max(end, isoDay(iso));
    }
  }
  return { start, end: end + 3 * MS_PER_DAY };
}

/** Percent along the range, clamped — a past target pins to the left edge. */
export function timelinePercent(iso: string, range: TimelineRange): number {
  const span = Math.max(1, range.end - range.start);
  return Math.min(100, Math.max(0, ((isoDay(iso) - range.start) / span) * 100));
}

/** Milestones grouped by workspace, each group in plan order. */
export function groupByWorkspace(rows: MilestoneSchedule[]): [string, MilestoneSchedule[]][] {
  const groups = new Map<string, MilestoneSchedule[]>();
  for (const row of rows) {
    const list = groups.get(row.workspace_slug) ?? [];
    list.push(row);
    groups.set(row.workspace_slug, list);
  }
  return [...groups.entries()];
}

export interface ProposalRow {
  key: string;
  label: string;
  from: string;
  to: string;
}

/** What accepting would change, row by row — unchanged rows are left out. */
export function proposalRows(plan: InitiativePlan, proposal: ScheduleProposal): ProposalRow[] {
  const byId = new Map(plan.milestones.map((m) => [m.id, m]));
  const rows: ProposalRow[] = [];
  if (proposal.mode && proposal.mode !== plan.mode) {
    rows.push({ key: "mode", label: "Mode", from: plan.mode, to: proposal.mode });
  }
  for (const item of proposal.items) {
    const milestone = byId.get(item.ticket_id);
    const current = item.ticket_id === plan.id ? plan.target_date : (milestone?.target_date ?? null);
    const label =
      item.ticket_id === plan.id
        ? `${plan.external_id} (whole initiative)`
        : milestone
          ? `${milestone.external_id} ${milestone.title}`
          : `${item.ticket_id} (no longer attached)`;
    // An absent key was not proposed; null clears the date.
    const proposed = item.target_date === undefined ? current : item.target_date;
    if (proposed !== current) {
      rows.push({ key: `${item.ticket_id}:date`, label, from: formatDay(current), to: formatDay(proposed) });
    }
    if (milestone && item.plan_order != null && item.plan_order !== milestone.plan_order) {
      rows.push({
        key: `${item.ticket_id}:order`,
        label: `${milestone.external_id} order`,
        from: `#${milestone.plan_order + 1}`,
        to: `#${item.plan_order + 1}`,
      });
    }
  }
  return rows;
}

/** Whether any row has a date the timeline could draw. */
export function hasTimelineDates(rows: MilestoneSchedule[]): boolean {
  return rows.some((row) => row.target_date || row.forecast_date);
}
