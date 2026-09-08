import { useEffect, useMemo, useRef, useState } from "react";
import type { Application } from "pixi.js";

import type { HiveSkinId } from "../../../lib/hive/skins";
import type { HiveWorldModel } from "../../../lib/hive/worldModel";
import { describeError, pushToast } from "../../../state/toastStore";
import { HiveErrorBoundary } from "./HiveErrorBoundary";
import { createHiveApplication, type OfficeFloor } from "./scene/OfficeFloor";

interface HiveFloorSceneProps {
  model: HiveWorldModel;
  skin: HiveSkinId;
}

function modelSyncKey(model: HiveWorldModel): string {
  const agents = model.agents
    .map((a) => `${a.id}:${a.status}:${a.station}:${a.skill}:${a.motion}`)
    .join("|");
  const events = model.events.map((e) => `${e.kind}:${e.stationId ?? ""}`).join("|");
  const flights = model.flights.map((f) => f.triggerKey).join("|");
  return `${model.skin}:${model.orchestratorActive}:${model.waitingProp.visible}:${agents}:${events}:${flights}`;
}

const INIT_TIMEOUT_MS = 3500;

/**
 * The Pixi floor, behind the boundary it was written for: a WebGL/WebGPU crash
 * throws during render, where `bootError` cannot catch it, and would otherwise
 * take the whole page down with it.
 */
export function HiveFloorScene(props: HiveFloorSceneProps) {
  return (
    <HiveErrorBoundary>
      <HiveFloorSceneCanvas {...props} />
    </HiveErrorBoundary>
  );
}

function HiveFloorSceneCanvas({ model, skin }: HiveFloorSceneProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const appRef = useRef<Application | null>(null);
  const floorRef = useRef<OfficeFloor | null>(null);
  const modelRef = useRef(model);
  const skinRef = useRef(skin);
  const lastSyncKeyRef = useRef("");
  const lastSizeRef = useRef({ w: 0, h: 0 });
  const [bootError, setBootError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  modelRef.current = model;
  skinRef.current = skin;

  const syncKey = useMemo(() => modelSyncKey(model), [model]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    let alive = true;
    let raf = 0;
    let last = performance.now();
    let ro: ResizeObserver | null = null;
    let resizeScheduled = false;
    let timeoutId = 0;

    const resize = () => {
      const app = appRef.current;
      const floor = floorRef.current;
      if (!app || !floor) return;
      const w = Math.max(1, Math.floor(host.clientWidth || 640));
      const h = Math.max(1, Math.floor(host.clientHeight || 440));
      if (w === lastSizeRef.current.w && h === lastSizeRef.current.h) return;
      lastSizeRef.current = { w, h };
      app.renderer.resize(w, h);
      floor.fitToView(w, h);
    };

    const scheduleResize = () => {
      if (resizeScheduled) return;
      resizeScheduled = true;
      requestAnimationFrame(() => {
        resizeScheduled = false;
        if (alive) resize();
      });
    };

    const cleanupApp = () => {
      cancelAnimationFrame(raf);
      ro?.disconnect();
      ro = null;
      const floor = floorRef.current;
      const app = appRef.current;
      floorRef.current = null;
      appRef.current = null;
      floor?.destroy();
      app?.destroy(true, { children: true, texture: false });
      host.replaceChildren();
    };

    timeoutId = window.setTimeout(() => {
      if (!alive || appRef.current) return;
      alive = false;
      cleanupApp();
      setBootError("Hive floor timed out starting");
      setReady(false);
    }, INIT_TIMEOUT_MS);

    (async () => {
      try {
        const { app, floor } = await createHiveApplication(host);
        window.clearTimeout(timeoutId);
        if (!alive) {
          floor.destroy();
          app.destroy(true, { children: true, texture: false });
          return;
        }

        appRef.current = app;
        floorRef.current = floor;
        app.ticker.stop();
        app.ticker.autoStart = false;

        resize();
        ro = new ResizeObserver(scheduleResize);
        ro.observe(host);

        setReady(true);
        setBootError(null);

        try {
          await floor.setSkin(skinRef.current);
          if (alive) {
            lastSyncKeyRef.current = `${skinRef.current}::${modelSyncKey(modelRef.current)}`;
            floor.sync(modelRef.current);
            app.render();
          }
        } catch (skinErr) {
          // The floor is up but dressed in nothing: say so, because a blank
          // office and an empty office look identical on screen.
          pushToast({
            tone: "warning",
            title: "Hive artwork failed to load",
            message: describeError(skinErr, "The floor is running without its sprites"),
          });
        }

        const tick = (now: number) => {
          if (!alive) return;
          const dt = Math.min(0.05, (now - last) / 1000);
          last = now;
          if (!document.hidden && host.offsetParent !== null) {
            floor.update(dt);
            app.render();
          }
          raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
      } catch (err) {
        window.clearTimeout(timeoutId);
        console.error("[hive] boot failed", err);
        if (alive) {
          setBootError(describeError(err, "Failed to start hive floor"));
          setReady(false);
        }
      }
    })();

    return () => {
      alive = false;
      window.clearTimeout(timeoutId);
      cleanupApp();
    };
  }, []);

  useEffect(() => {
    const floor = floorRef.current;
    const app = appRef.current;
    if (!floor || !ready) return;
    let alive = true;
    (async () => {
      try {
        await floor.setSkin(skin);
        if (!alive) return;
        const key = `${skin}::${syncKey}`;
        if (key === lastSyncKeyRef.current) return;
        lastSyncKeyRef.current = key;
        floor.sync(modelRef.current);
        app?.render();
      } catch (err) {
        // A sync that threw leaves the last frame on screen — stale, and
        // indistinguishable from a quiet office unless it is reported.
        pushToast({
          tone: "warning",
          title: "Hive floor is out of date",
          message: describeError(err, "The floor stopped following the run"),
        });
      }
    })();
    return () => {
      alive = false;
    };
  }, [skin, syncKey, ready]);

  if (bootError) {
    return (
      <div className="hive-panel__idle">
        <div className="hive-panel__idle-title">Hive floor unavailable</div>
        <div className="hive-panel__idle-copy">{bootError}</div>
      </div>
    );
  }

  return (
    <div ref={hostRef} className="hive-panel__canvas-host">
      {!ready ? (
        <div className="hive-panel__idle">
          <div className="hive-panel__idle-copy">Starting floor…</div>
        </div>
      ) : null}
    </div>
  );
}
