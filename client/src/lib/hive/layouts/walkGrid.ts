import type { WalkGrid } from "../pathfinding";
import { OFFICEPLACE_FLOOR, isFloorBlocked } from "./officeplaceFloorPlan";

export function createOfficeplaceWalkGrid(): WalkGrid {
  const { width, height } = OFFICEPLACE_FLOOR;

  function baseWalkable(x: number, y: number): boolean {
    return !isFloorBlocked(x, y);
  }

  return {
    width,
    height,
    isWalkable: baseWalkable,
    isStandable(x: number, y: number) {
      // Desks and walls already block; anything else on the floor can be stood on.
      return baseWalkable(x, y);
    },
  };
}

/**
 * Collision for the office background image (floor-bg.png), in the 60×50 tile space
 * the NPC coordinates share. Walkable = union of room rectangles bridged by 1-tile
 * doorways, so agents route through doors instead of cutting across walls. The green
 * MDR room reaches the floor through the elevator strip down the right edge; the
 * foundation strip along the bottom stays excluded.
 *
 * Rectangles are [x0, y0, x1, y1] inclusive, hand-aligned to the image and verified
 * against a walkability overlay; see scripts note in ATTRIBUTION.md.
 */
type Box = readonly [number, number, number, number];

const WALK_ROOMS: Box[] = [
  // top band
  [1, 1, 32, 10], // lobby (front hall, checkered floor)
  [34, 5, 45, 10], // break room / kitchen
  // upper-left offices
  [1, 12, 7, 18], // accounting
  [1, 20, 7, 28], // records office
  // centre-left suite
  [9, 12, 21, 28], // pink offices
  // central open plan
  [23, 12, 38, 30], // bullpen (open sales floor)
  [40, 12, 44, 30], // right offices (Michael + reception run)
  // lower-left
  [1, 30, 7, 35], // small office
  [1, 36, 7, 46], // lounge
  // centre-lower
  [9, 30, 17, 46], // stairwell
  [19, 32, 31, 46], // conference room
  [33, 40, 38, 46], // restrooms
  [40, 32, 44, 46], // office / logo
  // right side
  [47, 1, 57, 10], // MDR (QA desks)
  [52, 10, 57, 17], // elevator strip: MDR → annex down the right edge
  [46, 17, 58, 46], // annex + elevator lobby
];

const WALK_DOORS: Box[] = [
  [26, 11, 27, 11], // lobby → bullpen
  [15, 11, 16, 11], // lobby → pink offices
  [42, 11, 43, 11], // kitchen → right offices
  [8, 15, 8, 16], // accounting → pink offices
  [3, 19, 3, 19], // accounting → records
  [8, 24, 8, 24], // records → pink offices
  [22, 20, 22, 21], // pink offices → bullpen
  [39, 13, 39, 14], // bullpen → right offices (above Michael's desk)
  [39, 22, 39, 23], // bullpen → right offices (between desk and reception)
  [27, 31, 27, 31], // bullpen → conference
  [13, 29, 14, 29], // pink offices → stairwell
  [3, 29, 3, 29], // records → small office
  [3, 35, 3, 35], // small office → lounge
  [8, 41, 8, 41], // lounge → stairwell
  [18, 38, 18, 39], // stairwell → conference
  [32, 42, 32, 43], // conference → restrooms
  [42, 31, 42, 31], // right offices → office/logo
  [45, 25, 45, 25], // right offices → annex
  [45, 40, 45, 40], // office/logo → annex
];

// Furniture footprints inside the walkable rooms — agents route around these
// instead of standing on desks and tables. Stand spots (station/desk/errand tiles)
// must sit on open floor beside a blocker, never on it.
const WALK_BLOCKERS: Box[] = [
  [24, 21, 28, 28], // bullpen desk pod (left)
  [31, 21, 35, 28], // bullpen desk pod (right)
  [40, 16, 43, 19], // Michael's desk
  [38, 24, 42, 27], // reception counter
  [22, 38, 28, 42], // conference table
  [37, 6, 41, 8], // break-room table
];

function inBox(x: number, y: number, [x0, y0, x1, y1]: Box): boolean {
  return x >= x0 && x <= x1 && y >= y0 && y <= y1;
}

export function createOfficeplaceOpenWalkGrid(): WalkGrid {
  const { width, height } = OFFICEPLACE_FLOOR;

  function walkable(x: number, y: number): boolean {
    if (WALK_BLOCKERS.some((b) => inBox(x, y, b))) return false;
    return (
      WALK_ROOMS.some((b) => inBox(x, y, b)) || WALK_DOORS.some((b) => inBox(x, y, b))
    );
  }

  return {
    width,
    height,
    isWalkable: walkable,
    isStandable: walkable,
  };
}
