import { useEffect, useMemo, useRef, useState } from "react";

import type { DiskNode } from "../../api/diskTypes";
import {
  DISK_KIND_LABELS,
  formatDiskBytes,
  layoutTreemap,
  type TreemapTile,
} from "../../lib/diskUsage";
import { Button } from "../ui/Button";

interface DiskTreemapProps {
  focus: DiskNode;
  selected: ReadonlySet<string>;
  onOpen: (node: DiskNode) => void;
  /** The tile under the pointer or keyboard focus, for the readout beside the map. */
  onInspect: (node: DiskNode | null) => void;
  dimmed: boolean;
  /** The page's find box: tiles whose name (or group's name) misses it fade back. */
  filter: string;
}

const MIN_LABEL_WIDTH = 46;
const MIN_LABEL_HEIGHT = 30;

function tileLabel(tile: TreemapTile): string {
  const { node } = tile;
  const what =
    node.kind === "other"
      ? node.name
      : `${node.name}, ${DISK_KIND_LABELS[node.kind]}`;
  return `${what}, ${formatDiskBytes(node.size_bytes)}${node.is_dir && node.kind !== "other" ? ". Open" : ""}`;
}

/**
 * Where the space is, as a mosaic: each tile's area is its size.
 *
 * Two levels at once (the focus's children, and theirs inside them), so one
 * glance shows both which folder is big and what makes it big. Every tile is a
 * real button in size order, so the map is operable by keyboard and readable by
 * an agent; the table beside it says the same thing as rows. A folder opens on
 * click; a file or the folded "smaller items" only reports itself.
 */
export function DiskTreemap({
  focus,
  selected,
  onOpen,
  onInspect,
  dimmed,
  filter,
}: DiskTreemapProps) {
  const frameRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return undefined;
    const measure = () =>
      setSize({ width: frame.clientWidth, height: frame.clientHeight });
    measure();
    // jsdom has no ResizeObserver; one measurement is all a test needs.
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(frame);
    return () => observer.disconnect();
  }, []);

  const tiles = useMemo(
    () => layoutTreemap(focus, size.width, size.height),
    [focus, size],
  );
  const needle = filter.trim().toLowerCase();
  const matched = useMemo(() => {
    if (!needle) return null;
    const hits = new Set<string>();
    for (const tile of tiles) {
      if (!tile.node.name.toLowerCase().includes(needle)) continue;
      hits.add(tile.node.path);
      // A match inside a group lights the group's header too, and the reverse.
      for (const other of tiles) {
        if (
          other.node.path.startsWith(`${tile.node.path}/`) ||
          tile.node.path.startsWith(`${other.node.path}/`)
        ) {
          hits.add(other.node.path);
        }
      }
    }
    return hits;
  }, [tiles, needle]);

  return (
    <div
      ref={frameRef}
      className={`disk-treemap${dimmed ? " is-dimmed" : ""}`}
      role="group"
      aria-label={`Disk use inside ${focus.name}, largest first`}
      onMouseLeave={() => onInspect(null)}
    >
      {tiles.length === 0 && size.width > 0 ? (
        <p className="disk-treemap-empty">
          {focus.size_bytes > 0
            ? `${focus.name} holds ${formatDiskBytes(focus.size_bytes)} in nothing big enough to draw.`
            : `${focus.name} is empty.`}
        </p>
      ) : null}
      {tiles.map((tile) => {
        const { node } = tile;
        const opens = node.is_dir && node.kind !== "other";
        const showLabel =
          tile.isGroup ||
          (tile.width >= MIN_LABEL_WIDTH && tile.height >= MIN_LABEL_HEIGHT);
        return (
          <Button
            key={`${tile.depth}:${node.path}:${node.name}`}
            variant="plain"
            className={[
              "disk-tile",
              `disk-tile--${node.kind}`,
              tile.isGroup ? "is-group" : "",
              tile.depth === 2 ? "is-inner" : "",
              selected.has(node.path) ? "is-selected" : "",
              opens ? "" : "is-leaf",
              matched && !matched.has(node.path) ? "is-unmatched" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            style={{
              left: tile.x,
              top: tile.y,
              width: tile.width,
              height: tile.height,
            }}
            aria-label={tileLabel(tile)}
            title={`${node.name} · ${formatDiskBytes(node.size_bytes)} · ${DISK_KIND_LABELS[node.kind]}`}
            onClick={() => (opens ? onOpen(node) : onInspect(node))}
            onMouseEnter={() => onInspect(node)}
            onFocus={() => onInspect(node)}
          >
            {showLabel ? (
              <span className="disk-tile-label" aria-hidden>
                <span className="disk-tile-name">{node.name}</span>
                <span className="disk-tile-size">
                  {formatDiskBytes(node.size_bytes)}
                </span>
              </span>
            ) : null}
          </Button>
        );
      })}
    </div>
  );
}
