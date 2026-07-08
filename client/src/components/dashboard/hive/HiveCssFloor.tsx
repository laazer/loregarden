import { useEffect, useRef } from "react";

import type { HiveWorldModel } from "../../../lib/hive/worldModel";
import { HIVE_MAP, STATION_POSITIONS, WAITING_POSITION } from "../../../lib/hive/worldModel";

interface HiveCssFloorProps {
  model: HiveWorldModel;
}

function tilePercent(tile: { x: number; y: number }): { left: string; top: string } {
  return {
    left: `${(tile.x / HIVE_MAP.width) * 100}%`,
    top: `${(tile.y / HIVE_MAP.height) * 100}%`,
  };
}

/**
 * DOM/CSS floor — no Pixi. Safe for Dashboard critical path.
 * Agents animate with CSS transitions when their target tiles change.
 */
export function HiveCssFloor({ model }: HiveCssFloorProps) {
  const flyerSeen = useRef(new Set<string>());
  const flyersRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = flyersRef.current;
    if (!host) return;
    for (const flight of model.flights) {
      if (flyerSeen.current.has(flight.triggerKey)) continue;
      flyerSeen.current.add(flight.triggerKey);
      const el = document.createElement("div");
      el.className = `hive-css__flyer hive-css__flyer--${flight.kind}`;
      el.title = flight.label;
      const from = tilePercent(flight.from);
      const to = tilePercent(flight.to);
      el.style.left = from.left;
      el.style.top = from.top;
      host.appendChild(el);
      requestAnimationFrame(() => {
        el.style.left = to.left;
        el.style.top = to.top;
        el.style.opacity = "0";
      });
      window.setTimeout(() => el.remove(), 1200);
    }
  }, [model.flights]);

  const hq = tilePercent(STATION_POSITIONS.planner_hq);
  const wait = tilePercent(WAITING_POSITION);
  const errorEvent = model.events.find((e) => e.kind === "error");

  return (
    <div className={`hive-css hive-css--${model.skin}`} aria-label={`${model.floorTitle} simulation`}>
      <div
        className={`hive-css__god${model.orchestratorActive ? " hive-css__god--active" : ""}`}
        style={hq}
      >
        <div className="hive-css__god-icon" aria-hidden />
        <div className="hive-css__god-label">{model.orchestratorLabel}</div>
      </div>

      {model.stations.map((station) => {
        const pos = tilePercent({ x: station.x, y: station.y });
        const errored = errorEvent?.stationId === station.id;
        return (
          <div
            key={station.id}
            className={`hive-css__station${station.active ? " hive-css__station--active" : ""}${
              errored ? " hive-css__station--error" : ""
            }`}
            style={pos}
            title={station.label}
          >
            <div className="hive-css__station-sprite" aria-hidden />
            <div className="hive-css__station-label">{station.label}</div>
            {errored ? <div className="hive-css__station-error">{errorEvent.label}</div> : null}
          </div>
        );
      })}

      {model.waitingProp.visible ? (
        <div className="hive-css__waiting" style={wait} title={model.waitingProp.label}>
          <div className="hive-css__waiting-sprite" aria-hidden />
          <div className="hive-css__waiting-label">{model.waitingProp.label}</div>
        </div>
      ) : null}

      {model.agents.map((agent) => {
        const pos = tilePercent(agent.target);
        return (
          <div
            key={agent.id}
            className={`hive-css__agent hive-css__agent--${agent.motion}${
              agent.pulsing ? " hive-css__agent--pulse" : ""
            }`}
            style={{ ...pos, color: agent.color }}
            title={`${agent.name} · ${agent.statusLabel} · ${agent.stage}`}
          >
            <div className="hive-css__agent-avatar" aria-hidden>
              {agent.name.slice(0, 2)}
            </div>
            <div className="hive-css__agent-card">
              <div className="hive-css__agent-name">{agent.name}</div>
              <div className="hive-css__agent-status">
                {agent.statusLabel} · {agent.stage}
              </div>
              {agent.showTool ? <div className="hive-css__agent-skill">▸ {agent.skill}</div> : null}
            </div>
          </div>
        );
      })}

      <div ref={flyersRef} className="hive-css__flyers" aria-hidden />
    </div>
  );
}
