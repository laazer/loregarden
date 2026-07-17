/**
 * Dunder Mifflin Scranton floor plan, as data.
 *
 * Replaces the baked scenery.png import pipeline: rooms, desks and doors are
 * declared here in tile space, and both the rendered floor and the walk grid are
 * derived from them. Editing a rect here moves the room, its walls and the
 * pathfinding blockers together.
 *
 * Scale: one tile is one 16px tileset cell. A desk sprite is 48×32, so desks are
 * 3×2 tiles and a person is ~1 tile wide — keep new furniture on that footing or
 * it will read out of proportion against the agents.
 */

export type FloorRoomKind = "office" | "service" | "open";

export interface FloorRoom {
  id: string;
  label: string;
  /** Tile rect. For walled rooms the perimeter tiles are the wall band. */
  x: number;
  y: number;
  w: number;
  h: number;
  /** "open" rooms are label-only areas with no walls (bullpen, hallway). */
  kind: FloorRoomKind;
}

export interface FloorDesk {
  id: string;
  label: string;
  /** Desk footprint — blocked for pathfinding. */
  x: number;
  y: number;
  w: number;
  h: number;
  /** Where the occupant stands. Must be walkable and outside the footprint. */
  seat: { x: number; y: number };
  /** Wrap-around desks block a ring open at the front, enclosing the seat. */
  wrap?: boolean;
  /** Key into OFFICEPLACE_PROP_URLS; defaults to the wooden desk. */
  sprite?: string;
}

