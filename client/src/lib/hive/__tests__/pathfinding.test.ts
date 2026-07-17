import { createOpenWalkGrid, findPathTiles, pathCrossesBlocked } from "../pathfinding";
import { createOfficeplaceOpenWalkGrid, getWalkGridForSkin } from "../layouts";
import {
  OFFICEPLACE_MAP,
  OFFICEPLACE_RECEPTIONIST,
  OFFICEPLACE_STATIONS,
} from "../layouts/officeplaceLayout";

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

  it("routes from reception to the conference room without crossing walls", () => {
    const grid = createOfficeplaceOpenWalkGrid();
    const from = OFFICEPLACE_RECEPTIONIST;
    const to = OFFICEPLACE_STATIONS.research;
    const path = findPathTiles({ x: from.x, y: from.y }, to, grid);
    expect(path[0]).toEqual({ x: from.x, y: from.y });
    expect(path[path.length - 1]).toEqual(to);
    expect(pathCrossesBlocked(path, grid)).toBe(false);
  });

  it("leaves reception through a doorway rather than straight across the walls", () => {
    const grid = createOfficeplaceOpenWalkGrid();
    const from = OFFICEPLACE_RECEPTIONIST;
    const to = OFFICEPLACE_STATIONS.research;
    const path = findPathTiles({ x: from.x, y: from.y }, to, grid);
    const manhattan = Math.abs(to.x - from.x) + Math.abs(to.y - from.y);
    // A straight line would punch through the room walls; the real route detours via doors.
    expect(path.length).toBeGreaterThan(manhattan);
  });
});

describe("getWalkGridForSkin", () => {
  it("returns officeplace collision grid for officeplace", () => {
    const grid = getWalkGridForSkin("officeplace");
    expect(grid.width).toBe(OFFICEPLACE_MAP.width);
    expect(grid.height).toBe(OFFICEPLACE_MAP.height);
    expect(grid.isWalkable(0, 0)).toBe(false); // outer shell
  });
});
