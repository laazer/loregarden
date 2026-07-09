import { Application, Container, Graphics, Sprite, Text, TextStyle, Texture } from "pixi.js";

import type { HiveSkinId } from "../../../../lib/hive/skins";
import type { HiveWorldModel } from "../../../../lib/hive/worldModel";
import { HIVE_MAP } from "../../../../lib/hive/worldModel";
import { loadSkinTextures, type HiveSkinTextures } from "./assets";
import { ArtifactFlyerLayer } from "./ArtifactFlyer";
import { CharacterView } from "./Character";
import { StationView } from "./Station";
import { tileToWorld } from "./pathfinding";

function placeholderTextures(): HiveSkinTextures {
  const white = Texture.WHITE;
  return {
    floor: white,
    agent: {
      worker: white,
      planner: white,
      implementer: white,
      tester: white,
      reviewer: white,
    },
    station: {
      planner_hq: white,
      research: white,
      coding: white,
      testing: white,
      deploy: white,
    },
    artifact: { context: white, diff: white },
    event: { waiting: white, error: white },
  };
}

const SKIN_FLOOR_COLORS: Record<HiveSkinId, { a: number; b: number; path: number }> = {
  runeplace: { a: 0x3d5c2e, b: 0x2f4624, path: 0x8b7355 },
  officeplace: { a: 0xc9b896, b: 0xb8a682, path: 0x8a8f98 },
  netplace: { a: 0x1a1028, b: 0x120c1c, path: 0x3a2658 },
  starplace: { a: 0x2a2e28, b: 0x1e221c, path: 0x4a5240 },
};

export class OfficeFloor {
  readonly root = new Container();
  private world = new Container();
  private floorLayer = new Container();
  private stationLayer = new Container();
  private characterLayer = new Container();
  private fxLayer = new Container();
  private stations = new Map<string, StationView>();
  private characters = new Map<string, CharacterView>();
  private flyers = new ArtifactFlyerLayer();
  private waitingProp = new Container();
  private textures: HiveSkinTextures | null = null;
  private skin: HiveSkinId | null = null;
  private godLabel: Text | null = null;
  private waitingVisible = false;
  private waitingLabel = "";
  private destroyed = false;

  constructor() {
    this.root.addChild(this.world);
    this.world.addChild(this.floorLayer);
    this.world.addChild(this.stationLayer);
    this.world.addChild(this.waitingProp);
    this.world.addChild(this.characterLayer);
    this.world.addChild(this.fxLayer);
    this.fxLayer.addChild(this.flyers);
  }

  async setSkin(skin: HiveSkinId): Promise<void> {
    if (this.destroyed) return;
    if (this.skin === skin && this.textures) return;
    this.skin = skin;
    const textures = await loadSkinTextures(skin);
    if (this.destroyed || this.skin !== skin) return;
    this.textures = textures;
    this.rebuildFloor(skin);
    this.flyers.resetSeen();
    this.waitingVisible = false;
    this.waitingLabel = "";
  }

  /** Synchronous geometric floor — used before textures finish loading. */
  mountPlaceholderFloor(skin: HiveSkinId): void {
    if (this.destroyed) return;
    this.rebuildFloor(skin);
  }

  sync(model: HiveWorldModel): void {
    if (this.destroyed) return;
    // Allow geometric sync even before textures; stations/agents use WHITE then.
    const textures = this.textures ?? placeholderTextures();

    for (const station of model.stations) {
      let view = this.stations.get(station.id);
      if (!view) {
        view = new StationView(station, textures);
        this.stations.set(station.id, view);
        this.stationLayer.addChild(view);
      }
      const errorOverlay = model.events.some(
        (e) => e.kind === "error" && e.stationId === station.id,
      );
      view.sync(station, textures, errorOverlay);
    }

    const liveIds = new Set(model.agents.map((a) => a.id));
    for (const [id, view] of this.characters) {
      if (!liveIds.has(id)) {
        this.characterLayer.removeChild(view);
        view.destroy({ children: true });
        this.characters.delete(id);
      }
    }

    for (const agent of model.agents) {
      let view = this.characters.get(agent.id);
      if (!view) {
        view = new CharacterView(agent, textures, model.layout.walkGrid);
        this.characters.set(agent.id, view);
        this.characterLayer.addChild(view);
      }
      view.sync(agent, textures, model.layout.walkGrid);
    }

    this.flyers.spawn(model.flights, textures);
    this.syncWaiting(model, textures);
    this.syncGod(model);
  }

  update(dt: number): void {
    if (this.destroyed) return;
    for (const view of this.characters.values()) {
      view.update(dt, true);
    }
    this.flyers.update(dt);
  }

  fitToView(width: number, height: number): void {
    const mapW = HIVE_MAP.width * HIVE_MAP.tileSize;
    const mapH = HIVE_MAP.height * HIVE_MAP.tileSize;
    const scale = Math.min(width / mapW, height / mapH) * 0.92;
    this.world.scale.set(scale);
    this.world.x = Math.round((width - mapW * scale) / 2);
    this.world.y = Math.round((height - mapH * scale) / 2);
  }

