/** Tile-space point (integer or fractional grid coordinates). */
export type TilePoint = { x: number; y: number };

/** Map tile center as CSS percentage — aligns sprites with baked scenery cells. */
export function tilePercent(
  tile: TilePoint,
  map: { width: number; height: number },
): { left: string; top: string } {
  return {
    left: `${((tile.x + 0.5) / map.width) * 100}%`,
    top: `${((tile.y + 0.5) / map.height) * 100}%`,
  };
}
