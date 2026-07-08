import type { WalkGrid } from "../pathfinding";
import { createOpenWalkGrid } from "../pathfinding";
import type { HiveSkinId, HiveStationId } from "../skins";
import {
  OFFICEPLACE_DESKS,
  OFFICEPLACE_ERRANDS,
  OFFICEPLACE_MAP,
  OFFICEPLACE_SCENERY,
  OFFICEPLACE_STATIONS,
  OFFICEPLACE_WAITING,
  OFFICEPLACE_ZONES,
  type HiveLayoutZone,
  type HiveOfficeErrand,
} from "./officeplaceLayout";
import { createOfficeplaceWalkGrid } from "./walkGrid";

export interface HiveLayout {
  map: { tileSize: number; width: number; height: number };
  stationPositions: Record<HiveStationId, { x: number; y: number }>;
  waitingPosition: { x: number; y: number };
  deskRow: readonly { x: number; y: number }[];
  scenery?: string;
  zones: HiveLayoutZone[];
  errands: HiveOfficeErrand[];
  hideStationSprites: boolean;
  walkGrid: WalkGrid;
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
  hideStationSprites: false,
  walkGrid: createOpenWalkGrid(DEFAULT_MAP.width, DEFAULT_MAP.height),
};

const OFFICEPLACE_LAYOUT: HiveLayout = {
  map: OFFICEPLACE_MAP,
  stationPositions: OFFICEPLACE_STATIONS,
  waitingPosition: OFFICEPLACE_WAITING,
  deskRow: OFFICEPLACE_DESKS,
  scenery: OFFICEPLACE_SCENERY,
  zones: OFFICEPLACE_ZONES,
  errands: OFFICEPLACE_ERRANDS,
  hideStationSprites: true,
  walkGrid: createOfficeplaceWalkGrid(),
};

export function getWalkGridForSkin(skin: HiveSkinId): WalkGrid {
  return getHiveLayout(skin).walkGrid;
}

export function getHiveLayout(skin: HiveSkinId): HiveLayout {
  if (skin === "officeplace") return OFFICEPLACE_LAYOUT;
  return DEFAULT_LAYOUT;
}

export { type HiveLayoutZone, type HiveOfficeErrand } from "./officeplaceLayout";
export { createOfficeplaceWalkGrid } from "./walkGrid";
