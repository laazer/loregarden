import type { WalkGrid } from "../pathfinding";
import { createOpenWalkGrid } from "../pathfinding";
import type { HiveSkinId, HiveStationId } from "../skins";
import {
  OFFICEPLACE_DESKS,
  OFFICEPLACE_DOORS,
  OFFICEPLACE_ERRANDS,
  OFFICEPLACE_FLOOR_DESKS,
  OFFICEPLACE_MAP,
  OFFICEPLACE_MDR_STAFF,
  OFFICEPLACE_PROPS,
  OFFICEPLACE_RECEPTIONIST,
  OFFICEPLACE_ROOMS,
  OFFICEPLACE_STATIONS,
  OFFICEPLACE_WAITING,
  OFFICEPLACE_ZONES,
  type FloorDesk,
  type FloorDoor,
  type FloorProp,
  type FloorRoom,
  type HiveLayoutZone,
  type HiveOfficeErrand,
  type HiveOfficeReceptionist,
  type HiveOfficeResident,
} from "./officeplaceLayout";
import { createOfficeplaceOpenWalkGrid } from "./walkGrid";

export interface HiveLayout {
  map: { tileSize: number; width: number; height: number };
  stationPositions: Record<HiveStationId, { x: number; y: number }>;
  waitingPosition: { x: number; y: number };
  deskRow: readonly { x: number; y: number }[];
  scenery?: string;
  zones: HiveLayoutZone[];
  errands: HiveOfficeErrand[];
  receptionist: HiveOfficeReceptionist | null;
  /** Static placeholder NPCs pinned to a room (e.g. MDR QA staff). */
  mdrStaff: HiveOfficeResident[];
  hideStationSprites: boolean;
  walkGrid: WalkGrid;
  /** Drawn floor plan — empty for skins that use baked scenery art. */
  rooms: FloorRoom[];
  floorDesks: FloorDesk[];
  floorProps: FloorProp[];
  doors: FloorDoor[];
}

const DEFAULT_MAP = { tileSize: 16, width: 40, height: 28 } as const;

const DEFAULT_STATIONS: Record<HiveStationId, { x: number; y: number }> = {
  planner_hq: { x: 20, y: 3 },
  research: { x: 6, y: 10 },
  coding: { x: 20, y: 12 },
  testing: { x: 34, y: 10 },
  deploy: { x: 20, y: 22 },
};

const DEFAULT_WAITING = { x: 8, y: 20 };

const DEFAULT_DESKS = [
  { x: 10, y: 16 },
  { x: 14, y: 16 },
  { x: 18, y: 16 },
  { x: 22, y: 16 },
  { x: 26, y: 16 },
  { x: 30, y: 16 },
] as const;

const DEFAULT_LAYOUT: HiveLayout = {
  map: DEFAULT_MAP,
  stationPositions: DEFAULT_STATIONS,
  waitingPosition: DEFAULT_WAITING,
  deskRow: DEFAULT_DESKS,
  zones: [],
  errands: [],
  receptionist: null,
  mdrStaff: [],
  hideStationSprites: false,
  walkGrid: createOpenWalkGrid(DEFAULT_MAP.width, DEFAULT_MAP.height),
  rooms: [],
  floorDesks: [],
  floorProps: [],
  doors: [],
};

const OFFICEPLACE_LAYOUT: HiveLayout = {
  map: OFFICEPLACE_MAP,
  stationPositions: OFFICEPLACE_STATIONS,
  waitingPosition: OFFICEPLACE_WAITING,
  deskRow: OFFICEPLACE_DESKS,
  zones: OFFICEPLACE_ZONES,
  errands: OFFICEPLACE_ERRANDS,
  receptionist: OFFICEPLACE_RECEPTIONIST,
  mdrStaff: OFFICEPLACE_MDR_STAFF,
  hideStationSprites: true,
  walkGrid: createOfficeplaceOpenWalkGrid(),
  rooms: OFFICEPLACE_ROOMS,
  floorDesks: OFFICEPLACE_FLOOR_DESKS,
  floorProps: OFFICEPLACE_PROPS,
  doors: OFFICEPLACE_DOORS,
};

export function getWalkGridForSkin(skin: HiveSkinId): WalkGrid {
  return getHiveLayout(skin).walkGrid;
}

export function getHiveLayout(skin: HiveSkinId): HiveLayout {
  if (skin === "officeplace") return OFFICEPLACE_LAYOUT;
  return DEFAULT_LAYOUT;
}

export {
  type FloorDesk,
  type FloorDoor,
  type FloorProp,
  type FloorRoom,
  type HiveLayoutZone,
  type HiveOfficeErrand,
  type HiveOfficeReceptionist,
  type HiveOfficeResident,
} from "./officeplaceLayout";
export { createOfficeplaceOpenWalkGrid, createOfficeplaceWalkGrid } from "./walkGrid";
