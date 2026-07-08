import { findPathTiles } from "../../../components/dashboard/hive/scene/pathfinding";
import { assertManifestCoverage, HIVE_MANIFESTS } from "../manifests";
import { HIVE_SKIN_IDS } from "../skins";

describe("findPathTiles", () => {
  it("returns a path from desk to coding station", () => {
    const path = findPathTiles({ x: 10, y: 16 }, { x: 20, y: 12 });
    expect(path.length).toBeGreaterThan(1);
    expect(path[0]).toEqual({ x: 10, y: 16 });
    expect(path[path.length - 1]).toEqual({ x: 20, y: 12 });
  });

  it("returns single point when already there", () => {
    expect(findPathTiles({ x: 5, y: 5 }, { x: 5, y: 5 })).toEqual([{ x: 5, y: 5 }]);
  });
});

describe("hive skin manifests", () => {
  it("covers all semantic keys for every skin", () => {
    expect(() => assertManifestCoverage()).not.toThrow();
    expect(Object.keys(HIVE_MANIFESTS).sort()).toEqual([...HIVE_SKIN_IDS].sort());
  });
});
