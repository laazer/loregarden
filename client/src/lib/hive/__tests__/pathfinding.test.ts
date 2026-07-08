import { createOpenWalkGrid, findPathTiles, nearestWalkableTile, pathCrossesBlocked } from "../pathfinding";
import { createOfficeplaceWalkGrid, getWalkGridForSkin } from "../layouts";

describe("findPathTiles", () => {
  const open = createOpenWalkGrid(40, 28);

  it("returns a path from desk to coding station on open grid", () => {
    const path = findPathTiles({ x: 10, y: 16 }, { x: 20, y: 12 }, open);
    expect(path.length).toBeGreaterThan(1);
    expect(path[0]).toEqual({ x: 10, y: 16 });
    expect(path[path.length - 1]).toEqual({ x: 20, y: 12 });
  });

  it("returns single point when already there", () => {
    expect(findPathTiles({ x: 5, y: 5 }, { x: 5, y: 5 }, open)).toEqual([{ x: 5, y: 5 }]);
  });

  it("routes around office walls for officeplace layout", () => {
    const grid = createOfficeplaceWalkGrid();
    const path = findPathTiles({ x: 2, y: 13 }, { x: 23, y: 7 }, grid);
    expect(path.length).toBeGreaterThan(4);
    expect(path[0]).toEqual({ x: 2, y: 13 });
    expect(path[path.length - 1]).toEqual({ x: 23, y: 7 });
    expect(pathCrossesBlocked(path, grid)).toBe(false);
  });

  it("does not shortcut through walls on failed direct line", () => {
    const grid = createOfficeplaceWalkGrid();
    const path = findPathTiles({ x: 2, y: 13 }, { x: 23, y: 7 }, grid);
    const manhattan = Math.abs(23 - 2) + Math.abs(7 - 13);
    expect(path.length).toBeGreaterThan(manhattan);
  });

  it("snaps southern goals off the bottom wall row", () => {
    const grid = createOfficeplaceWalkGrid();
    expect(grid.isStandable?.(16, 20)).toBe(false);
    expect(grid.isStandable?.(16, 19)).toBe(true);
    const goal = nearestWalkableTile(grid, { x: 16, y: 20 });
    expect(goal).toEqual({ x: 16, y: 19 });
    const path = findPathTiles({ x: 2, y: 13 }, { x: 16, y: 20 }, grid);
    expect(path[path.length - 1]).toEqual({ x: 16, y: 19 });
  });
});

describe("getWalkGridForSkin", () => {
  it("returns officeplace collision grid for officeplace", () => {
    const grid = getWalkGridForSkin("officeplace");
    expect(grid.width).toBe(34);
    expect(grid.isWalkable(2, 13)).toBe(true);
    expect(grid.isWalkable(0, 0)).toBe(false);
  });
});
