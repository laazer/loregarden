import { assertAllSkinSpritesResolve, resolveSkinSprites } from "../spriteUrls";
import { HIVE_SKIN_IDS } from "../skins";

describe("resolveSkinSprites", () => {
  it("resolves every semantic asset for all skins", () => {
    expect(() => assertAllSkinSpritesResolve()).not.toThrow();
    for (const skin of HIVE_SKIN_IDS) {
      const sprites = resolveSkinSprites(skin);
      expect(sprites.floor).toBeTruthy();
      if (skin === "officeplace") {
        expect(sprites.scenery).toBeTruthy();
      }
      expect(sprites.agent.implementer).toBeTruthy();
      expect(sprites.station.coding).toBeTruthy();
      expect(sprites.artifact.context).toBeTruthy();
      expect(sprites.event.error).toBeTruthy();
    }
  });
});
