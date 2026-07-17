import { getHiveLayout } from "../layouts";
import {
  OFFICEPLACE_MAP,
  OFFICEPLACE_STATIONS,
  OFFICEPLACE_WAITING,
} from "../layouts/officeplaceLayout";

describe("hive layouts", () => {
  it("uses officeplace office coordinates for officeplace skin", () => {
    const layout = getHiveLayout("officeplace");
    expect(layout.map).toEqual(OFFICEPLACE_MAP);
    expect(layout.stationPositions.planner_hq).toEqual(OFFICEPLACE_STATIONS.planner_hq);
    expect(layout.stationPositions.coding).toEqual(OFFICEPLACE_STATIONS.coding);
    expect(layout.waitingPosition).toEqual(OFFICEPLACE_WAITING);
    expect(layout.deskRow).toHaveLength(6);
    expect(layout.errands.length).toBeGreaterThan(0);
    expect(layout.hideStationSprites).toBe(true);
  });

  it("draws the Scranton floor plan from data instead of baked scenery", () => {
    const layout = getHiveLayout("officeplace");
    expect(layout.scenery).toBeUndefined();
    expect(layout.rooms.some((r) => r.id === "reception")).toBe(true);
    expect(layout.rooms.some((r) => r.id === "annex")).toBe(true);
    expect(layout.floorDesks.some((d) => d.id === "pam")).toBe(true);
  });

  it("keeps generic layout for other skins", () => {
    const layout = getHiveLayout("runeplace");
    expect(layout.map.width).toBe(40);
    expect(layout.scenery).toBeUndefined();
    expect(layout.hideStationSprites).toBe(false);
  });
});
