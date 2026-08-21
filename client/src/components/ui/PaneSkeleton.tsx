/**
 * The placeholder a pane shows while its content is still on the wire.
 *
 * A pane that is empty while it loads is indistinguishable from a pane that is
 * broken — the same reasoning behind `views/primitives/Unconfigured`, applied to
 * the other half of the wait. So the placeholder is shaped like the content it
 * is standing in for: a file list draws rows, a file draws code lines, and a
 * whole surface draws a block. The shape is the signal that something is coming.
 *
 * The bars are decoration. The wait itself is announced once, politely, by the
 * wrapper's `role="status"` — a screen reader hears "Loading file…", not a run
 * of empty divs.
 */

import "./PaneSkeleton.css";

export type PaneSkeletonVariant = "list" | "code" | "block";

/**
 * Bar widths, as percentages, cycled across rows.
 *
 * Fixed rather than random: a skeleton that reshuffles on every render flickers,
 * and a random width is a test that cannot assert what it drew.
 */
const LIST_WIDTHS = [72, 54, 83, 61, 76, 48, 68, 58];
const CODE_WIDTHS = [64, 88, 41, 73, 55, 92, 37, 69, 80, 46, 61, 77];

const DEFAULT_ROWS: Record<PaneSkeletonVariant, number> = {
  list: 8,
  code: 12,
  block: 1,
};

export interface PaneSkeletonProps {
  /** Which content this stands in for. Defaults to a single filling block. */
  variant?: PaneSkeletonVariant;
  /** How many rows to draw. Defaults per variant; ignored by `block`. */
  rows?: number;
  /** What the wait is, said once to assistive tech. */
  label?: string;
}

export function PaneSkeleton({ variant = "block", rows, label = "Loading…" }: PaneSkeletonProps) {
  const count = Math.max(1, rows ?? DEFAULT_ROWS[variant]);
  const widths = variant === "code" ? CODE_WIDTHS : LIST_WIDTHS;

  return (
    <div
      className={`pane-skeleton pane-skeleton--${variant}`}
      data-testid="pane-skeleton"
      data-variant={variant}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <span className="pane-skeleton-label">{label}</span>
      {variant === "block" ? (
        <div className="pane-skeleton-bar pane-skeleton-block" aria-hidden />
      ) : (
        Array.from({ length: count }, (_, row) => (
          <div className="pane-skeleton-row" key={row} aria-hidden>
            {variant === "code" ? <span className="pane-skeleton-bar pane-skeleton-gutter" /> : null}
            <span
              className="pane-skeleton-bar pane-skeleton-line"
              style={{ width: `${widths[row % widths.length]}%` }}
            />
          </div>
        ))
      )}
    </div>
  );
}
