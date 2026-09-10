/**
 * How much of a bounded resource is spoken for.
 *
 * The first shared meter in the app, so it settles two things the queue rail's
 * ad-hoc percentages left open.
 *
 * **An unmeasured ceiling is not an empty one.** A pool whose limit nobody has
 * established renders `0 / 0`, and a bar drawn from that reads as "completely
 * free" — the most dangerous thing this component could say, since the honest
 * answer is that nothing is known. `total <= 0` therefore draws no bar at all
 * and says so in words.
 *
 * **Never colour alone.** The fill carries a tone, and the same fact is always
 * written next to it as text. A red bar and an amber bar are the same bar to a
 * viewer who cannot separate them, and roughly 1 in 12 men cannot.
 *
 * The `meter` role is deliberate over a bare `progressbar`: this is a *level*
 * within a known range, not progress toward completion, and `aria-valuetext`
 * carries the units so a screen reader hears "3 of 5 cpus" rather than "60".
 */

import "./CapacityMeter.css";

export type CapacityTone = "normal" | "tight" | "full" | "unknown";

export interface CapacityMeterProps {
  /** What is being measured, e.g. "CPU". Rendered, and used as the accessible name. */
  label: string;
  used: number;
  /** The ceiling. Zero or less means unmeasured — see the module note. */
  total: number;
  /** Renders an amount for display, units included. */
  format: (value: number) => string;
  /** Overrides the tone derived from the ratio. */
  tone?: CapacityTone;
  /** Said instead of the numbers when the ceiling is unmeasured. */
  unknownLabel?: string;
}

/**
 * Tone from how little is left, not how much is used.
 *
 * The thresholds are about the decision the reader is making — "can I start
 * something?" — so they sit where the answer changes, not at round numbers.
 */
function deriveTone(used: number, total: number): CapacityTone {
  if (total <= 0) return "unknown";
  const free = total - used;
  if (free <= 0) return "full";
  if (free / total < 0.25) return "tight";
  return "normal";
}

export function CapacityMeter({
  label,
  used,
  total,
  format,
  tone,
  unknownLabel = "not measured",
}: CapacityMeterProps) {
  const resolved = tone ?? deriveTone(used, total);
  const measured = total > 0;
  // Clamped so an over-booked pool — which a reconciliation lag can produce for
  // a moment — draws a full bar rather than one overflowing its track.
  const percent = measured ? Math.min(100, Math.max(0, (used / total) * 100)) : 0;
  const valueText = measured ? `${format(used)} of ${format(total)}` : unknownLabel;

  return (
    <div className="capacity-meter" data-testid={`capacity-meter-${label.toLowerCase()}`}>
      <div className="capacity-meter-head">
        <span className="capacity-meter-label">{label}</span>
        <span className={`capacity-meter-value capacity-meter-value--${resolved}`}>
          {valueText}
        </span>
      </div>
      <div
        className={`capacity-meter-track capacity-meter-track--${resolved}`}
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={measured ? total : undefined}
        aria-valuenow={measured ? used : undefined}
        aria-valuetext={valueText}
        data-tone={resolved}
      >
        {measured ? (
          <span className="capacity-meter-fill" style={{ width: `${percent}%` }} aria-hidden />
        ) : null}
      </div>
    </div>
  );
}
