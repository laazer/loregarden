import type { CSSProperties } from "react";

/**
 * The inline style for a progress fill at `percent` of its track.
 *
 * The fill spans the track and is scaled from its left edge, so a change of
 * value animates `transform` on the compositor instead of relaying out the
 * page every frame, as a `width` transition did. Out-of-range input (a ratio
 * over 1, a negative estimate, NaN) is clamped rather than drawn past the track.
 */
export function progressFillStyle(percent: number): CSSProperties {
  const clamped = Number.isFinite(percent) ? Math.min(100, Math.max(0, percent)) : 0;
  return { transform: `scaleX(${clamped / 100})` };
}
