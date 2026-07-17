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
 * MDR room and the foundation strip are excluded — roaming agents never enter them.
 *
 * Rectangles are [x0, y0, x1, y1] inclusive, hand-aligned to the image and verified
 * against a walkability overlay; see scripts note in ATTRIBUTION.md.
 */
type Box = readonly [number, number, number, number];

const WALK_ROOMS: Box[] = [
  // top band
  [1, 1, 21, 8], // lobby
  [38, 1, 46, 8], // kitchen / break
  // left rooms + vertical corridor spine
  [1, 12, 7, 19], // accounting (top-left)
  [1, 23, 7, 28], // office (mid-left)
  [1, 32, 7, 43], // lounge
  [9, 12, 12, 43], // corridor
  [13, 12, 19, 27], // pink offices
  // central open plan
  [21, 12, 36, 27], // bullpen
  [39, 12, 45, 27], // reception + right offices
  // lower rooms
  [13, 30, 20, 43], // stairs
  [22, 32, 36, 43], // conference room
  [37, 32, 44, 43], // office + logo
  [47, 29, 57, 43], // white rooms / annex (below MDR)
];

const WALK_DOORS: Box[] = [
  [15, 8, 16, 12], // lobby → corridor
  [42, 8, 43, 12], // kitchen → reception
  [7, 15, 9, 16], // accounting → corridor
  [7, 25, 9, 26], // office → corridor
  [7, 36, 9, 37], // lounge → corridor
  [12, 18, 13, 19], // corridor → pink offices
  [19, 18, 21, 19], // pink offices → bullpen
  [36, 18, 39, 19], // bullpen → reception
  [28, 27, 29, 32], // bullpen → conference
  [11, 28, 15, 30], // corridor → stairs
  [40, 27, 41, 32], // reception → office/logo
  [44, 34, 47, 35], // office/logo → white rooms
];

function inBox(x: number, y: number, [x0, y0, x1, y1]: Box): boolean {
  return x >= x0 && x <= x1 && y >= y0 && y <= y1;
}

export function createOfficeplaceOpenWalkGrid(): WalkGrid {
  const { width, height } = OFFICEPLACE_FLOOR;

  function walkable(x: number, y: number): boolean {
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
