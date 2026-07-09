import { Container, Graphics, Sprite, Text, TextStyle } from "pixi.js";

import type { HiveAgentState } from "../../../../lib/hive/worldModel";
import type { HiveSkinTextures } from "./assets";
import { findPathTiles, tileToWorld, type TilePoint, type WalkGrid } from "./pathfinding";

const NAME_STYLE = new TextStyle({
  fontFamily: "monospace",
  fontSize: 8,
  fill: 0xd7dee8,
  align: "center",
});

export class CharacterView extends Container {
  readonly agentId: string;
  private body: Sprite;
  private shadow: Graphics;
  private nameLabel: Text;
  private toolBubble: Text;
  private path: TilePoint[] = [];
  private pathIndex = 0;
  private bob = 0;
  private tilePos: TilePoint;
  private lastTarget: TilePoint | null = null;

  constructor(agent: HiveAgentState, textures: HiveSkinTextures, grid: WalkGrid) {
    super();
    this.agentId = agent.id;
    this.tilePos = { ...agent.desk };

    this.shadow = new Graphics();
    this.shadow.ellipse(0, 10, 10, 4).fill({ color: 0x000000, alpha: 0.35 });
    this.addChild(this.shadow);

    this.body = new Sprite(textures.agent[agent.cast] ?? textures.agent.worker);
    this.body.anchor.set(0.5, 0.85);
    this.body.width = 28;
    this.body.height = 28;
    this.addChild(this.body);

    this.nameLabel = new Text({ text: agent.name, style: NAME_STYLE });
    this.nameLabel.anchor.set(0.5, 0);
    this.nameLabel.y = 12;
    this.addChild(this.nameLabel);

    this.toolBubble = new Text({
      text: "",
      style: new TextStyle({ fontFamily: "monospace", fontSize: 8, fill: 0x4b9bff }),
    });
    this.toolBubble.anchor.set(0.5, 1);
    this.toolBubble.y = -22;
    this.toolBubble.visible = false;
    this.addChild(this.toolBubble);

    this.applyWorldPos();
    this.setTarget(agent.target, grid);
  }

  setTarget(target: TilePoint, grid: WalkGrid): void {
    if (
      this.lastTarget &&
      this.lastTarget.x === target.x &&
      this.lastTarget.y === target.y &&
      this.pathIndex < this.path.length
    ) {
      return;
    }
    this.lastTarget = { ...target };
    this.path = findPathTiles(this.tilePos, target, grid);
    this.pathIndex = 0;
  }

  sync(agent: HiveAgentState, textures: HiveSkinTextures, grid: WalkGrid): void {
    this.body.texture = textures.agent[agent.cast] ?? textures.agent.worker;
    if (this.nameLabel.text !== agent.name) this.nameLabel.text = agent.name;
    const toolText = agent.showTool ? `▸ ${agent.skill}` : "";
    if (this.toolBubble.text !== toolText) this.toolBubble.text = toolText;
    this.toolBubble.visible = agent.showTool;
    this.alpha = agent.motion === "ghost" ? 0.35 : 1;

    const atTarget =
      Math.abs(this.tilePos.x - agent.target.x) < 0.15 &&
      Math.abs(this.tilePos.y - agent.target.y) < 0.15;
    if (!atTarget) {
      this.setTarget(agent.target, grid);
    }

    if (agent.pulsing) {
      this.body.tint = 0xffffff;
    } else if (agent.motion === "waiting") {
      this.body.tint = 0xffd27a;
    } else if (agent.motion === "success") {
      this.body.tint = 0x8dffb0;
    } else {
      this.body.tint = 0xd0d6de;
    }
  }

  update(dt: number, walking: boolean): void {
    const speed = 4.5; // tiles / second
    if (this.pathIndex < this.path.length) {
      const next = this.path[this.pathIndex]!;
      const dx = next.x - this.tilePos.x;
      const dy = next.y - this.tilePos.y;
      const dist = Math.hypot(dx, dy);
      if (dist < 0.05) {
        this.tilePos = { ...next };
        this.pathIndex += 1;
      } else {
        const step = Math.min(dist, speed * dt);
        this.tilePos.x += (dx / dist) * step;
        this.tilePos.y += (dy / dist) * step;
      }
      this.bob += dt * 12;
    } else if (walking) {
      this.bob += dt * 8;
    }

    this.applyWorldPos();
    const bobY = Math.round(Math.sin(this.bob) * (this.pathIndex < this.path.length ? 1 : 0));
    this.body.y = bobY;
  }

  private applyWorldPos(): void {
    const world = tileToWorld(this.tilePos);
    this.x = Math.round(world.x);
    this.y = Math.round(world.y);
  }
}
