import { getHiveLayout } from "../layouts";
import { OFFICEPLACE_MAP } from "../layouts/officeplaceLayout";

describe("hive layouts", () => {
  it("uses officeplace office coordinates for officeplace skin", () => {
    const layout = getHiveLayout("officeplace");
    expect(layout.map).toEqual(OFFICEPLACE_MAP);
    expect(layout.stationPositions.planner_hq).toEqual({ x: 3, y: 4 });
    expect(layout.stationPositions.coding).toEqual({ x: 12, y: 13 });
    expect(layout.waitingPosition).toEqual({ x: 26, y: 20 });
    expect(layout.deskRow).toHaveLength(6);
    expect(layout.scenery).toBe("officeplace/scenery.png");
    expect(layout.errands.length).toBeGreaterThan(0);
    expect(layout.walkGrid.isWalkable(2, 13)).toBe(true);
    expect(layout.hideStationSprites).toBe(true);
    expect(layout.zones.some((z) => z.label === "Conference room")).toBe(true);
  });

  it("keeps generic layout for other skins", () => {
    const layout = getHiveLayout("runeplace");
    expect(layout.map.width).toBe(40);
    expect(layout.scenery).toBeUndefined();
    expect(layout.hideStationSprites).toBe(false);
  });
});
