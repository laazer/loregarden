import { useEffect, useRef } from "react";

import { tilePercent } from "../../../lib/hive/coords";
import type { HiveOfficeErrand } from "../../../lib/hive/layouts/officeplaceLayout";
import { findPathTiles, type TilePoint, type WalkGrid } from "../../../lib/hive/pathfinding";
import { HIVE_AGENT_MOTION_MS, hiveScaledMs, type HiveSpeedMultiplier } from "../../../lib/hive/speed";
import type { HiveAgentState } from "../../../lib/hive/worldModel";

const TILE_SPEED = 4.5;
const IDLE_BEFORE_ERRAND_MS = 4500;
const ERRAND_PAUSE_MS = 3000;

type ErrandPhase = "none" | "to" | "at" | "back";

interface HiveCssAgentProps {
  agent: HiveAgentState;
  map: { width: number; height: number };
  grid: WalkGrid;
  errands: HiveOfficeErrand[];
  speedMultiplier: HiveSpeedMultiplier;
  avatarSrc: string | null;
  avatarFallback: string;
}

function stableErrandIndex(agentId: string, count: number, salt: number): number {
  let hash = salt;
  for (let i = 0; i < agentId.length; i++) {
    hash = (hash * 31 + agentId.charCodeAt(i)) | 0;
  }
  return Math.abs(hash) % count;
}

function SpriteOrFallback({
  src,
  className,
  alt,
  fallback,
}: {
  src: string | null;
  className: string;
  alt: string;
  fallback: string;
}) {
  if (src) {
    return <img className={className} src={src} alt={alt} draggable={false} />;
  }
  return (
    <div className={className} aria-hidden>
      {fallback}
    </div>
  );
}

