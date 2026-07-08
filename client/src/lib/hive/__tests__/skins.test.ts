import { mapAgentToRole } from "../roleMap";
import { DEFAULT_HIVE_SKIN, HIVE_ENABLED_SKIN_IDS, HIVE_SKIN_IDS, HIVE_SKINS, isHiveSkinId, normalizeHiveSkinId, resolveHiveSkinId, skinLabel } from "../skins";

describe("hive skins", () => {
  it("includes four skins with complete semantic labels", () => {
    expect(HIVE_SKIN_IDS).toEqual(["runeplace", "officeplace", "netplace", "starplace"]);
    expect(DEFAULT_HIVE_SKIN).toBe("officeplace");

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
    expect(isHiveSkinId("runeplace")).toBe(true);
    expect(isHiveSkinId("nope")).toBe(false);
  });

  it("maps legacy skin ids to renamed ids", () => {
    expect(normalizeHiveSkinId("dunder_mifflin")).toBe("officeplace");
    expect(normalizeHiveSkinId("warcraft")).toBe("runeplace");
    expect(normalizeHiveSkinId("cyberpunk")).toBe("netplace");
    expect(normalizeHiveSkinId("starcraft")).toBe("starplace");
    expect(normalizeHiveSkinId("officespace")).toBe("officeplace");
    expect(normalizeHiveSkinId("runespace")).toBe("runeplace");
    expect(normalizeHiveSkinId("netspace")).toBe("netplace");
    expect(normalizeHiveSkinId("unknown")).toBeNull();
  });

  it("resolves legacy skin ids for labels", () => {
    expect(skinLabel("officespace", "planner_hq")).toBe("Regional Manager");
    expect(skinLabel("dunder_mifflin", "waiting")).toBe("Coffee Machine");
    expect(resolveHiveSkinId("officespace")).toBe("officeplace");
  });

  it("clamps disabled skins to the default", () => {
    expect(resolveHiveSkinId("runeplace")).toBe("officeplace");
    expect(resolveHiveSkinId("netplace")).toBe("officeplace");
    expect(HIVE_ENABLED_SKIN_IDS).toEqual(["officeplace"]);
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
