import type { HiveSkinId, HiveStationId } from "./skins";
import { HIVE_SKIN_IDS, HIVE_STATION_IDS } from "./skins";
import type { HiveCastVariant } from "./roleMap";
import { HIVE_MANIFEST_DATA, type HiveManifestJson } from "./manifestData";

export type { HiveManifestJson };

export const HIVE_MANIFESTS: Record<HiveSkinId, HiveManifestJson> = HIVE_MANIFEST_DATA;

const CAST: HiveCastVariant[] = ["worker", "planner", "implementer", "tester", "reviewer"];

export function assertManifestCoverage(): void {
  for (const id of HIVE_SKIN_IDS) {
    const m = HIVE_MANIFESTS[id];
    for (const cast of CAST) {
      if (!m.agents[cast]) throw new Error(`missing agent ${id}/${cast}`);
    }
    for (const station of HIVE_STATION_IDS as HiveStationId[]) {
      if (!m.stations[station]) throw new Error(`missing station ${id}/${station}`);
    }
    if (!m.artifacts.context || !m.artifacts.diff) throw new Error(`missing artifacts ${id}`);
    if (!m.events.waiting || !m.events.error) throw new Error(`missing events ${id}`);
  }
}
