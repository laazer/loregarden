/**
 * A status, said three ways at once: a hue, a glyph and a word.
 *
 * One component for every status vocabulary (ticket state, activity, stage,
 * run), so the same meaning looks the same everywhere. Running is a blue dot,
 * failed a red square, waiting an amber dot, needs-you a violet diamond, done a
 * muted tick and idle an outline. The glyph is what keeps it readable without
 * colour: a red square and a blue dot differ in shape, not just hue.
 *
 * The glyph is decorative (`aria-hidden`); the word is the accessible text, so
 * a screen reader hears "Failed", not "red square Failed".
 */

import type { StatusTone } from "../../lib/statusTone";

import { joinClasses } from "./classNames";
import "./StatusChip.css";

export interface StatusChipProps {
  tone: StatusTone;
  label: string;
  /** Extra detail on hover, e.g. how long a run has held its slot. */
  title?: string;
  className?: string;
}

export function StatusChip({ tone, label, title, className }: StatusChipProps) {
  return (
    <span
      className={joinClasses("status-chip", `status-chip--${tone}`, className)}
      data-tone={tone}
      title={title}
    >
      <span className="status-chip-glyph" aria-hidden="true" />
      {label}
    </span>
  );
}