  destroy(): void {
    this.destroyed = true;
    this.stations.clear();
    this.characters.clear();
    this.root.destroy({ children: true });
  }

  private rebuildFloor(skin: HiveSkinId): void {
    this.floorLayer.removeChildren().forEach((c) => c.destroy());
    const colors = SKIN_FLOOR_COLORS[skin];
    const g = new Graphics();
    // Two big checker fills instead of 1000+ per-tile draw calls.
    const mapW = HIVE_MAP.width * HIVE_MAP.tileSize;
    const mapH = HIVE_MAP.height * HIVE_MAP.tileSize;
    g.rect(0, 0, mapW, mapH).fill(colors.a);
    for (let y = 0; y < HIVE_MAP.height; y += 1) {
      for (let x = (y % 2); x < HIVE_MAP.width; x += 2) {
        g.rect(x * HIVE_MAP.tileSize, y * HIVE_MAP.tileSize, HIVE_MAP.tileSize, HIVE_MAP.tileSize).fill(
          colors.b,
        );
      }
    }
    g.rect(18 * HIVE_MAP.tileSize, 0, 4 * HIVE_MAP.tileSize, mapH).fill({
      color: colors.path,
      alpha: 0.35,
    });
    g.rect(0, 11 * HIVE_MAP.tileSize, mapW, 3 * HIVE_MAP.tileSize).fill({
      color: colors.path,
      alpha: 0.35,
    });
    this.floorLayer.addChild(g);

    if (this.textures?.floor) {
      const floorSprite = new Sprite(this.textures.floor);
      floorSprite.width = mapW;
      floorSprite.height = mapH;
      floorSprite.alpha = 0.25;
      this.floorLayer.addChild(floorSprite);
    }
  }

  private syncWaiting(model: HiveWorldModel, textures: HiveSkinTextures): void {
    const { visible, label, x, y } = model.waitingProp;
    if (!visible) {
      this.waitingProp.visible = false;
      this.waitingVisible = false;
      return;
    }

    if (
      this.waitingVisible &&
      this.waitingLabel === label &&
      this.waitingProp.children.length > 0
    ) {
      this.waitingProp.visible = true;
      return;
    }

    this.waitingProp.removeChildren().forEach((c) => c.destroy());
    const world = tileToWorld({ x, y });
    const sprite = new Sprite(textures.event.waiting);
    sprite.anchor.set(0.5, 0.7);
    sprite.width = 28;
    sprite.height = 28;
    sprite.x = world.x;
    sprite.y = world.y;
    const text = new Text({
      text: label,
      style: new TextStyle({ fontFamily: "monospace", fontSize: 8, fill: 0xffd27a }),
    });
    text.anchor.set(0.5, 0);
    text.x = world.x;
    text.y = world.y + 12;
    this.waitingProp.addChild(sprite, text);
    this.waitingProp.visible = true;
    this.waitingVisible = true;
    this.waitingLabel = label;
  }

  private syncGod(model: HiveWorldModel): void {
    if (!this.godLabel) {
      this.godLabel = new Text({
        text: "",
        style: new TextStyle({
          fontFamily: "monospace",
          fontSize: 10,
          fill: 0x2dd4a7,
          fontWeight: "bold",
        }),
      });
      this.godLabel.anchor.set(0.5, 1);
      this.stationLayer.addChild(this.godLabel);
    }
    const hq = tileToWorld({ x: 20, y: 3 });
    this.godLabel.x = hq.x;
    this.godLabel.y = hq.y - 36;
    this.godLabel.text = model.orchestratorLabel;
    this.godLabel.alpha = model.orchestratorActive ? 1 : 0.55;
  }
}

export async function createHiveApplication(host: HTMLElement): Promise<{
  app: Application;
  floor: OfficeFloor;
}> {
  const width = Math.max(1, host.clientWidth || 640);
  const height = Math.max(1, host.clientHeight || 440);
  const app = new Application();

  const initOpts = {
    background: "#0b0f14",
    antialias: false,
    resolution: 1,
    autoDensity: false,
    width,
    height,
  } as const;

  try {
    await app.init({ ...initOpts, preference: "webgl" });
  } catch {
    try {
      await app.init({ ...initOpts, preference: "webgpu" });
    } catch {
      await app.init({ ...initOpts });
    }
  }

  host.replaceChildren();
  host.appendChild(app.canvas);
  app.canvas.className = "hive-panel__canvas";
  app.canvas.style.width = "100%";
  app.canvas.style.height = "100%";
  app.canvas.style.display = "block";
  app.canvas.style.imageRendering = "pixelated";

  app.ticker.autoStart = false;
  app.ticker.stop();
  const floor = new OfficeFloor();
  floor.mountPlaceholderFloor("officeplace");
  app.stage.addChild(floor.root);
  return { app, floor };
}
