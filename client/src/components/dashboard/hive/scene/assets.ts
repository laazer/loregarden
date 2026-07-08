import { Assets, Texture } from "pixi.js";

import type { HiveCastVariant } from "../../../../lib/hive/roleMap";
import type { HiveArtifactKind, HiveEventKind, HiveSkinId, HiveStationId } from "../../../../lib/hive/skins";
import { HIVE_STATION_IDS } from "../../../../lib/hive/skins";
import { HIVE_MANIFESTS } from "../../../../lib/hive/manifests";

export interface HiveSkinTextures {
  floor: Texture;
  agent: Record<HiveCastVariant, Texture>;
  station: Record<HiveStationId, Texture>;
  artifact: Record<HiveArtifactKind, Texture>;
  event: Record<HiveEventKind, Texture>;
}

const CAST: HiveCastVariant[] = ["worker", "planner", "implementer", "tester", "reviewer"];

const textureCache = new Map<HiveSkinId, HiveSkinTextures>();
const textureInflight = new Map<HiveSkinId, Promise<HiveSkinTextures>>();

const PNG_URLS: Record<string, string> = import.meta.glob("../../../../assets/hive/**/*.png", {
  eager: true,
  import: "default",
  query: "?url",
}) as Record<string, string>;

function resolveUrl(relativeFromHiveRoot: string): string | null {
  const suffix = relativeFromHiveRoot.replace(/^\//, "");
  for (const [path, url] of Object.entries(PNG_URLS)) {
    const normalized = path.replace(/\\/g, "/");
    if (normalized.endsWith(`/hive/${suffix}`) || normalized.endsWith(`/${suffix}`)) {
      return url;
    }
  }
  return null;
}

async function loadTexture(rel: string): Promise<Texture> {
  const url = resolveUrl(rel);
  if (!url) return Texture.WHITE;
  try {
    return await Assets.load<Texture>(url);
  } catch {
    return Texture.WHITE;
  }
}

async function loadSkinTexturesUncached(skin: HiveSkinId): Promise<HiveSkinTextures> {
  const manifest = HIVE_MANIFESTS[skin];
  const agentEntries = await Promise.all(
    CAST.map(async (cast) => {
      const rel = manifest.agents[cast] ?? `${skin}/agents/${cast}.png`;
      return [cast, await loadTexture(rel)] as const;
    }),
  );
  const stationEntries = await Promise.all(
    HIVE_STATION_IDS.map(async (id) => {
      const rel = manifest.stations[id] ?? `${skin}/stations/${id}.png`;
      return [id, await loadTexture(rel)] as const;
    }),
  );

  return {
    floor: await loadTexture(manifest.floor),
    agent: Object.fromEntries(agentEntries) as Record<HiveCastVariant, Texture>,
    station: Object.fromEntries(stationEntries) as Record<HiveStationId, Texture>,
    artifact: {
      context: await loadTexture(manifest.artifacts.context),
      diff: await loadTexture(manifest.artifacts.diff),
    },
    event: {
      waiting: await loadTexture(manifest.events.waiting),
      error: await loadTexture(manifest.events.error),
    },
  };
}

export async function loadSkinTextures(skin: HiveSkinId): Promise<HiveSkinTextures> {
  const cached = textureCache.get(skin);
  if (cached) return cached;
  const inflight = textureInflight.get(skin);
  if (inflight) return inflight;
  const promise = loadSkinTexturesUncached(skin).then((textures) => {
    textureCache.set(skin, textures);
    textureInflight.delete(skin);
    return textures;
  });
  textureInflight.set(skin, promise);
  return promise;
}
