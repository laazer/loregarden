export {
  createOpenWalkGrid,
  findPathTiles,
  nearestWalkableTile,
  pathCrossesBlocked,
  type TilePoint,
  type WalkGrid,
} from "../../../../lib/hive/pathfinding";

import { HIVE_MAP } from "../../../../lib/hive/worldModel";
import type { TilePoint } from "../../../../lib/hive/pathfinding";

/** @deprecated Use layout map tileSize */
export function tileToWorld(tile: TilePoint): { x: number; y: number } {
  return {
    x: tile.x * HIVE_MAP.tileSize + HIVE_MAP.tileSize / 2,
    y: tile.y * HIVE_MAP.tileSize + HIVE_MAP.tileSize / 2,
  };
}
