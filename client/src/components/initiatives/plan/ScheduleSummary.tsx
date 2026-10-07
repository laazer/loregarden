import type { InitiativePlan, ScheduleMode } from "../../../api/initiativeApi";
import {
  SCHEDULE_STATUS_COLOR,
  SCHEDULE_STATUS_LABEL,
  formatDay,
  formatDrift,
  formatPace,
} from "../../../lib/scheduleFormat";
import { Button } from "../../ui/Button";
import { TargetDateField } from "./TargetDateField";

const MODES: { mode: ScheduleMode; label: string; hint: string }[] = [
  {
    mode: "fixed",
    label: "Fixed",
    hint: "Targets stay put; the forecast shows whether you are ahead or behind.",
  },
  {
    mode: "rolling",
    label: "Rolling",
    hint: "The planned dates follow the forecast as work speeds up or slows down.",
  },
  {
    mode: "pace",
    label: "Pace",
    hint: "No official dates: shows where the work will likely land at the current pace. Targets are kept for when you switch back.",
  },
];

export function StatusChip({ status }: { status: InitiativePlan["status"] }) {
  return (
    <span className="plan-status" style={{ color: SCHEDULE_STATUS_COLOR[status] }}>
      {SCHEDULE_STATUS_LABEL[status]}
    </span>
  );
}

/** The initiative's own line: where it stands, and how its plan behaves. */
export function ScheduleSummary({
  plan,
  busy,
  onModeChange,
  onTargetChange,
}: {
  plan: InitiativePlan;
  busy: boolean;
  onModeChange: (mode: ScheduleMode) => void;
  onTargetChange: (date: string | null) => Promise<unknown>;
}) {
  const drift = formatDrift(plan.drift_days);
  // Pace mode has no dates in force: the forecast is the whole answer.
  const paced = plan.mode === "pace";
  return (
    <section className="plan-summary" aria-label="Schedule summary">
      <div className="plan-summary-dates">
        <div>
          <div className="plan-label">Status</div>
          <div className="plan-value">
            <StatusChip status={plan.status} />
          </div>
        </div>
        {paced ? null : (
          <div>
            <div className="plan-label">Planned</div>
            <div className="plan-value plan-figure">{formatDay(plan.planned_date)}</div>
          </div>
        )}
        <div>
          <div className="plan-label">{paced ? "Likely done" : "Forecast"}</div>
          <div className="plan-value plan-figure">
            {plan.forecast_date
              ? formatDay(plan.forecast_date)
              : plan.unforecast_milestones > 0
                ? "Unknown"
                : "—"}
          </div>
          {drift ? <div className="plan-under">{drift}</div> : null}
        </div>
        {paced ? null : (
          <div>
            <div className="plan-label">Target</div>
            <div className="plan-value">
              <TargetDateField
                value={plan.target_date}
                label={`Target date for ${plan.title}`}
                onCommit={onTargetChange}
              />
            </div>
          </div>
        )}
        <div role="group" aria-label="Schedule mode">
          <div className="plan-label">Mode</div>
          <div className="plan-value plan-mode-buttons">
            {MODES.map(({ mode, label }) => (
              <Button
                key={mode}
                variant="plain"
                className={`plan-mode-btn${plan.mode === mode ? " active" : ""}`}
                aria-pressed={plan.mode === mode}
                aria-describedby="plan-mode-hint"
                disabled={busy}
                onClick={() => plan.mode !== mode && onModeChange(mode)}
              >
                {label}
              </Button>
            ))}
          </div>
          <p id="plan-mode-hint" className="plan-under plan-mode-hint">
            {MODES.find((m) => m.mode === plan.mode)?.hint}
          </p>
        </div>
      </div>

      {plan.unforecast_milestones > 0 ? (
        <p className="plan-hint">
          {plan.unforecast_milestones} open milestone{plan.unforecast_milestones === 1 ? " has" : "s have"} no
          forecast: some of the work cannot be priced, because nothing in this plan has a run-time history or a
          recent pace yet. It appears as soon as any of the work has been run or closed.
        </p>
      ) : null}

      <p className="plan-muted">
        Pace over the last {plan.window_days} days — {plan.paces.map(formatPace).join(" · ") || "no workspaces yet"}
      </p>
    </section>
  );
}
