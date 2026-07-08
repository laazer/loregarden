import { useEffect, useMemo, useRef, type CSSProperties } from "react";

import { tilePercent } from "../../../lib/hive/coords";
import { floorBackgroundStyle, resolveSkinSprites } from "../../../lib/hive/spriteUrls";
import {
  HIVE_FLYER_FADE_MS,
  HIVE_FLYER_MOTION_MS,
  hiveScaledMs,
  type HiveSpeedMultiplier,
} from "../../../lib/hive/speed";
import type { HiveWorldModel } from "../../../lib/hive/worldModel";
import { HiveCssAgent } from "./HiveCssAgent";

interface HiveCssFloorProps {
  model: HiveWorldModel;
  speedMultiplier?: HiveSpeedMultiplier;
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
  fallback?: string;
}) {
  if (src) {
    return <img className={className} src={src} alt={alt} draggable={false} />;
  }
  return (
    <div className={className} aria-hidden>
      {fallback ?? null}
    </div>
  );
}

/**
 * DOM/CSS floor — no Pixi. Safe for Dashboard critical path.
 * Agents animate with CSS transitions when their target tiles change.
 */
export function HiveCssFloor({ model, speedMultiplier = 1 }: HiveCssFloorProps) {
  const flyerSeen = useRef(new Set<string>());
  const flyersRef = useRef<HTMLDivElement>(null);
  const sprites = useMemo(() => resolveSkinSprites(model.skin), [model.skin]);
  const { layout } = model;
  const map = layout.map;
  const hasScenery = Boolean(sprites.scenery);
  const hideStationSprites = layout.hideStationSprites && hasScenery;

  const flyerMotionMs = hiveScaledMs(HIVE_FLYER_MOTION_MS, speedMultiplier);
  const flyerFadeMs = hiveScaledMs(HIVE_FLYER_FADE_MS, speedMultiplier);
  const motionStyle = useMemo(
    () =>
      ({
        "--hive-flyer-ms": `${flyerMotionMs}ms`,
      }) as CSSProperties,
    [flyerMotionMs],
  );

  useEffect(() => {
    flyerSeen.current.clear();
  }, [model.skin]);

  useEffect(() => {
    const host = flyersRef.current;
    if (!host || hasScenery) return;
    for (const flight of model.flights) {
      if (flyerSeen.current.has(flight.triggerKey)) continue;
      flyerSeen.current.add(flight.triggerKey);
      const el = document.createElement("div");
      el.className = `hive-css__flyer hive-css__flyer--${flight.kind}`;
      el.title = flight.label;
      const art = sprites.artifact[flight.kind];
      if (art) {
        const img = document.createElement("img");
        img.src = art;
        img.alt = flight.label;
        img.draggable = false;
        img.className = "hive-css__flyer-img";
        el.appendChild(img);
      }
      const from = tilePercent(flight.from, map);
      const to = tilePercent(flight.to, map);
      el.style.left = from.left;
      el.style.top = from.top;
      host.appendChild(el);
      requestAnimationFrame(() => {
        el.style.left = to.left;
        el.style.top = to.top;
        el.style.opacity = "0";
      });
      window.setTimeout(() => el.remove(), flyerFadeMs);
    }
  }, [model.flights, sprites.artifact, flyerFadeMs, map, hasScenery]);

  const hq = tilePercent(layout.stationPositions.planner_hq, map);
  const wait = tilePercent(layout.waitingPosition, map);
  const errorEvent = model.events.find((e) => e.kind === "error");
  const floorStyle = {
    ...floorBackgroundStyle(sprites.floor, sprites.scenery),
    ...motionStyle,
  };

  return (
    <div
      className={`hive-css hive-css--${model.skin}${hasScenery ? " hive-css--scenery" : sprites.floor ? " hive-css--has-floor" : ""}`}
      style={floorStyle}
      aria-label={`${model.floorTitle} simulation`}
    >
      {model.stations
        .filter((station) => station.id !== "planner_hq")
        .map((station) => {
          const pos = tilePercent({ x: station.x, y: station.y }, map);
          const errored = errorEvent?.stationId === station.id;
          return (
            <div
              key={station.id}
              className={`hive-css__station${station.active ? " hive-css__station--active" : ""}${
                errored ? " hive-css__station--error" : ""
              }${hideStationSprites ? " hive-css__station--marker" : ""}`}
              style={pos}
              title={station.label}
            >
              <div className="hive-css__station-label hive-css__nameplate">{station.label}</div>
              {hideStationSprites ? (
                <div className="hive-css__station-marker" aria-hidden />
              ) : (
                <SpriteOrFallback
                  src={sprites.station[station.id]}
                  className="hive-css__station-sprite"
                  alt={station.label}
                />
              )}
              {errored ? (
                <div className="hive-css__station-error">
                  <SpriteOrFallback
                    src={sprites.event.error}
                    className="hive-css__event-badge"
                    alt={errorEvent.label}
                  />
                  <span>{errorEvent.label}</span>
                </div>
              ) : null}
            </div>
          );
        })}

      {layout.zones.map((zone) => (
        <div
          key={zone.id}
          className="hive-css__zone"
          style={tilePercent({ x: zone.x, y: zone.y }, map)}
        >
          <span className="hive-css__zone-label hive-css__nameplate hive-css__nameplate--muted">
            {zone.label}
          </span>
        </div>
      ))}

      <div
        className={`hive-css__god${model.orchestratorActive ? " hive-css__god--active" : ""}${
          hideStationSprites ? " hive-css__god--marker" : ""
        }`}
        style={hq}
      >
        <div className="hive-css__god-label hive-css__nameplate">{model.orchestratorLabel}</div>
        {hideStationSprites ? (
          <div className="hive-css__god-marker" aria-hidden />
        ) : (
          <SpriteOrFallback
            src={sprites.station.planner_hq}
            className="hive-css__god-icon"
            alt={model.orchestratorLabel}
          />
        )}
      </div>

      {model.waitingProp.visible && !hasScenery ? (
        <div className="hive-css__waiting" style={wait} title={model.waitingProp.label}>
          <div className="hive-css__waiting-label hive-css__nameplate">{model.waitingProp.label}</div>
          <SpriteOrFallback
            src={sprites.event.waiting}
            className="hive-css__waiting-sprite"
            alt={model.waitingProp.label}
          />
        </div>
      ) : null}

      {model.agents.map((agent) => (
        <HiveCssAgent
          key={agent.id}
          agent={agent}
          map={map}
          grid={layout.walkGrid}
          errands={layout.errands}
          speedMultiplier={speedMultiplier}
          avatarSrc={sprites.agent[agent.cast]}
          avatarFallback={agent.name.slice(0, 2)}
        />
      ))}

      <div ref={flyersRef} className="hive-css__flyers" aria-hidden />
    </div>
  );
}
