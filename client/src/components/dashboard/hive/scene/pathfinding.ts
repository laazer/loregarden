import { HIVE_MAP } from "../../../../lib/hive/worldModel";

export type TilePoint = { x: number; y: number };

const DIRS: TilePoint[] = [
  { x: 1, y: 0 },
  { x: -1, y: 0 },
  { x: 0, y: 1 },
  { x: 0, y: -1 },
];

function key(p: TilePoint): string {
  return `${p.x},${p.y}`;
}

function snap(p: TilePoint): TilePoint {
  return { x: Math.round(p.x), y: Math.round(p.y) };
}

function heuristic(a: TilePoint, b: TilePoint): number {
  return Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
}

/** A* on an open grid with a 1-tile border wall. */
export function findPathTiles(from: TilePoint, to: TilePoint): TilePoint[] {
  const start = snap(from);
  const goal = snap(to);
  if (start.x === goal.x && start.y === goal.y) return [{ ...goal }];

  const cameFrom = new Map<string, string>();
  const gScore = new Map<string, number>();
  const fScore = new Map<string, number>();
  const open = new Set<string>();
  const nodes = new Map<string, TilePoint>();

  const sk = key(start);
  open.add(sk);
  nodes.set(sk, start);
  gScore.set(sk, 0);
  fScore.set(sk, heuristic(start, goal));

  while (open.size > 0) {
    let currentKey = "";
    let best = Infinity;
    for (const k of open) {
      const f = fScore.get(k) ?? Infinity;
      if (f < best) {
        best = f;
        currentKey = k;
      }
    }
    const current = nodes.get(currentKey);
    if (!current) break;

    if (current.x === goal.x && current.y === goal.y) {
      return unwind(cameFrom, nodes, currentKey);
    }
    open.delete(currentKey);

    for (const dir of DIRS) {
      const nx = current.x + dir.x;
      const ny = current.y + dir.y;
      if (nx < 1 || ny < 1 || nx >= HIVE_MAP.width - 1 || ny >= HIVE_MAP.height - 1) continue;
      const nk = `${nx},${ny}`;
      const tentative = (gScore.get(currentKey) ?? Infinity) + 1;
      if (tentative >= (gScore.get(nk) ?? Infinity)) continue;
      cameFrom.set(nk, currentKey);
      nodes.set(nk, { x: nx, y: ny });
      gScore.set(nk, tentative);
      fScore.set(nk, tentative + heuristic({ x: nx, y: ny }, goal));
      open.add(nk);
    }
  }

  return [start, goal];
}

function unwind(
  cameFrom: Map<string, string>,
  nodes: Map<string, TilePoint>,
  endKey: string,
): TilePoint[] {
  const path: TilePoint[] = [];
  let cur: string | undefined = endKey;
  while (cur) {
    const p = nodes.get(cur);
    if (p) path.push(p);
    cur = cameFrom.get(cur);
  }
  path.reverse();
  return path.length > 0 ? path : [];
}

export function tileToWorld(tile: TilePoint): { x: number; y: number } {
  return {
    x: tile.x * HIVE_MAP.tileSize + HIVE_MAP.tileSize / 2,
    y: tile.y * HIVE_MAP.tileSize + HIVE_MAP.tileSize / 2,
  };
}
