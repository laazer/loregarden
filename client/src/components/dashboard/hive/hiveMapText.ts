/**
 * The words and marks the Hive map shows, in one place.
 *
 * Every mark here is decoration: it is rendered `aria-hidden`, and its meaning goes into the
 * row's `aria-label` as words, so a screen reader reads "failed" and never "✕". The strings
 * are pinned by `HiveAgentMap.test.tsx` because each one is a different situation, and
 * collapsing any two of them is how an unobservable run came to read as an idle one.
 */

import { MAP_PLACES, MAP_STATIONS, type MapPlaceId } from "../../../lib/hive/agentMap";
import type { ToolActState } from "../../../lib/hive/toolActs";

const RING_PLACES = new Set<MapPlaceId>(MAP_STATIONS.map((station) => station.id));
const PLACE_LABEL = new Map<MapPlaceId, string>(MAP_PLACES.map((place) => [place.id, place.label]));

/** Whether this place is drawn on the ring at all. The desk is the agent's own point, so no. */
export function isRingPlace(place: MapPlaceId): boolean {
  return RING_PLACES.has(place);
}

export function placeLabel(place: MapPlaceId): string {
  return PLACE_LABEL.get(place) ?? place;
}

/** The mark beside an act. Four states, four marks, none of them read aloud. */
export const STATE_MARK: Record<ToolActState, string> = {
  running: "●",
  done: "✓",
  failed: "✕",
  unknown: "?",
};

/**
 * What a run's now-line reads when its own status has taken the floor. `RunStatus` is
 * queued/running/awaiting_permission/succeeded/failed/cancelled — there is no `completed`, so
 * nothing here renders one.
 */
export const STATUS_NOW_LINE: Record<string, string> = {
  succeeded: "✓ succeeded",
  failed: "✕ failed",
  cancelled: "✕ cancelled",
  awaiting_permission: "⏳ waiting for you — awaiting permission",
};

/**
 * Why this run's tool calls cannot be seen. Two different causes, two different sentences: an
 * adapter that writes no call lines, and a run whose `command` recorded no adapter at all
 * (790 of 6,211 runs). Neither is idleness and neither is failure.
 */
export function unknownActivity(adapter: string): string {
  return adapter === ""
    ? "activity unknown — adapter not recorded"
    : `activity unknown — the ${adapter} adapter does not log tool calls`;
}

/** The same line with every mark taken out, for a label a screen reader reads as words. */
export function inWords(line: string): string {
  return line.replace(/[●✕✓⏳?]/g, "").trim();
}

export function initials(name: string): string {
  return name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase() ?? "")
    .join("");
}
