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
  planner_hq: { x: 43, y: 15 }, // Michael's office (upper right)
  research: { x: 26, y: 37 }, // conference room (long table, lower centre)
  coding: { x: 28, y: 21 }, // bullpen (central desk clusters)
  testing: { x: 52, y: 5 }, // the annex desk cluster (green room, top right)
  deploy: { x: 42, y: 26 }, // entrance, below reception
};

export const OFFICEPLACE_WAITING = { x: 41, y: 4 }; // break room / kitchen (top centre)

/** Agent desk seats — the sales bullpen clusters in the image. */
export const OFFICEPLACE_DESKS = [
  { x: 24, y: 19 },
  { x: 28, y: 19 },
  { x: 32, y: 20 },
  { x: 26, y: 24 },
  { x: 30, y: 24 },
  { x: 34, y: 22 },
] as const;

export interface HiveLayoutZone {
  id: string;
  x: number;
  y: number;
  label: string;
}

/** Rooms draw their own name plates, so the officeplace skin needs no zone overlays. */
export const OFFICEPLACE_ZONES: HiveLayoutZone[] = [];

export interface HiveOfficeReceptionist {
  id: string;
  x: number;
  y: number;
  label: string;
}

/** Pam, at the curved reception desk in the centre-right of the image. */
export const OFFICEPLACE_RECEPTIONIST: HiveOfficeReceptionist = {
  id: "receptionist",
  x: 45,
  y: 25,
  label: "Receptionist",
};

export interface HiveOfficeResident {
  id: string;
  x: number;
  y: number;
  label: string;
}

/**
 * Placeholder QA staff who live in the MDR room (green, top-right). Roaming office
 * agents never path here; these are static stand-ins until dedicated QA NPCs land.
 * Seated at the four MDR desks around the testing station.
 */
export const OFFICEPLACE_MDR_STAFF: HiveOfficeResident[] = [
  { id: "mdr-1", x: 50, y: 4, label: "QA" },
  { id: "mdr-2", x: 54, y: 4, label: "QA" },
  { id: "mdr-3", x: 50, y: 7, label: "QA" },
  { id: "mdr-4", x: 54, y: 7, label: "QA" },
];

export interface HiveOfficeErrand {
  id: string;
  stand: { x: number; y: number };
  label: string;
}

/** Idle errands — each stands on an open floor tile in the image. */
export const OFFICEPLACE_ERRANDS: HiveOfficeErrand[] = [
  { id: "lobby", stand: { x: 10, y: 4 }, label: "In the lobby" },
  { id: "kitchen", stand: { x: 44, y: 4 }, label: "In the kitchen" },
  { id: "lounge", stand: { x: 4, y: 40 }, label: "In the lounge" },
  { id: "bullpen-walk", stand: { x: 30, y: 22 }, label: "Crossing the bullpen" },
  { id: "conference", stand: { x: 24, y: 38 }, label: "Leaving the conference room" },
  { id: "annex", stand: { x: 50, y: 40 }, label: "Waiting on the elevator" },
  { id: "reception-chat", stand: { x: 39, y: 24 }, label: "Loitering at reception" },
  { id: "corridor", stand: { x: 10, y: 20 }, label: "Down the corridor" },
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
