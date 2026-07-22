import { useEffect, useRef, useState } from "react";

import { tilePercent } from "../../../lib/hive/coords";
import type { HiveLayout } from "../../../lib/hive/layouts";

interface HiveDebugOverlayProps {
  layout: HiveLayout;
}

interface Marker {
  x: number;
  y: number;
  label: string;
  kind: "station" | "desk" | "errand" | "waiting" | "reception" | "mdr" | "zone";
}

/** Every hand-placed point on the officeplace floor, gathered for eyeball calibration. */
function collectMarkers(layout: HiveLayout): Marker[] {
  const markers: Marker[] = [];
  for (const [id, pos] of Object.entries(layout.stationPositions)) {
    markers.push({ x: pos.x, y: pos.y, label: id, kind: "station" });
  }
  layout.deskRow.forEach((desk, i) => {
    markers.push({ x: desk.x, y: desk.y, label: `desk${i}`, kind: "desk" });
  });
  layout.errands.forEach((errand) => {
    markers.push({ x: errand.stand.x, y: errand.stand.y, label: errand.id, kind: "errand" });
  });
  markers.push({
    x: layout.waitingPosition.x,
    y: layout.waitingPosition.y,
    label: "waiting",
    kind: "waiting",
  });
  if (layout.receptionist) {
    markers.push({
      x: layout.receptionist.x,
      y: layout.receptionist.y,
      label: "reception",
      kind: "reception",
    });
  }
  layout.mdrStaff.forEach((staff) => {
    markers.push({ x: staff.x, y: staff.y, label: staff.label, kind: "mdr" });
  });
  layout.zones.forEach((zone) => {
    markers.push({ x: zone.x, y: zone.y, label: zone.label, kind: "zone" });
  });
  return markers;
}

/**
 * Calibration overlay for officeplace pathing. Paints the (invisible) walk grid
 * over the background image — green = walkable, red = blocked — and drops a dot on
 * every hand-placed station/desk/errand. Hover reads out the tile under the cursor;
 * click copies `{ x, y }` so coordinates can be pasted straight back into the layout.
 *
 * Not shipped by default: gated behind `?hiveDebug` in HiveCssFloor.
 */
export function HiveDebugOverlay({ layout }: HiveDebugOverlayProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const { map, walkGrid } = layout;
  const [hover, setHover] = useState<{ x: number; y: number; walkable: boolean } | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    canvas.width = map.width;
    canvas.height = map.height;
    ctx.clearRect(0, 0, map.width, map.height);
    for (let y = 0; y < map.height; y++) {
      for (let x = 0; x < map.width; x++) {
        ctx.fillStyle = walkGrid.isWalkable(x, y)
          ? "rgba(64, 208, 120, 0.32)"
          : "rgba(224, 64, 64, 0.24)";
        ctx.fillRect(x, y, 1, 1);
      }
    }
  }, [map.width, map.height, walkGrid]);

  const markers = collectMarkers(layout);

  const onMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = Math.floor(((e.clientX - rect.left) / rect.width) * map.width);
    const y = Math.floor(((e.clientY - rect.top) / rect.height) * map.height);
    if (x < 0 || y < 0 || x >= map.width || y >= map.height) {
      setHover(null);
      return;
    }
    setHover({ x, y, walkable: walkGrid.isWalkable(x, y) });
  };

  const onClick = () => {
    if (!hover) return;
    const text = `{ x: ${hover.x}, y: ${hover.y} }`;
    void navigator.clipboard?.writeText(text);
    setCopied(text);
    window.setTimeout(() => setCopied(null), 1200);
  };

  return (
    <div className="hive-debug" aria-hidden>
      <canvas ref={canvasRef} className="hive-debug__grid" />
      {markers.map((m) => (
        <div
          key={`${m.kind}-${m.label}-${m.x}-${m.y}`}
          className={`hive-debug__marker hive-debug__marker--${m.kind}`}
          style={tilePercent(m, map)}
        >
          <span className="hive-debug__marker-label">{m.label}</span>
        </div>
      ))}
      <div
        className="hive-debug__capture"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        onClick={onClick}
      />
      <div className="hive-debug__hud">
        <div className="hive-debug__readout">
          {hover ? (
            <>
              tile <strong>{hover.x}, {hover.y}</strong> ·{" "}
              <span className={hover.walkable ? "hive-debug__ok" : "hive-debug__no"}>
                {hover.walkable ? "walkable" : "blocked"}
              </span>
              {copied ? <em> · copied {copied}</em> : <em> · click to copy</em>}
            </>
          ) : (
            <>
              {map.width}×{map.height} grid · hover to read tile, click to copy
            </>
          )}
        </div>
        <div className="hive-debug__legend">
          <span className="hive-debug__key hive-debug__key--walk">walkable</span>
          <span className="hive-debug__key hive-debug__key--block">blocked</span>
          <span className="hive-debug__key hive-debug__key--station">station</span>
          <span className="hive-debug__key hive-debug__key--desk">desk</span>
          <span className="hive-debug__key hive-debug__key--errand">errand</span>
          <span className="hive-debug__key hive-debug__key--reception">reception</span>
        </div>
      </div>
    </div>
  );
}
