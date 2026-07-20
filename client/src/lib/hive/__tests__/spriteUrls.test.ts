import { assertAllSkinSpritesResolve, resolveSkinSprites } from "../spriteUrls";
import { HIVE_SKIN_IDS } from "../skins";
import { HIVE_CHARACTER_IDS } from "../cast";
import { HIVE_MANIFEST_DATA } from "../manifestData";

describe("resolveSkinSprites", () => {
  it("resolves every semantic asset for all skins", () => {
    expect(() => assertAllSkinSpritesResolve()).not.toThrow();
    for (const skin of HIVE_SKIN_IDS) {
      const sprites = resolveSkinSprites(skin);
      expect(sprites.floor).toBeTruthy();
      if (skin === "officeplace") {
        // The office renders from a baked background image; the drawn floor-plan
        // layer is suppressed when scenery is present.
        expect(sprites.scenery).toBeTruthy();
      }
      expect(sprites.agent.implementer).toBeTruthy();
      expect(sprites.station.coding).toBeTruthy();
      expect(sprites.artifact.context).toBeTruthy();
      expect(sprites.event.error).toBeTruthy();
    }
  });
});

describe("officeplace cast manifest", () => {
  it("maps every crew character to its own asset", () => {
    const cast = HIVE_MANIFEST_DATA.officeplace.cast ?? {};
    const paths = Object.values(cast);
    expect(paths).toHaveLength(HIVE_CHARACTER_IDS.length);
    // One sprite per character — a duplicated path means two colleagues would
    // render as the same person on the floor.
    expect(new Set(paths).size).toBe(paths.length);
  });

  it("declares a sprite for every character id", () => {
    const cast = HIVE_MANIFEST_DATA.officeplace.cast ?? {};
    for (const id of HIVE_CHARACTER_IDS) {
      expect(cast[id]).toBeTruthy();
    }
  });
});
