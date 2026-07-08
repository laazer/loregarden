export type TilePoint = { x: number; y: number };

export interface WalkGrid {
  width: number;
  height: number;
  isWalkable: (x: number, y: number) => boolean;
  /** When set, path goals snap here instead of any walkable tile (e.g. off wall-adjacent rows). */
  isStandable?: (x: number, y: number) => boolean;
}

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

function inBounds(grid: WalkGrid, x: number, y: number): boolean {
  return x >= 0 && y >= 0 && x < grid.width && y < grid.height;
}

function canStand(grid: WalkGrid, x: number, y: number): boolean {
  if (grid.isStandable) return grid.isStandable(x, y);
  return grid.isWalkable(x, y);
}

/** Snap goal onto the nearest standable tile (stations often sit on furniture or wall rows). */
export function nearestWalkableTile(grid: WalkGrid, goal: TilePoint): TilePoint {
  const g = snap(goal);
  if (inBounds(grid, g.x, g.y) && canStand(grid, g.x, g.y)) return g;

  const seen = new Set<string>();
  const queue: TilePoint[] = [g];
  seen.add(key(g));

  while (queue.length > 0) {
    const cur = queue.shift()!;
    for (const dir of DIRS) {
      const nx = cur.x + dir.x;
      const ny = cur.y + dir.y;
      const nk = key({ x: nx, y: ny });
      if (!inBounds(grid, nx, ny) || seen.has(nk)) continue;
      seen.add(nk);
      if (canStand(grid, nx, ny)) return { x: nx, y: ny };
      queue.push({ x: nx, y: ny });
    }
  }

  return g;
}

/** A* on a walkability grid. Returns empty array when start is blocked. */
export function findPathTiles(
  from: TilePoint,
  to: TilePoint,
  grid: WalkGrid,
): TilePoint[] {
  const start = snap(from);
  const goal = nearestWalkableTile(grid, to);
  if (start.x === goal.x && start.y === goal.y) return [{ ...goal }];
  if (!grid.isWalkable(start.x, start.y)) return [{ ...start }];

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
      if (!inBounds(grid, nx, ny) || !grid.isWalkable(nx, ny)) continue;
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

  return [{ ...start }];
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

export function createOpenWalkGrid(width: number, height: number): WalkGrid {
  return {
    width,
    height,
    isWalkable(x: number, y: number) {
      if (x < 1 || y < 1 || x >= width - 1 || y >= height - 1) return false;
      return true;
    },
  };
}

export function pathCrossesBlocked(
  path: TilePoint[],
  grid: WalkGrid,
  skipStart = true,
): boolean {
  return path.some((p, i) => {
    if (skipStart && i === 0) return false;
    return !grid.isWalkable(p.x, p.y);
  });
}