export function HiveCssAgent({
  agent,
  map,
  grid,
  errands,
  speedMultiplier,
  avatarSrc,
  avatarFallback,
}: HiveCssAgentProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const statusRef = useRef<HTMLDivElement>(null);
  const tilePos = useRef<TilePoint>({ ...agent.desk });
  const pathRef = useRef<TilePoint[]>([]);
  const pathIndexRef = useRef(0);
  const missionTargetRef = useRef<TilePoint>({ ...agent.target });
  const errandPhaseRef = useRef<ErrandPhase>("none");
  const errandSaltRef = useRef(0);
  const idleMsRef = useRef(0);
  const pauseMsRef = useRef(0);
  const errandLabelRef = useRef<string | null>(null);
  const walkingRef = useRef(false);

  const beginPath = (target: TilePoint) => {
    pathRef.current = findPathTiles(tilePos.current, target, grid);
    pathIndexRef.current = 0;
    walkingRef.current = pathRef.current.length > 1;
  };

  const applyPosition = () => {
    const el = rootRef.current;
    if (!el) return;
    const pos = tilePercent(tilePos.current, map);
    el.style.left = pos.left;
    el.style.top = pos.top;
    el.classList.toggle("hive-css__agent--walking", walkingRef.current);
  };

  const applyStatus = () => {
    const el = statusRef.current;
    if (!el) return;
    const errand = errandLabelRef.current;
    el.textContent = errand ?? `${agent.statusLabel} · ${agent.stage}`;
  };

  useEffect(() => {
    tilePos.current = { ...agent.desk };
    missionTargetRef.current = { ...agent.target };
    errandPhaseRef.current = "none";
    errandLabelRef.current = null;
    idleMsRef.current = 0;
    pauseMsRef.current = 0;
    beginPath(agent.target);
    applyPosition();
    applyStatus();
  }, [agent.id, agent.desk.x, agent.desk.y]);

  useEffect(() => {
    missionTargetRef.current = { ...agent.target };
    errandPhaseRef.current = "none";
    errandLabelRef.current = null;
    idleMsRef.current = 0;
    pauseMsRef.current = 0;
    beginPath(agent.target);
    applyStatus();
  }, [agent.target.x, agent.target.y, agent.motion]);

  useEffect(() => {
    applyStatus();
  }, [agent.statusLabel, agent.stage]);

  useEffect(() => {
    let raf = 0;
    let last = performance.now();

    const step = (dt: number) => {
      const speed = TILE_SPEED * speedMultiplier;
      const canErrand = agent.motion === "idle" && errands.length > 0;

      if (pathIndexRef.current < pathRef.current.length) {
        const next = pathRef.current[pathIndexRef.current]!;
        const dx = next.x - tilePos.current.x;
        const dy = next.y - tilePos.current.y;
        const dist = Math.hypot(dx, dy);
        if (dist < 0.04) {
          tilePos.current = { ...next };
          pathIndexRef.current += 1;
        } else {
          const stepDist = Math.min(dist, speed * dt);
          tilePos.current.x += (dx / dist) * stepDist;
          tilePos.current.y += (dy / dist) * stepDist;
        }
        walkingRef.current = true;
        applyPosition();
        return;
      }

      walkingRef.current = false;
      applyPosition();

      if (!canErrand) return;

      if (errandPhaseRef.current === "none") {
        const atDesk =
          Math.abs(tilePos.current.x - agent.desk.x) < 0.2 &&
          Math.abs(tilePos.current.y - agent.desk.y) < 0.2;
        if (!atDesk) return;
        idleMsRef.current += dt * 1000;
        if (idleMsRef.current < IDLE_BEFORE_ERRAND_MS / speedMultiplier) return;
        idleMsRef.current = 0;
        errandSaltRef.current += 1;
        const idx = stableErrandIndex(agent.id, errands.length, errandSaltRef.current);
        const spot = errands[idx]!;
        errandPhaseRef.current = "to";
        errandLabelRef.current = spot.label;
        applyStatus();
        beginPath(spot.stand);
        return;
      }

      if (errandPhaseRef.current === "to") {
        errandPhaseRef.current = "at";
        pauseMsRef.current = 0;
        return;
      }

      if (errandPhaseRef.current === "at") {
        pauseMsRef.current += dt * 1000;
        if (pauseMsRef.current < ERRAND_PAUSE_MS / speedMultiplier) return;
        errandPhaseRef.current = "back";
        beginPath(agent.desk);
        return;
      }

      if (errandPhaseRef.current === "back") {
        errandPhaseRef.current = "none";
        errandLabelRef.current = null;
        applyStatus();
        idleMsRef.current = 0;
      }
    };

    const loop = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;
      step(dt);
      raf = requestAnimationFrame(loop);
    };

    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [agent.motion, agent.desk.x, agent.desk.y, agent.id, errands, grid, map, speedMultiplier]);

  const initialPos = tilePercent(agent.desk, map);

  return (
    <div
      ref={rootRef}
      className={`hive-css__agent hive-css__agent--pathing hive-css__agent--${agent.motion}${
        agent.pulsing ? " hive-css__agent--pulse" : ""
      }`}
      style={{ left: initialPos.left, top: initialPos.top, color: agent.color }}
      title={`${agent.name} · ${agent.statusLabel} · ${agent.stage}`}
    >
      {agent.lead ? (
        <div className="hive-css__agent-card">
          <div className="hive-css__agent-name">{agent.name}</div>
          <div ref={statusRef} className="hive-css__agent-status">
            {agent.statusLabel} · {agent.stage}
          </div>
          {agent.showTool ? <div className="hive-css__agent-skill">▸ {agent.skill}</div> : null}
        </div>
      ) : null}
      <SpriteOrFallback
        src={avatarSrc}
        className="hive-css__agent-avatar"
        alt={agent.name}
        fallback={avatarFallback}
      />
    </div>
  );
}

export const hiveAgentStepMs = (speed: HiveSpeedMultiplier) =>
  hiveScaledMs(HIVE_AGENT_MOTION_MS, speed);
