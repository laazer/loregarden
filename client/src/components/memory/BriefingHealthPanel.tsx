/**
 * Briefing health for a window of runs — what `183` records, rendered.
 *
 * Holes are shown apart from the outcome breakdown on purpose. A hole is a run
 * where nothing was recorded at all, which is the failure the telemetry exists
 * to expose; drawn as one more bar beside `built` and `empty` it would read as
 * a sixth outcome rather than as the absence of one.
 */

import type { MemoryBriefingStats } from "../../api/memoryApi";
import { BRIEFING_OUTCOMES, type BriefingOutcome } from "../../api/memoryApi";
import { WINDOW_CHOICES, holeShare, isEmptyWindow, seamVerdict } from "../../lib/memoryHealth";
import { describeError } from "../../state/toastStore";
import { PaneSkeleton } from "../ui/PaneSkeleton";

const OUTCOME_LABELS: Record<BriefingOutcome, { label: string; hint: string }> = {
  built: { label: "Built", hint: "a briefing with content reached the prompt" },
  empty: { label: "Empty", hint: "stores read fine and held nothing relevant" },
  store_error: { label: "Store error", hint: "a store failed to read" },
  no_store: { label: "No store", hint: "no memory store is configured" },
  skipped: { label: "Skipped", hint: "a verify stage, which carries no briefing" },
};

function WindowControl({
  windowDays,
  onChange,
}: {
  windowDays: number;
  onChange: (days: number) => void;
}) {
  return (
    <div className="memory-window" role="radiogroup" aria-label="Window">
      {WINDOW_CHOICES.map((days) => (
        <button
          key={days}
          type="button"
          role="radio"
          aria-checked={days === windowDays}
          className={`memory-window-option${days === windowDays ? " selected" : ""}`}
          onClick={() => onChange(days)}
        >
          {days} days
        </button>
      ))}
    </div>
  );
}

function Holes({ stats }: { stats: MemoryBriefingStats }) {
  const verdict = seamVerdict(stats);
  const holes = stats.runs_with_no_briefing_row;
  return (
    <section
      className={`memory-holes${holes > 0 || verdict.tone === "dead" ? " memory-holes--bad" : ""}`}
      aria-labelledby="memory-holes-title"
    >
      <h3 id="memory-holes-title" className="memory-section-title">
        Runs that recorded nothing
      </h3>
      <div className="memory-holes-figure">
        <span className="memory-holes-count">{holes}</span>
        <span className="memory-holes-of">
          of {stats.runs_in_window} runs ({holeShare(stats)}%)
        </span>
      </div>
      <p className={`memory-seam memory-seam--${verdict.tone}`} role="status">
        {verdict.text}
      </p>
    </section>
  );
}

function Outcomes({ stats }: { stats: MemoryBriefingStats }) {
  const total = Math.max(stats.rows_in_window, 1);
  return (
    <section aria-labelledby="memory-outcomes-title">
      <h3 id="memory-outcomes-title" className="memory-section-title">
        Recorded outcomes
      </h3>
      <p className="memory-muted">
        {stats.rows_in_window} briefings across {stats.runs_with_briefing_row} runs. A run can
        record two when its prompt is assembled twice.
      </p>
      <dl className="memory-outcomes">
        {BRIEFING_OUTCOMES.map((outcome) => (
          <div key={outcome} className={`memory-outcome memory-outcome--${outcome}`}>
            <dt title={OUTCOME_LABELS[outcome].hint}>{OUTCOME_LABELS[outcome].label}</dt>
            <dd>
              <span className="memory-outcome-count">{stats[outcome]}</span>
              <span className="memory-outcome-bar" aria-hidden>
                <span style={{ width: `${(stats[outcome] / total) * 100}%` }} />
              </span>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export function BriefingHealthPanel({
  stats,
  isLoading,
  error,
  windowDays,
  onWindowChange,
  onRetry,
}: {
  stats: MemoryBriefingStats | undefined;
  isLoading: boolean;
  error: unknown;
  windowDays: number;
  onWindowChange: (days: number) => void;
  onRetry: () => void;
}) {
  let body;
  if (stats) {
    body = isEmptyWindow(stats) ? (
      <p className="memory-empty">
        No runs in this window. Briefings are recorded when a stage run builds its prompt — start a
        run, or widen the window.
      </p>
    ) : (
      <div className="memory-health-grid">
        <Holes stats={stats} />
        <Outcomes stats={stats} />
      </div>
    );
  } else if (isLoading) {
    body = <PaneSkeleton variant="list" rows={5} label="Loading briefing health…" />;
  } else {
    body = (
      <div className="memory-error" role="alert">
        <p>Could not load briefing health: {describeError(error, "the request failed")}.</p>
        <button type="button" className="btn-secondary" onClick={onRetry}>
          Try again
        </button>
      </div>
    );
  }
  return (
    <section className="memory-panel" aria-labelledby="memory-health-title">
      <header className="memory-panel-header">
        <h2 id="memory-health-title" className="memory-panel-title">
          Briefing health
        </h2>
        <WindowControl windowDays={windowDays} onChange={onWindowChange} />
      </header>
      {body}
    </section>
  );
}
