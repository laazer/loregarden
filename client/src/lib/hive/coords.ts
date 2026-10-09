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

/**
 * Map tile center as a `translate` offset in container units (`cqw`/`cqh`).
 *
 * The same point as `tilePercent`, for an element that moves with `translate`
 * instead of `left`/`top`: a percentage in `translate` is of the element's own
 * box, so the floor's size has to come from its query container.
 */
export function tileTranslate(tile: TilePoint, map: { width: number; height: number }): string {
  const x = ((tile.x + 0.5) / map.width) * 100;
  const y = ((tile.y + 0.5) / map.height) * 100;
  return `${x}cqw ${y}cqh`;
}
