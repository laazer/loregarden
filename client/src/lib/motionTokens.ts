/**
 * The motion tokens on `:root` in index.css, as numbers Motion can take.
 *
 * Motion animates in JS, so it cannot read `var(--t-med)`. These mirror the
 * CSS values (seconds, cubic-bezier control points); a test fails when the two
 * drift. The rules are in @lore-eden/ui's tokens/specs.ts: fast for hover and
 * press, med for toggles, dropdowns and toasts, slow for modals and panels,
 * and an exit runs one step faster than its entrance.
 */
export const DURATION = {
  fast: 0.12,
  med: 0.2,
  slow: 0.3,
  stagger: 0.04,
} as const;

export const EASE_OUT = [0.4, 0, 0.2, 1] as const;
