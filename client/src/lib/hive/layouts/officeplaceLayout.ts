import type { HiveCharacterId } from "../cast";
import type { HiveStationId } from "../skins";
import { OFFICEPLACE_FLOOR } from "./officeplaceFloorPlan";

/** Office grid — 60×50 tiles, matching the floor-bg.png office image. */
export const OFFICEPLACE_MAP = OFFICEPLACE_FLOOR;

/**
 * NPC positions are aligned to the floor-bg.png office image, in 60×50 tile space
 * (tile ≈ image px / 16.6 horizontally, / 13.1 vertically — 998×656 over 60×50).
 * Read off a tile-grid overlay on the image and verified in the running app.
 */
export const OFFICEPLACE_STATIONS: Record<HiveStationId, { x: number; y: number }> = {
  planner_hq: { x: 42, y: 44 }, // manager's office, right of the conference room
  research: { x: 25, y: 44 }, // conference room, below the table
  coding: { x: 30, y: 20 }, // bullpen aisle, above the desk pods
  testing: { x: 52, y: 5 }, // MDR green room (now reachable via the elevator strip)
  deploy: { x: 43, y: 28 }, // front desk, beside the reception counter
};

export const OFFICEPLACE_WAITING = { x: 43, y: 9 }; // break room, by the coffee counter

/** Agent stand spots — bullpen aisles flanking the desk pods (which are blocked). */
export const OFFICEPLACE_DESKS = [
  { x: 26, y: 18 },
  { x: 30, y: 18 },
  { x: 34, y: 18 },
  { x: 26, y: 29 },
  { x: 30, y: 29 },
  { x: 34, y: 29 },
] as const;

export interface HiveLayoutZone {
  id: string;
  x: number;
  y: number;
  label: string;
}

/** Area labels for rooms the baked scenery doesn't name on its own. */
export const OFFICEPLACE_ZONES: HiveLayoutZone[] = [
  { id: "kitchen", x: 40, y: 6, label: "Kitchen" },
  { id: "break-room", x: 4, y: 40, label: "Break Room" },
  { id: "bathroom", x: 36, y: 43, label: "Bathroom" },
  { id: "annex", x: 52, y: 41, label: "The Annex" },
];

export interface HiveOfficeReceptionist {
  id: string;
  x: number;
  y: number;
  label: string;
  /** Full-body cast sprite. The bust-only Office art (worker.png) is retired. */
  character: HiveCharacterId;
}

/**
 * Pam at the curved reception counter, centre-right of the image — full-body art
 * from #76. She doubles as coding crew, so the static hides while she's out on
 * the floor with a coding agent.
 */
export const OFFICEPLACE_RECEPTIONIST: HiveOfficeReceptionist = {
  id: "receptionist",
  x: 43,
  y: 26,
  label: "Receptionist",
  character: "pam",
};

export interface HiveOfficeResident {
  id: string;
  x: number;
  y: number;
  label: string;
  /** Full-body cast sprite. The bust-only Office art (tester.png) is retired. */
  character: HiveCharacterId;
}

/**
 * The MDR four at their desks in the green room (top-right). These statics keep
 * the room staffed while no testing agent runs; HiveCssFloor hides any of them
 * whose character is already walking the floor as a testing-crew body.
 */
export const OFFICEPLACE_MDR_STAFF: HiveOfficeResident[] = [
  { id: "mdr-1", x: 50, y: 4, label: "Mark", character: "mark" },
  { id: "mdr-2", x: 54, y: 4, label: "Helly", character: "helly" },
  { id: "mdr-3", x: 50, y: 7, label: "Irving", character: "irving" },
  { id: "mdr-4", x: 54, y: 7, label: "Dylan", character: "dylan" },
];

export interface HiveOfficeErrand {
  id: string;
  stand: { x: number; y: number };
  label: string;
  /** Semantic image-space anchor on floor-bg.png (60×50). Not a path destination. */
  object: { id: string; label: string; tile: { x: number; y: number } };
}

/**
 * Idle errands — stand is the sole path destination; object.tile is a Chebyshev-1
 * image-space anchor read from floor-bg.png (not OFFICEPLACE_PROPS, not derived).
 */
export const OFFICEPLACE_ERRANDS: HiveOfficeErrand[] = [
  {
    id: "lobby-elevator",
    label: "Waiting for the lobby elevator",
    object: { id: "lobby-elevator-door", label: "Lobby elevator", tile: { x: 28, y: 5 } },
    stand: { x: 28, y: 6 },
  },
  {
    id: "kitchen-coffee",
    label: "Getting coffee in the kitchen",
    object: { id: "kitchen-counter", label: "Kitchen counter", tile: { x: 44, y: 5 } },
    stand: { x: 44, y: 6 },
  },
  {
    id: "break-room-table",
    label: "Tidying the break-room table",
    // break-room table, walkGrid WALK_BLOCKERS lower break-room box
    object: {
      id: "break-room-lower-table",
      label: "Break-room table",
      tile: { x: 4, y: 43 },
    },
    stand: { x: 4, y: 42 },
  },
  {
    id: "plant-watering",
    label: "Watering the lobby plant",
    object: { id: "lobby-west-plant", label: "Lobby plant", tile: { x: 1, y: 6 } },
    stand: { x: 2, y: 6 },
  },
  {
    id: "conference-exit",
    label: "Leaving the conference room",
    object: {
      id: "conference-east-exit",
      label: "Conference-room exit",
      tile: { x: 34, y: 39 },
    },
    stand: { x: 33, y: 40 },
  },
  {
    id: "annex-elevator",
    label: "Waiting for the annex elevator",
    object: { id: "annex-elevator-door", label: "Annex elevator", tile: { x: 55, y: 40 } },
    stand: { x: 54, y: 40 },
  },
  {
    id: "reception-counter",
    label: "Checking in at reception",
    // reception counter, walkGrid WALK_BLOCKERS [38,24,42,27]
    object: { id: "reception-counter", label: "Reception counter", tile: { x: 43, y: 27 } },
    stand: { x: 42, y: 28 },
  },
  {
    id: "stairwell-check",
    label: "Checking the stairwell",
    object: {
      id: "stairwell-lower-landing",
      label: "Stairwell landing",
      tile: { x: 18, y: 43 },
    },
    stand: { x: 17, y: 43 },
  },
];

export {
  OFFICEPLACE_DOORS,
  OFFICEPLACE_FLOOR_DESKS,
  OFFICEPLACE_PROPS,
  OFFICEPLACE_ROOMS,
  type FloorDesk,
  type FloorDoor,
  type FloorProp,
  type FloorRoom,
} from "./officeplaceFloorPlan";
