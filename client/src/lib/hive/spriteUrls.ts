import type { CSSProperties } from "react";

import type { HiveCharacterId } from "./cast";
import { HIVE_CHARACTER_IDS } from "./cast";
import type { HiveCastVariant } from "./roleMap";
import type { HiveArtifactKind, HiveEventKind, HiveSkinId, HiveStationId } from "./skins";
import { HIVE_SKIN_IDS, HIVE_STATION_IDS } from "./skins";
import { HIVE_MANIFESTS } from "./manifests";
import { HIVE_SPRITE_URL_MAP } from "./spriteUrlMap";

export interface HiveCssSpriteUrls {
  floor: string | null;
  scenery: string | null;
  agent: Record<HiveCastVariant, string | null>;
  /** Named cast, empty for skins that draw a single generic worker per role. */
  character: Partial<Record<HiveCharacterId, string | null>>;
  station: Record<HiveStationId, string | null>;
  artifact: Record<HiveArtifactKind, string | null>;
  event: Record<HiveEventKind, string | null>;
}

const CAST: HiveCastVariant[] = ["worker", "planner", "implementer", "tester", "reviewer"];

function resolveAssetUrl(relativeFromHiveRoot: string): string | null {
  return HIVE_SPRITE_URL_MAP[relativeFromHiveRoot] ?? null;
}

export function resolveSkinSprites(skin: HiveSkinId): HiveCssSpriteUrls {
  const manifest = HIVE_MANIFESTS[skin];
  const agent = {} as Record<HiveCastVariant, string | null>;
  for (const cast of CAST) {
    agent[cast] = resolveAssetUrl(manifest.agents[cast] ?? `${skin}/agents/${cast}.png`);
  }
  const station = {} as Record<HiveStationId, string | null>;
  for (const id of HIVE_STATION_IDS) {
    station[id] = resolveAssetUrl(manifest.stations[id] ?? `${skin}/stations/${id}.png`);
  }
  const character: Partial<Record<HiveCharacterId, string | null>> = {};
  for (const id of HIVE_CHARACTER_IDS) {
    const path = manifest.cast?.[id];
    if (path) character[id] = resolveAssetUrl(path);
  }
  return {
    floor: resolveAssetUrl(manifest.floor),
    scenery: manifest.scenery ? resolveAssetUrl(manifest.scenery) : null,
    agent,
    character,
    station,
    artifact: {
      context: resolveAssetUrl(manifest.artifacts.context),
      diff: resolveAssetUrl(manifest.artifacts.diff),
    },
    event: {
      waiting: resolveAssetUrl(manifest.events.waiting),
      error: resolveAssetUrl(manifest.events.error),
    },
  };
}

export function floorBackgroundStyle(
  floorUrl: string | null,
  sceneryUrl?: string | null,
): CSSProperties | undefined {
  if (sceneryUrl) {
    return { ["--hive-scenery-url" as string]: `url(${sceneryUrl})` };
  }
  if (!floorUrl) return undefined;
  return { ["--hive-floor-url" as string]: `url(${floorUrl})` };
}

export function assertAllSkinSpritesResolve(): void {
  for (const skin of HIVE_SKIN_IDS) {
    const sprites = resolveSkinSprites(skin);
    if (!sprites.floor) throw new Error(`missing floor for ${skin}`);
    for (const cast of CAST) {
      if (!sprites.agent[cast]) throw new Error(`missing agent ${skin}/${cast}`);
    }
    for (const id of HIVE_STATION_IDS) {
      if (!sprites.station[id]) throw new Error(`missing station ${skin}/${id}`);
    }
    // A skin need not declare a cast, but anything it does declare must load —
    // otherwise a typo'd path degrades to an invisible agent at runtime.
    for (const [id, url] of Object.entries(sprites.character)) {
      if (!url) throw new Error(`missing character ${skin}/${id}`);
    }
    if (!sprites.artifact.context || !sprites.artifact.diff) {
      throw new Error(`missing artifacts for ${skin}`);
    }
    if (!sprites.event.waiting || !sprites.event.error) {
      throw new Error(`missing events for ${skin}`);
    }
  }
}
