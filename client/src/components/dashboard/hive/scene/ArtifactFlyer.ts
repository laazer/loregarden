import { Container, Sprite } from "pixi.js";

import type { HiveArtifactFlight } from "../../../../lib/hive/worldModel";
import type { HiveSkinTextures } from "./assets";
import { tileToWorld } from "./pathfinding";

interface ActiveFlight {
  id: string;
  triggerKey: string;
  sprite: Sprite;
  from: { x: number; y: number };
  to: { x: number; y: number };
  t: number;
  duration: number;
}

export class ArtifactFlyerLayer extends Container {
  private active: ActiveFlight[] = [];
  private seen = new Set<string>();

  spawn(flights: HiveArtifactFlight[], textures: HiveSkinTextures): void {
    for (const flight of flights) {
      if (this.seen.has(flight.triggerKey)) continue;
      this.seen.add(flight.triggerKey);

      const sprite = new Sprite(textures.artifact[flight.kind]);
      sprite.anchor.set(0.5);
      sprite.width = 16;
      sprite.height = 16;
      const from = tileToWorld(flight.from);
      const to = tileToWorld(flight.to);
      sprite.x = from.x;
      sprite.y = from.y;
      this.addChild(sprite);
      this.active.push({
        id: flight.id,
        triggerKey: flight.triggerKey,
        sprite,
        from,
        to,
        t: 0,
        duration: 1.15,
      });
    }
  }

  update(dt: number): void {
    const next: ActiveFlight[] = [];
    for (const flight of this.active) {
      flight.t += dt / flight.duration;
      const t = Math.min(1, flight.t);
      const ease = t * (2 - t);
      flight.sprite.x = Math.round(flight.from.x + (flight.to.x - flight.from.x) * ease);
      flight.sprite.y = Math.round(flight.from.y + (flight.to.y - flight.from.y) * ease - Math.sin(Math.PI * t) * 18);
      flight.sprite.alpha = t < 0.1 ? t / 0.1 : t > 0.85 ? (1 - t) / 0.15 : 1;
      if (t < 1) {
        next.push(flight);
      } else {
        this.removeChild(flight.sprite);
        flight.sprite.destroy();
      }
    }
    this.active = next;
  }

  resetSeen(): void {
    this.seen.clear();
  }
}
