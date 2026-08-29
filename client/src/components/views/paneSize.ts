/**
 * How much room a pane actually has, published to the primitive inside it.
 *
 * A container primitive is handed its settings and nothing about the space it
 * is drawn in — so every one of them is written for a size nobody promised.
 * The grid lets a pane be a third of a screen or eighty pixels tall, and the
 * canvas lets it be anything at all; a card that is right at 600px wide has a
 * two-line title and a wrapped button row at 200.
 *
 * `min-height: 0` and `overflow: auto` (in `registry`) keep a too-small pane
 * from breaking the *layout*. This is the other half: letting a primitive
 * choose different content rather than the same content with a scrollbar.
 *
 * ## Tiers, not pixels, for the decision
 *
 * The measurement is exact and both numbers are published, because a primitive
 * that genuinely needs pixels — a chart, a virtualiser — should have them. But
 * the layout decision is a tier, so the thresholds are chosen once here instead
 * of being scattered as magic numbers through sixteen primitives that would
 * each pick a slightly different one.
 *
 * Height is part of the answer, not a footnote. A pane 900px wide and 90px tall
 * is `compact`: the constraint an operator feels there is vertical, and a
 * primitive that only consulted width would lay out a comfortable three-column
 * card into a letterbox.
 *
 * ## Before the first measurement
 *
 * `null` until a `ResizeObserver` has reported, and `usePaneSize` hands back
 * `regular` in that case. Guessing `compact` would make every pane flash its
 * dense layout on mount; guessing `wide` would do the reverse. `regular` is the
 * layout most primitives are already written for, so the frame that precedes
 * the measurement looks like the steady state rather than like a glitch.
 */

import { createContext, useContext } from "react";

/**
 * The coarse answer a primitive lays out against.
 *
 * Three, not five: every tier has to be a layout somebody actually writes, and
 * a vocabulary with more entries than that is one where most primitives ignore
 * the middle ones and the tiers stop meaning anything.
 */
export const PANE_TIERS = ["compact", "regular", "wide"] as const;

export type PaneTier = (typeof PANE_TIERS)[number];

/**
 * The thresholds, in CSS pixels, spelled once.
 *
 * `COMPACT_WIDTH` is a little under half a 720px column — the width at which a
 * two-column card stops fitting — and `COMPACT_HEIGHT` is roughly a header plus
 * two rows, below which a list is a scrollbar with a hint of content.
 */
export const COMPACT_WIDTH = 340;
export const COMPACT_HEIGHT = 200;
export const WIDE_WIDTH = 720;

export interface PaneSize {
  width: number;
  height: number;
  tier: PaneTier;
}

/** The tier a measured pane falls in. Exported so a test can ask directly. */
export function paneTierFor(width: number, height: number): PaneTier {
  if (width < COMPACT_WIDTH || height < COMPACT_HEIGHT) return "compact";
  if (width >= WIDE_WIDTH) return "wide";
  return "regular";
}

/**
 * `null` means "not measured yet", which is not the same as "zero".
 *
 * A pane genuinely measuring 0×0 is hidden, and `paneTierFor` calls that
 * `compact` — the right answer. Collapsing the two would make every primitive
 * outside a measured host render its densest layout forever.
 */
export const PaneSizeContext = createContext<PaneSize | null>(null);

/** What the pane measures, or the regular-sized stand-in described above. */
export function usePaneSize(): PaneSize {
  return useContext(PaneSizeContext) ?? { width: 0, height: 0, tier: "regular" };
}

/** Whether the pane has actually been measured, for the rare caller that cares. */
export function usePaneMeasured(): boolean {
  return useContext(PaneSizeContext) !== null;
}
