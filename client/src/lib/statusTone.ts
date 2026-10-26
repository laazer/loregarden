/**
 * The six things a status can mean, and the one hue each owns.
 *
 * Ticket state, stage state, run state and activity each grew their own
 * colours, and green ended up meaning four things (done, running, succeeded,
 * the accent). Every status vocabulary now maps onto these tones, so a hue
 * means one thing wherever it appears, and green is left to the accent and
 * primary actions.
 *
 * A tone is never colour alone: StatusChip pairs it with a glyph and a word.
 */

export type StatusTone = "run" | "bad" | "wait" | "you" | "done" | "idle";

/** A status as it reads on screen: what it means, and what it says. */
export interface ChipStatus {
  tone: StatusTone;
  label: string;
}

/**
 * The hue of each tone, for marks that carry no word of their own (a tree
 * dot, a stage segment). Anything with room for text uses StatusChip instead.
 * Done and idle are deliberately quiet: finished and untouched work should not
 * compete with live work.
 */
export const STATUS_TONE_COLORS: Record<StatusTone, string> = {
  run: "var(--blue)",
  bad: "var(--red)",
  wait: "var(--amb)",
  you: "var(--vio)",
  done: "var(--txm)",
  idle: "var(--txl)",
};
