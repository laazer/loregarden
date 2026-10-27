/**
 * A vine: how a sequence is drawn wherever order matters (workflow stages
 * first). A leaf is a finished step, a glowing bud the one running, a closed
 * bud one still to come. Lists, boards and tables never get one, so a vine
 * always means "this happens in order".
 *
 * Append-stable, as living-map's tree layout is (harshit-pi/living-map,
 * `web/map/layout.ts` at 81df5a7): a row's shape follows from its own index
 * and state, never from the row count. A stage added at the end (a loop, a
 * re-run) grows the vine downward: the row that was last grows a stem to it,
 * and nothing above moves, flips side or changes shape.
 */

import type { StageStatus } from "../api/types";

/** What grows at a stage's node. */
export type VineMark =
  /** Finished: the step is done. */
  | "leaf"
  /** The step running now, or the one the run is on. */
  | "bud-open"
  /** Stopped on a person: an approval, an answer. */
  | "bud-held"
  /** Stopped on a failure. */
  | "bud-blocked"
  /** Not reached yet. */
  | "bud-closed"
  /** Pruned: the run decided the step was not needed. */
  | "pruned";

export function vineMark(status: StageStatus, isCurrent: boolean): VineMark {
  switch (status) {
    case "done":
      return "leaf";
    case "running":
      return "bud-open";
    case "awaiting":
      return "bud-held";
    case "blocked":
      return "bud-blocked";
    case "wont_do":
      return "pruned";
    case "pending":
      // The stage the run is on, before an agent has picked it up, is still
      // the one to watch.
      return isCurrent ? "bud-open" : "bud-closed";
  }
}

/** The stem below a node: grown green once the step is done, faint until then. */
export function stemGrown(mark: VineMark): boolean {
  return mark === "leaf" || mark === "pruned";
}

/** Which way a row's leaf points, and which way its stem leans on the way down. */
export type VineSide = "left" | "right";

/**
 * A row's side, from its index alone: the vine alternates, so leaves fall on
 * either side the way they grow. Never from the row count, so appending a row
 * cannot flip one above it.
 */
export function vineSide(index: number): VineSide {
  return index % 2 === 0 ? "right" : "left";
}

/**
 * The stem from one node down to the next, in a 14 × 100 box the row stretches
 * to its own height. It leaves the node at the centre, leans toward the row's
 * side, and comes back to the centre by the next node, so neighbouring rows
 * join without a kink.
 */
export function stemPath(index: number): string {
  const lean = vineSide(index) === "right" ? 11 : 3;
  return `M 7 0 C ${lean} 30, ${lean} 70, 7 100`;
}