/** A gap punched through a room's wall band. */
export interface FloorDoor {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A piece of furniture. Decorative unless `solid`, which also blocks pathfinding. */
export interface FloorProp {
  id: string;
  /** Key into OFFICEPLACE_PROP_URLS. */
  sprite: string;
  x: number;
  y: number;
  w: number;
  h: number;
  solid?: boolean;
  /** Degrees clockwise. The rect is the rotated footprint. */
  rotate?: 0 | 90 | 180 | 270;
}

export const OFFICEPLACE_FLOOR = {
  tileSize: 16,
  width: 60,
  height: 50,
} as const;

export const OFFICEPLACE_ROOMS: FloorRoom[] = [
  { id: "supply-closet", label: "Supplies", x: 0, y: 1, w: 5, h: 16, kind: "service" },
  { id: "michaels-office", label: "Michael's Office", x: 5, y: 1, w: 17, h: 13, kind: "office" },
  { id: "conference-room", label: "Conference Room", x: 23, y: 1, w: 16, h: 13, kind: "office" },
  { id: "break-room", label: "Break Room", x: 45, y: 1, w: 14, h: 14, kind: "service" },
  { id: "reception", label: "Reception", x: 0, y: 18, w: 14, h: 10, kind: "open" },
  { id: "accounting", label: "Accounting", x: 0, y: 30, w: 18, h: 18, kind: "office" },
  { id: "annex", label: "The Annex", x: 45, y: 17, w: 14, h: 31, kind: "office" },
  { id: "restrooms", label: "Restrooms", x: 33, y: 39, w: 10, h: 9, kind: "service" },
  { id: "bullpen", label: "Bullpen", x: 19, y: 15, w: 20, h: 32, kind: "open" },
  { id: "hallway", label: "Hallway", x: 39, y: 1, w: 6, h: 38, kind: "open" },
];

export const OFFICEPLACE_DOORS: FloorDoor[] = [
  { x: 1, y: 16, w: 3, h: 1 }, // supply closet → front corridor
  { x: 21, y: 8, w: 1, h: 3 }, // michael's office → bullpen
  { x: 29, y: 13, w: 3, h: 1 }, // conference room → bullpen
  { x: 50, y: 14, w: 3, h: 1 }, // break room → hallway
  { x: 17, y: 36, w: 1, h: 3 }, // accounting → bullpen
  { x: 45, y: 30, w: 1, h: 3 }, // annex → hallway
  { x: 37, y: 39, w: 3, h: 1 }, // restrooms → hallway
];

export const OFFICEPLACE_FLOOR_DESKS: FloorDesk[] = [
  { id: "michael", label: "Michael", x: 12, y: 4, w: 3, h: 2, seat: { x: 13, y: 6 } },
  // Pam's reception desk: a ring open at the front, drawn by the counter run in
  // DECOR below rather than one sprite, so it reads as a real wrap-around desk.
  { id: "pam", label: "Pam", x: 3, y: 20, w: 6, h: 4, seat: { x: 5, y: 22 }, wrap: true },
  { id: "angela", label: "Angela", x: 3, y: 33, w: 3, h: 2, seat: { x: 4, y: 35 }, sprite: "desk-grey" },
  { id: "oscar", label: "Oscar", x: 9, y: 33, w: 3, h: 2, seat: { x: 10, y: 35 }, sprite: "desk-grey" },
  { id: "kevin", label: "Kevin", x: 3, y: 39, w: 3, h: 2, seat: { x: 4, y: 41 }, sprite: "desk-grey" },
  { id: "jim", label: "Jim", x: 21, y: 19, w: 3, h: 2, seat: { x: 22, y: 21 } },
  { id: "dwight", label: "Dwight", x: 28, y: 19, w: 3, h: 2, seat: { x: 29, y: 21 } },
  { id: "phyllis", label: "Phyllis", x: 21, y: 26, w: 3, h: 2, seat: { x: 22, y: 28 } },
  { id: "stanley", label: "Stanley", x: 28, y: 26, w: 3, h: 2, seat: { x: 29, y: 28 } },
  { id: "meredith", label: "Meredith", x: 21, y: 33, w: 3, h: 2, seat: { x: 22, y: 35 } },
  { id: "creed", label: "Creed", x: 28, y: 33, w: 3, h: 2, seat: { x: 29, y: 35 } },
  { id: "toby", label: "Toby", x: 48, y: 20, w: 3, h: 2, seat: { x: 49, y: 22 } },
  { id: "ryan", label: "Ryan", x: 48, y: 27, w: 3, h: 2, seat: { x: 49, y: 29 } },
  { id: "kelly", label: "Kelly", x: 48, y: 34, w: 3, h: 2, seat: { x: 49, y: 36 } },
];

/** Furniture. Desks carry their own art; these dress the rest of each room. */
const DECOR: FloorProp[] = [
  // Supply closet
  { id: "supply-shelf-a", sprite: "shelf", x: 1, y: 2, w: 3, h: 2, solid: true },
  { id: "supply-shelf-b", sprite: "shelf", x: 1, y: 6, w: 3, h: 2, solid: true },
  { id: "supply-boxes", sprite: "boxes", x: 1, y: 12, w: 2, h: 2, solid: true },

  // Michael's office
  { id: "michael-painting", sprite: "painting", x: 12, y: 2, w: 2, h: 1 },
  { id: "michael-couch", sprite: "couch", x: 7, y: 10, w: 3, h: 2, solid: true },
  { id: "michael-plant", sprite: "plant-big", x: 19, y: 11, w: 1, h: 1 },

  // Conference room
  { id: "conf-whiteboard", sprite: "whiteboard", x: 30, y: 2, w: 2, h: 1 },
  { id: "conf-table", sprite: "table-long", x: 28, y: 5, w: 6, h: 3, solid: true },
  { id: "conf-chair-a", sprite: "chair", x: 29, y: 4, w: 1, h: 1 },
  { id: "conf-chair-b", sprite: "chair", x: 32, y: 4, w: 1, h: 1 },
  { id: "conf-chair-c", sprite: "chair", x: 29, y: 9, w: 1, h: 1 },
  { id: "conf-chair-d", sprite: "chair", x: 32, y: 9, w: 1, h: 1 },
  { id: "conf-plant", sprite: "plant-big", x: 25, y: 11, w: 1, h: 1 },

  // Break room
  { id: "break-fridge", sprite: "fridge", x: 46, y: 2, w: 2, h: 2, solid: true },
  { id: "break-vending", sprite: "vending", x: 49, y: 2, w: 2, h: 2, solid: true },
  { id: "break-coffee", sprite: "coffee", x: 52, y: 2, w: 1, h: 2, solid: true },
  { id: "break-table", sprite: "table-long", x: 48, y: 7, w: 4, h: 2, solid: true },
  { id: "break-chair-a", sprite: "chair", x: 49, y: 6, w: 1, h: 1 },
  { id: "break-chair-b", sprite: "chair", x: 51, y: 6, w: 1, h: 1 },
  { id: "break-chair-c", sprite: "chair", x: 49, y: 9, w: 1, h: 1 },
  { id: "break-chair-d", sprite: "chair", x: 51, y: 9, w: 1, h: 1 },
  { id: "break-plant", sprite: "plant-big", x: 56, y: 12, w: 1, h: 1 },

  // Reception — Pam's wrap-around counter: a back run with a wing turned in each side.
  { id: "pam-counter-l", sprite: "desk-wood", x: 3, y: 20, w: 3, h: 2 },
  { id: "pam-counter-r", sprite: "desk-wood", x: 6, y: 20, w: 3, h: 2 },
  { id: "pam-wing-l", sprite: "desk-wood", x: 3, y: 22, w: 2, h: 3, rotate: 90 },
  { id: "pam-wing-r", sprite: "desk-wood", x: 7, y: 22, w: 2, h: 3, rotate: 270 },
  { id: "reception-couch", sprite: "couch", x: 1, y: 25, w: 3, h: 2, solid: true },
  { id: "reception-plant", sprite: "plant-big", x: 12, y: 19, w: 1, h: 1 },

  // Accounting
  { id: "accounting-shelf", sprite: "shelf", x: 14, y: 44, w: 3, h: 2, solid: true },
  { id: "accounting-plant", sprite: "plant-small", x: 1, y: 31, w: 1, h: 1 },

  // The Annex
  { id: "annex-shelf", sprite: "shelf", x: 54, y: 18, w: 3, h: 2, solid: true },
  { id: "annex-plant", sprite: "plant-big", x: 46, y: 45, w: 1, h: 1 },

  // Restrooms
  { id: "restroom-toilet-a", sprite: "toilet", x: 35, y: 41, w: 1, h: 1, solid: true },
  { id: "restroom-toilet-b", sprite: "toilet", x: 39, y: 41, w: 1, h: 1, solid: true },

  // Bullpen / hallway dressing
  { id: "bullpen-plant", sprite: "plant-big", x: 36, y: 16, w: 1, h: 1 },
  { id: "hallway-plant", sprite: "plant-small", x: 41, y: 4, w: 1, h: 1 },
];

/**
 * Every desk gets a monitor on its surface and a chair on its seat, so the desk
 * list stays the single source of truth for who sits where.
 */
const DESK_DRESSING: FloorProp[] = OFFICEPLACE_FLOOR_DESKS.flatMap((desk): FloorProp[] => {
  if (desk.wrap) return [{ id: `${desk.id}-monitor`, sprite: "monitor", x: desk.x + 4, y: desk.y + 1, w: 1, h: 1 }];
  return [
    { id: `${desk.id}-monitor`, sprite: "monitor", x: desk.x + 1, y: desk.y, w: 1, h: 1 },
    { id: `${desk.id}-chair`, sprite: "chair", x: desk.seat.x, y: desk.seat.y, w: 1, h: 1 },
  ];
});

export const OFFICEPLACE_PROPS: FloorProp[] = [...DECOR, ...DESK_DRESSING];

function inRect(x: number, y: number, r: { x: number; y: number; w: number; h: number }): boolean {
  return x >= r.x && x < r.x + r.w && y >= r.y && y < r.y + r.h;
}

/** Perimeter tile of a walled room — the wall band itself. */
function onRoomWall(x: number, y: number, room: FloorRoom): boolean {
  if (room.kind === "open") return false;
  if (!inRect(x, y, room)) return false;
  return x === room.x || x === room.x + room.w - 1 || y === room.y || y === room.y + room.h - 1;
}

export function isDoor(x: number, y: number): boolean {
  return OFFICEPLACE_DOORS.some((d) => inRect(x, y, d));
}

/**
 * Desk blockers. A plain desk is a solid block; a wrap-around desk is a ring open
 * at the front, so the occupant's seat sits in the hole and can be walked into.
 */
function inDeskFootprint(x: number, y: number, desk: FloorDesk): boolean {
  if (!inRect(x, y, desk)) return false;
  if (!desk.wrap) return true;
  if (y === desk.y + desk.h - 1) return false; // open front edge
  return x === desk.x || x === desk.x + desk.w - 1 || y === desk.y;
}

/** True when the tile is a wall, a desk footprint, or outside the floor outline. */
export function isFloorBlocked(x: number, y: number): boolean {
  const { width, height } = OFFICEPLACE_FLOOR;
  if (x < 0 || y < 0 || x >= width || y >= height) return true;
  // Outer shell of the building.
  if (x === 0 || y === 0 || x === width - 1 || y === height - 1) return true;
  if (isDoor(x, y)) return false;
  if (OFFICEPLACE_ROOMS.some((room) => onRoomWall(x, y, room))) return true;
  if (OFFICEPLACE_FLOOR_DESKS.some((desk) => inDeskFootprint(x, y, desk))) return true;
  if (OFFICEPLACE_PROPS.some((prop) => prop.solid && inRect(x, y, prop))) return true;
  return false;
}

export function findRoomAt(x: number, y: number): FloorRoom | null {
  // Walled rooms win over the open areas they may overlap.
  const walled = OFFICEPLACE_ROOMS.find((r) => r.kind !== "open" && inRect(x, y, r));
  if (walled) return walled;
  return OFFICEPLACE_ROOMS.find((r) => inRect(x, y, r)) ?? null;
}
