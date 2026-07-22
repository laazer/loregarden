export const HIVE_SKIN_IDS = ["runeplace", "officeplace", "netplace", "starplace"] as const;

export type HiveSkinId = (typeof HIVE_SKIN_IDS)[number];

export const DEFAULT_HIVE_SKIN: HiveSkinId = "officeplace";

/** Skins with complete assets — others stay in catalog but are not selectable yet. */
export const HIVE_ENABLED_SKIN_IDS: readonly HiveSkinId[] = ["officeplace"];

/** Legacy persisted skin ids from before pseudo-legal renames. */
const LEGACY_HIVE_SKIN_ALIASES: Record<string, HiveSkinId> = {
  warcraft: "runeplace",
  dunder_mifflin: "officeplace",
  cyberpunk: "netplace",
  starcraft: "starplace",
  runespace: "runeplace",
  officespace: "officeplace",
  netspace: "netplace",
};

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
  runeplace: {
    id: "runeplace",
    label: "The Runeplace",
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
  officeplace: {
    id: "officeplace",
    label: "The Officeplace",
    floorTitle: "Officeplace floor",
    agent: "Employee",
    planner_hq: "Regional Manager",
    research: "Conference Room",
    coding: "Cubicle",
    testing: "QA Office",
    deploy: "Front Desk",
    context: "Binder",
    diff: "Stack of Papers",
    waiting: "Coffee Machine",
    error: "HR Meeting",
  },
  netplace: {
    id: "netplace",
    label: "The Netplace",
    floorTitle: "Neon district",
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
  starplace: {
    id: "starplace",
    label: "The Starplace",
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
    error: "Attack Alarm",
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

export function normalizeHiveSkinId(value: string): HiveSkinId | null {
  if (isHiveSkinId(value)) return value;
  return LEGACY_HIVE_SKIN_ALIASES[value] ?? null;
}

/** Resolve any persisted or legacy skin string to a valid, enabled catalog id. */
export function resolveHiveSkinId(value: string | HiveSkinId | null | undefined): HiveSkinId {
  if (!value) return DEFAULT_HIVE_SKIN;
  const resolved = normalizeHiveSkinId(value) ?? DEFAULT_HIVE_SKIN;
  if ((HIVE_ENABLED_SKIN_IDS as readonly string[]).includes(resolved)) return resolved;
  return DEFAULT_HIVE_SKIN;
}

export function skinLabel(skin: HiveSkinId | string, key: HiveSemanticKey): string {
  return HIVE_SKINS[resolveHiveSkinId(skin)][key];
}
