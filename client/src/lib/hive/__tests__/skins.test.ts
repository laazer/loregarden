import { mapAgentToRole } from "../roleMap";
import { DEFAULT_HIVE_SKIN, HIVE_SKIN_IDS, HIVE_SKINS, isHiveSkinId } from "../skins";

describe("hive skins", () => {
  it("includes four skins with complete semantic labels", () => {
    expect(HIVE_SKIN_IDS).toEqual(["warcraft", "dunder_mifflin", "cyberpunk", "starcraft"]);
    expect(DEFAULT_HIVE_SKIN).toBe("dunder_mifflin");

    for (const id of HIVE_SKIN_IDS) {
      const skin = HIVE_SKINS[id];
      expect(skin.agent).toBeTruthy();
      expect(skin.planner_hq).toBeTruthy();
      expect(skin.research).toBeTruthy();
      expect(skin.coding).toBeTruthy();
      expect(skin.testing).toBeTruthy();
      expect(skin.deploy).toBeTruthy();
      expect(skin.context).toBeTruthy();
      expect(skin.diff).toBeTruthy();
      expect(skin.waiting).toBeTruthy();
      expect(skin.error).toBeTruthy();
      expect(skin.floorTitle).toBeTruthy();
    }
  });

  it("validates skin ids", () => {
    expect(isHiveSkinId("warcraft")).toBe(true);
    expect(isHiveSkinId("nope")).toBe(false);
  });
});

describe("mapAgentToRole", () => {
  it.each([
    ["planner", "planner_hq", "planner"],
    ["spec", "planner_hq", "planner"],
    ["retriever", "research", "worker"],
    ["backend_implementer", "coding", "implementer"],
    ["frontend_implementer", "coding", "implementer"],
    ["core_simulation", "coding", "implementer"],
    ["static_qa", "testing", "tester"],
    ["test_breaker", "testing", "tester"],
    ["gatekeeper", "deploy", "reviewer"],
    ["architecture_reviewer", "deploy", "reviewer"],
    ["mystery_bot", "coding", "implementer"],
  ] as const)("maps %s → %s / %s", (agentId, station, cast) => {
    expect(mapAgentToRole(agentId)).toEqual({ station, cast });
  });
});
