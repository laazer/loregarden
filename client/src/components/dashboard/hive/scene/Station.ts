import { Container, Graphics, Sprite, Text, TextStyle } from "pixi.js";

import type { HiveStationState } from "../../../../lib/hive/worldModel";
import { HIVE_MAP } from "../../../../lib/hive/worldModel";
import type { HiveSkinTextures } from "./assets";
import { tileToWorld } from "./pathfinding";

const LABEL_STYLE = new TextStyle({
  fontFamily: "monospace",
  fontSize: 9,
  fill: 0xe8eef5,
  align: "center",
});

export class StationView extends Container {
  readonly stationId: string;
  private sprite: Sprite;
  private glow: Graphics;
  private label: Text;
  private overlay: Sprite | null = null;

  constructor(
    station: HiveStationState,
    textures: HiveSkinTextures,
  ) {
    super();
    this.stationId = station.id;
    const world = tileToWorld({ x: station.x, y: station.y });
    this.x = world.x;
    this.y = world.y;

    this.glow = new Graphics();
    this.addChild(this.glow);

    this.sprite = new Sprite(textures.station[station.id]);
    this.sprite.anchor.set(0.5, 0.7);
    this.sprite.width = HIVE_MAP.tileSize * 3;
    this.sprite.height = HIVE_MAP.tileSize * 3;
    this.addChild(this.sprite);

    this.label = new Text({ text: station.label, style: LABEL_STYLE });
    this.label.anchor.set(0.5, 0);
    this.label.y = HIVE_MAP.tileSize * 0.9;
    this.addChild(this.label);
  }

  sync(station: HiveStationState, textures: HiveSkinTextures, errorOverlay: boolean): void {
    this.sprite.texture = textures.station[station.id];
    this.label.text = station.label;

    this.glow.clear();
    if (station.active) {
      this.glow.circle(0, 4, 22).fill({ color: 0x2dd4a7, alpha: 0.22 });
      this.sprite.tint = 0xffffff;
    } else {
      this.sprite.tint = 0xb0b8c4;
    }

    if (errorOverlay) {
      if (!this.overlay) {
        this.overlay = new Sprite(textures.event.error);
        this.overlay.anchor.set(0.5, 0.5);
        this.overlay.width = 28;
        this.overlay.height = 28;
        this.overlay.y = -28;
        this.addChild(this.overlay);
      }
      this.overlay.texture = textures.event.error;
      this.overlay.visible = true;
    } else if (this.overlay) {
      this.overlay.visible = false;
    }
  }
}
