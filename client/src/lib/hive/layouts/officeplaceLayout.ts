import type { HiveStationId } from "../skins";

/** officeplace office.tmj — 34×22 tiles @ 16px */
export const OFFICEPLACE_MAP = {
  tileSize: 16,
  width: 34,
  height: 22,
} as const;

/** Spawn points and zones from office.tmj / themeRegistry */
export const OFFICEPLACE_STATIONS: Record<HiveStationId, { x: number; y: number }> = {
  planner_hq: { x: 3, y: 4 }, // desk-ceo
  research: { x: 13, y: 5 }, // boardroom zone center
  coding: { x: 12, y: 13 }, // bullpen pc row
  testing: { x: 23, y: 7 }, // war room
  deploy: { x: 16, y: 20 }, // entrance / shipping
};

export const OFFICEPLACE_WAITING = { x: 26, y: 20 }; // café coffee machine

/** Agent desk seats — pc-1 … pc-6 bullpen row */
export const OFFICEPLACE_DESKS = [
  { x: 2, y: 13 },
  { x: 6, y: 13 },
  { x: 10, y: 13 },
  { x: 14, y: 13 },
  { x: 18, y: 13 },
  { x: 22, y: 13 },
] as const;

export interface HiveLayoutZone {
  id: string;
  x: number;
  y: number;
  label: string;
}

/** Zone labels for baked office scenery (furniture is in scenery.png). */
export const OFFICEPLACE_ZONES: HiveLayoutZone[] = [
  { id: "boardroom", x: 13, y: 3, label: "Conference room" },
  { id: "reception", x: 16, y: 19, label: "Reception" },
  { id: "break-room", x: 28, y: 14, label: "Break room" },
  { id: "bullpen", x: 12, y: 11, label: "Bullpen" },
];

export const OFFICEPLACE_SCENERY = "officeplace/scenery.png";

export interface HiveOfficeErrand {
  id: string;
  stand: { x: number; y: number };
  label: string;
}

/** Idle errands from office themeRegistry (worker-accessible). */
export const OFFICEPLACE_ERRANDS: HiveOfficeErrand[] = [
  { id: "plant-left", stand: { x: 2, y: 20 }, label: "Watering plant" },
  { id: "plant-right", stand: { x: 22, y: 20 }, label: "Watering plant" },
  { id: "plant-cafe", stand: { x: 30, y: 20 }, label: "Watering plant" },
  { id: "window-left", stand: { x: 10, y: 3 }, label: "Staring out the window" },
  { id: "window-right", stand: { x: 15, y: 3 }, label: "Staring out the window" },
  { id: "cooler", stand: { x: 16, y: 3 }, label: "At the water cooler" },
  { id: "coffee", stand: { x: 26, y: 20 }, label: "Coffee run" },
  { id: "fridge", stand: { x: 29, y: 20 }, label: "Raiding the fridge" },
  { id: "shelf", stand: { x: 30, y: 20 }, label: "Snack shelf" },
  { id: "bin-entrance", stand: { x: 18, y: 20 }, label: "Trash toss" },
  { id: "bin-cafe", stand: { x: 31, y: 16 }, label: "Trash toss" },
];
