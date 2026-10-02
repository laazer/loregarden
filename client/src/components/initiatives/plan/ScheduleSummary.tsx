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
  onTargetChange: (date: string | null) => void;
}) {
  const drift = formatDrift(plan.drift_days);
  return (
    <section className="plan-summary" aria-label="Schedule summary">
      <div className="plan-summary-dates">
        <div>
          <div className="plan-label">Status</div>
          <StatusChip status={plan.status} />
        </div>
        <div>
          <div className="plan-label">Planned</div>
          <div className="plan-figure">{formatDay(plan.planned_date)}</div>
        </div>
        <div>
          <div className="plan-label">Forecast</div>
          <div className="plan-figure">
            {plan.forecast_date
              ? formatDay(plan.forecast_date)
              : plan.unforecast_milestones > 0
                ? "Unknown"
                : "—"}
          </div>
          {drift ? <div className="plan-muted">{drift}</div> : null}
        </div>
        <div>
          <div className="plan-label">Target</div>
          <TargetDateField
            value={plan.target_date}
            label={`Target date for ${plan.title}`}
            disabled={busy}
            onCommit={onTargetChange}
          />
        </div>
        <div role="group" aria-label="Schedule mode">
          <div className="plan-label">Mode</div>
          <div className="plan-mode-buttons">
            {MODES.map(({ mode, label, hint }) => (
              <Button
                key={mode}
                variant="plain"
                className={`plan-mode-btn${plan.mode === mode ? " active" : ""}`}
                aria-pressed={plan.mode === mode}
                title={hint}
                disabled={busy}
                onClick={() => plan.mode !== mode && onModeChange(mode)}
              >
                {label}
              </Button>
            ))}
          </div>
        </div>
      </div>

      {plan.unforecast_milestones > 0 ? (
        <p className="plan-hint">
          {plan.unforecast_milestones} open milestone{plan.unforecast_milestones === 1 ? " has" : "s have"} no
          forecast: nothing has closed in {plan.unforecast_milestones === 1 ? "its workspace" : "their workspaces"} in
          the last {plan.window_days} days. The forecast appears as soon as work there starts finishing.
        </p>
      ) : null}

      <p className="plan-muted">
        Pace over the last {plan.window_days} days — {plan.paces.map(formatPace).join(" · ") || "no workspaces yet"}
      </p>
    </section>
  );
}
