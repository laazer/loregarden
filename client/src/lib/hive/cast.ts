import type { HiveSkinId, HiveStationId } from "./skins";

/** Named character sprites. Only the officeplace skin ships a named cast. */
export type HiveCharacterId =
  | "cobel"
  | "ryan"
  | "ms_casey"
  | "oscar"
  | "kevin"
  | "kelly"
  | "andy"
  | "mark"
  | "helly"
  | "irving"
  | "dylan"
  | "milchick"
  | "burt";

export const HIVE_CHARACTER_IDS: HiveCharacterId[] = [
  "cobel",
  "ryan",
  "ms_casey",
  "oscar",
  "kevin",
  "kelly",
  "andy",
  "mark",
  "helly",
  "irving",
  "dylan",
  "milchick",
  "burt",
];

/**
 * Officeplace staffs a station with a crew rather than one worker, so a single
 * running agent puts its whole team on the floor — the MDR four appear together
 * whenever a testing agent runs.
 *
 * The first member is the lead: it carries the agent's status card and drives
 * artifact flights. The rest are colleagues, present but silent, so one agent
 * still produces exactly one card and one flight.
 */
const OFFICEPLACE_CREW: Record<HiveStationId, HiveCharacterId[]> = {
  planner_hq: ["cobel", "ryan"],
  research: ["ms_casey", "oscar"],
  coding: ["kevin", "kelly", "andy"],
  testing: ["mark", "helly", "irving", "dylan"],
  deploy: ["milchick", "burt"],
};

/** Crew for a station, or null for skins that draw a single generic worker. */
export function crewForStation(
  skin: HiveSkinId,
  station: HiveStationId,
): HiveCharacterId[] | null {
  return skin === "officeplace" ? OFFICEPLACE_CREW[station] : null;
}
