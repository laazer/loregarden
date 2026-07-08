import type { WalkGrid } from "../pathfinding";
import {
  OFFICEPLACE_WALK_HEIGHT,
  OFFICEPLACE_WALK_ROWS,
  OFFICEPLACE_WALK_WIDTH,
} from "./officeplaceWalkability";

export function createOfficeplaceWalkGrid(): WalkGrid {
  function baseWalkable(x: number, y: number): boolean {
    if (x < 0 || y < 0 || x >= OFFICEPLACE_WALK_WIDTH || y >= OFFICEPLACE_WALK_HEIGHT) {
      return false;
    }
    return OFFICEPLACE_WALK_ROWS[y]![x] === "1";
  }

  return {
    width: OFFICEPLACE_WALK_WIDTH,
    height: OFFICEPLACE_WALK_HEIGHT,
    isWalkable(x: number, y: number) {
      return baseWalkable(x, y);
    },
    isStandable(x: number, y: number) {
      if (!baseWalkable(x, y)) return false;
      // Never plant feet on the bottom map row (entrance lip / outer wall).
      if (y >= OFFICEPLACE_WALK_HEIGHT - 1) return false;
      // Stay off tiles that hug a south wall cell on the next row.
      if (!baseWalkable(x, y + 1)) return false;
      // Stay off the row directly above the bottom wall/entrance strip.
      if (y + 1 >= OFFICEPLACE_WALK_HEIGHT - 1) return false;
      return true;
    },
  };
}
