import type { HiveCharacterId } from "../cast";
import type { HiveStationId } from "../skins";
import { OFFICEPLACE_FLOOR } from "./officeplaceFloorPlan";

/** Office grid — 60×50 tiles, matching the floor-bg.png office image. */
export const OFFICEPLACE_MAP = OFFICEPLACE_FLOOR;

/**
 * NPC positions are aligned to the floor-bg.png office image, in 60×50 tile space
 * (tile ≈ image px / 16.6 horizontally, / 10.4 vertically). Read off a tile-grid
 * overlay on the image and verified in the running app.
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

/** Front desk, at the curved reception counter in the centre-right of the image. */
export const OFFICEPLACE_RECEPTIONIST: HiveOfficeReceptionist = {
  id: "receptionist",
  x: 43,
  y: 26,
  label: "Receptionist",
  character: "ms_casey",
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
}

/** Idle errands — each stands on an open floor tile in the image. */
export const OFFICEPLACE_ERRANDS: HiveOfficeErrand[] = [
  { id: "lobby", stand: { x: 10, y: 6 }, label: "In the lobby" },
  { id: "kitchen", stand: { x: 43, y: 8 }, label: "In the kitchen" },
  { id: "break-room", stand: { x: 4, y: 42 }, label: "In the break room" },
  { id: "bullpen-walk", stand: { x: 30, y: 27 }, label: "Crossing the bullpen" },
  { id: "conference", stand: { x: 27, y: 43 }, label: "Leaving the conference room" },
  { id: "annex", stand: { x: 52, y: 42 }, label: "Waiting on the elevator" },
  { id: "reception-chat", stand: { x: 42, y: 28 }, label: "Loitering at reception" },
  { id: "corridor", stand: { x: 13, y: 38 }, label: "Down the stairwell" },
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
