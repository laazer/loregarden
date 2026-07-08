export const HIVE_SKIN_IDS = ["warcraft", "dunder_mifflin", "cyberpunk", "starcraft"] as const;

export type HiveSkinId = (typeof HIVE_SKIN_IDS)[number];

export const DEFAULT_HIVE_SKIN: HiveSkinId = "dunder_mifflin";

export type HiveStationId = "planner_hq" | "research" | "coding" | "testing" | "deploy";

export type HiveArtifactKind = "context" | "diff";

export type HiveEventKind = "waiting" | "error";

export type HiveSemanticKey =
  | "agent"
  | HiveStationId
  | HiveArtifactKind
  | HiveEventKind;

export interface HiveSkinLabels {
  id: HiveSkinId;
  label: string;
  floorTitle: string;
  agent: string;
  planner_hq: string;
  research: string;
  coding: string;
  testing: string;
  deploy: string;
  context: string;
  diff: string;
  waiting: string;
  error: string;
}

export const HIVE_SKINS: Record<HiveSkinId, HiveSkinLabels> = {
  warcraft: {
    id: "warcraft",
    label: "Warcraft",
    floorTitle: "Battleground",
    agent: "Peasant/Hero",
    planner_hq: "King/Archmage",
    research: "Mage Tower",
    coding: "Blacksmith",
    testing: "Training Grounds",
    deploy: "Castle",
    context: "Scroll",
    diff: "Blueprint",
    waiting: "Campfire",
    error: "Building on Fire",
  },
  dunder_mifflin: {
    id: "dunder_mifflin",
    label: "Dunder Mifflin",
    floorTitle: "Dunder Mifflin floor",
    agent: "Employee",
    planner_hq: "Regional Manager",
    research: "Library",
    coding: "Cubicle",
    testing: "QA Office",
    deploy: "Shipping",
    context: "Binder",
    diff: "Stack of Papers",
    waiting: "Coffee Machine",
    error: "HR Meeting",
  },
  cyberpunk: {
    id: "cyberpunk",
    label: "Cyberpunk",
    floorTitle: "Night City",
    agent: "Edgerunner",
    planner_hq: "Fixer",
    research: "Netrunner",
    coding: "Cyberdeck",
    testing: "Combat Simulator",
    deploy: "Drop Point",
    context: "Data Shard",
    diff: "Code Chip",
    waiting: "Smoking",
    error: "System Crash",
  },
  starcraft: {
    id: "starcraft",
    label: "StarCraft",
    floorTitle: "Command deck",
    agent: "SCV/Ghost/Marine",
    planner_hq: "Command Center",
    research: "Science Facility",
    coding: "Engineering Bay",
    testing: "Test Range",
    deploy: "Starport",
    context: "Data Crystal",
    diff: "Hologram",
    waiting: "Standing By",
    error: "Zerg Attack Alarm",
  },
};

export const HIVE_STATION_IDS: HiveStationId[] = [
  "planner_hq",
  "research",
  "coding",
  "testing",
  "deploy",
];

export function isHiveSkinId(value: string): value is HiveSkinId {
  return (HIVE_SKIN_IDS as readonly string[]).includes(value);
}

export function skinLabel(skin: HiveSkinId, key: HiveSemanticKey): string {
  return HIVE_SKINS[skin][key];
}
