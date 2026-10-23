/**
 * Formatting and layout for Machine → Disk: sizes, kinds, and the treemap.
 *
 * The layout is d3-hierarchy's squarified treemap. Each node's own value is its
 * size minus what its children account for (a directory's own blocks), so a
 * parent's area is exactly its size and never double-counts a child.
 */

import {
  hierarchy,
  treemap,
  treemapSquarify,
  type HierarchyRectangularNode,
} from "d3-hierarchy";

import type { DiskKind, DiskNode } from "../api/diskTypes";

export function formatDiskBytes(bytes: number): string {
  const units: [string, number][] = [
    ["TB", 1024 ** 4],
    ["GB", 1024 ** 3],
    ["MB", 1024 ** 2],
    ["KB", 1024],
  ];
  for (const [unit, scale] of units) {
    if (bytes >= scale) {
      const value = bytes / scale;
      return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} ${unit}`;
    }
  }
  return `${bytes} B`;
}

export const DISK_KIND_LABELS: Record<DiskKind, string> = {
  dependencies: "Dependencies",
  build_output: "Build output",
  cache: "Cache",
  worktree: "Worktree",
  workspace: "Workspace",
  git_store: "Git history",
  loregarden_data: "Loregarden data",
  directory: "Folder",
  file: "File",
  other: "Smaller items",
};

/** The legend's order: what cleanup can usually take first. */
export const DISK_LEGEND_KINDS: DiskKind[] = [
  "worktree",
  "dependencies",
  "build_output",
  "cache",
  "git_store",
  "loregarden_data",
  "directory",
  "file",
  "other",
];

/** Kinds a package manager, build or app puts back. A worktree is not one: it may hold uncommitted work. */
export function isRegenerable(node: DiskNode): boolean {
  return node.kind === "dependencies" || node.kind === "build_output" || node.kind === "cache";
}

/**
 * Whether the page offers a checkbox. Broader than `isRegenerable`: an
 * ignored folder of logs may be fine, and the server's preview says why when it
 * is not. A scanned root, the folded "smaller items", git's own store and
 * loregarden's data are never offered, because the server refuses them all.
 */
export function isSelectable(
  node: DiskNode,
  rootPaths: ReadonlySet<string>,
): boolean {
  if (rootPaths.has(node.path) && node.kind !== "worktree") return false;
  return !(
    node.kind === "other" ||
    node.kind === "workspace" ||
    node.kind === "git_store" ||
    node.kind === "loregarden_data"
  );
}

export interface TreemapTile {
  node: DiskNode;
  /** 1 for the focus's children, 2 for theirs. */
  depth: number;
  x: number;
  y: number;
  width: number;
  height: number;
  /** Has drawn children, so its label sits in a header strip. */
  isGroup: boolean;
}

const GROUP_HEADER = 18;

function ownValue(node: DiskNode): number {
  const children = node.children.reduce(
    (sum, child) => sum + child.size_bytes,
    0,
  );
  return Math.max(0, node.size_bytes - children);
}

export function layoutTreemap(
  focus: DiskNode,
  width: number,
  height: number,
): TreemapTile[] {
  if (width <= 0 || height <= 0 || focus.size_bytes <= 0) return [];
  const root = hierarchy(focus, (node) => node.children)
    .sum(ownValue)
    .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
  const laid: HierarchyRectangularNode<DiskNode> = treemap<DiskNode>()
    .tile(treemapSquarify)
    .size([width, height])
    .paddingInner(2)
    .paddingTop((node) =>
      node.depth === 1 && node.children?.length ? GROUP_HEADER : 0,
    )
    .paddingRight((node) => (node.depth === 1 && node.children?.length ? 2 : 0))
    .paddingBottom((node) =>
      node.depth === 1 && node.children?.length ? 2 : 0,
    )
    .paddingLeft((node) => (node.depth === 1 && node.children?.length ? 2 : 0))
    .round(true)(root);
  const tiles: TreemapTile[] = [];
  for (const node of laid.descendants()) {
    if (node.depth === 0 || node.depth > 2) continue;
    const tileWidth = node.x1 - node.x0;
    const tileHeight = node.y1 - node.y0;
    if (tileWidth < 1 || tileHeight < 1) continue;
    tiles.push({
      node: node.data,
      depth: node.depth,
      x: node.x0,
      y: node.y0,
      width: tileWidth,
      height: tileHeight,
      isGroup: node.depth === 1 && Boolean(node.children?.length),
    });
  }
  return tiles;
}

/** Selected paths with any path inside another selected one dropped, so nothing counts twice. */
export function outermostPaths(paths: Iterable<string>): string[] {
  const sorted = [...new Set(paths)].sort((a, b) => a.length - b.length);
  const kept: string[] = [];
  for (const path of sorted) {
    if (!kept.some((outer) => path === outer || path.startsWith(`${outer}/`)))
      kept.push(path);
  }
  return kept;
}
