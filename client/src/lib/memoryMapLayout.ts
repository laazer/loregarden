/**
 * Where each record sits on the memory map, and what the map can say about
 * the graph's shape.
 *
 * A force layout: recorded links pull their records together, every record
 * pushes the others away, so linked records settle into clusters and unlinked
 * ones drift to the edge. That is the only thing position says — distance is
 * not similarity, and the legend says so in words. Edges remain the only
 * relationships the map asserts.
 *
 * Deterministic: d3-force seeds its initial positions on a phyllotaxis spiral
 * in input order and draws jitter from a fixed LCG, and the simulation is run
 * to rest here rather than animated. The same graph always draws the same way,
 * which is what lets a person come back to "the cluster in the top left".
 */

import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";

import type { GraphNode, GraphRelation } from "../api/memoryApi";

/** What the layout needs of an edge — a recorded relation or an inferred one. */
export type EdgeLike = Pick<GraphRelation, "id" | "source_id" | "target_id">;

const TICKS = 300;
const MIN_RADIUS = 5;
const MAX_RADIUS = 18;
const PADDING = 40;

export interface MapPoint {
  id: string;
  x: number;
  y: number;
  /** Drawn radius: grows with the number of recorded links. */
  r: number;
  degree: number;
}

export interface MapLayout {
  points: Map<string, MapPoint>;
  /** A viewBox that holds every point, padded. */
  viewBox: { x: number; y: number; width: number; height: number };
}

interface SimNode extends SimulationNodeDatum {
  id: string;
  r: number;
  linked: boolean;
}

export function degrees(nodes: GraphNode[], relations: EdgeLike[]): Map<string, number> {
  const counts = new Map(nodes.map((node) => [node.id, 0]));
  for (const edge of relations) {
    if (edge.source_id === edge.target_id) continue;
    counts.set(edge.source_id, (counts.get(edge.source_id) ?? 0) + 1);
    counts.set(edge.target_id, (counts.get(edge.target_id) ?? 0) + 1);
  }
  return counts;
}

export function radiusFor(degree: number): number {
  return Math.min(MAX_RADIUS, MIN_RADIUS + Math.sqrt(degree) * 3.5);
}

export function layoutMemoryMap(nodes: GraphNode[], relations: EdgeLike[]): MapLayout {
  const degree = degrees(nodes, relations);
  // Sorted by id so the seed spiral does not depend on the order the server
  // happened to return the window in (newest first shifts with every write).
  const sim: SimNode[] = [...nodes]
    .sort((a, b) => a.id.localeCompare(b.id))
    .map((node) => {
      const links = degree.get(node.id) ?? 0;
      return { id: node.id, r: radiusFor(links), linked: links > 0 };
    });
  const present = new Set(sim.map((node) => node.id));
  const links: SimulationLinkDatum<SimNode>[] = relations
    .filter((e) => e.source_id !== e.target_id && present.has(e.source_id) && present.has(e.target_id))
    // Link order feeds the simulation too; sort it for the same reason.
    .sort((a, b) => a.id.localeCompare(b.id))
    .map((e) => ({ source: e.source_id, target: e.target_id }));

  forceSimulation(sim)
    .force(
      "link",
      forceLink<SimNode, SimulationLinkDatum<SimNode>>(links)
        .id((node) => node.id)
        .distance(70)
        .strength(0.6),
    )
    .force("charge", forceManyBody<SimNode>().strength(-180))
    .force("collide", forceCollide<SimNode>((node) => node.r + 14))
    // A gentle pull to the middle keeps unlinked records on screen rather
    // than flung to infinity by the charge.
    // Unlinked records get a stronger pull: nothing else holds them, and left
    // to the charge they drift far enough out to shrink everything else.
    .force("x", forceX<SimNode>(0).strength((node) => (node.linked ? 0.05 : 0.2)))
    .force("y", forceY<SimNode>(0).strength((node) => (node.linked ? 0.05 : 0.2)))
    .stop()
    .tick(TICKS);

  const points = new Map<string, MapPoint>();
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const node of sim) {
    const x = node.x ?? 0;
    const y = node.y ?? 0;
    points.set(node.id, { id: node.id, x, y, r: node.r, degree: degree.get(node.id) ?? 0 });
    minX = Math.min(minX, x - node.r);
    minY = Math.min(minY, y - node.r);
    maxX = Math.max(maxX, x + node.r);
    maxY = Math.max(maxY, y + node.r);
  }
  if (points.size === 0) {
    return { points, viewBox: { x: -100, y: -100, width: 200, height: 200 } };
  }
  // A lone record would give a zero-size box; keep a floor so it draws at a
  // sensible scale instead of filling the canvas.
  const width = Math.max(maxX - minX, 240) + PADDING * 2;
  const height = Math.max(maxY - minY, 180) + PADDING * 2;
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  return { points, viewBox: { x: cx - width / 2, y: cy - height / 2, width, height } };
}

export interface GraphShape {
  /** Groups of two or more records joined by recorded links. */
  clusters: number;
  /** Records with no recorded link at all. */
  unlinked: string[];
  /** Records by link count, most first; ties broken by title for a stable order. */
  mostConnected: { id: string; degree: number }[];
}

export function graphShape(nodes: GraphNode[], relations: EdgeLike[]): GraphShape {
  const degree = degrees(nodes, relations);
  const parent = new Map(nodes.map((node) => [node.id, node.id]));
  const find = (id: string): string => {
    let root = id;
    while (parent.get(root) !== root) root = parent.get(root) ?? root;
    parent.set(id, root);
    return root;
  };
  for (const edge of relations) {
    if (!parent.has(edge.source_id) || !parent.has(edge.target_id)) continue;
    parent.set(find(edge.source_id), find(edge.target_id));
  }
  const sizes = new Map<string, number>();
  for (const node of nodes) {
    const root = find(node.id);
    sizes.set(root, (sizes.get(root) ?? 0) + 1);
  }
  const title = new Map(nodes.map((node) => [node.id, node.title]));
  return {
    clusters: [...sizes.values()].filter((size) => size >= 2).length,
    unlinked: nodes.filter((node) => (degree.get(node.id) ?? 0) === 0).map((node) => node.id),
    mostConnected: nodes
      .map((node) => ({ id: node.id, degree: degree.get(node.id) ?? 0 }))
      .filter((entry) => entry.degree > 0)
      .sort(
        (a, b) =>
          b.degree - a.degree || (title.get(a.id) ?? "").localeCompare(title.get(b.id) ?? ""),
      ),
  };
}

/** The ids one record is linked to, either direction. */
export function neighbourhood(id: string, relations: EdgeLike[]): Set<string> {
  const near = new Set([id]);
  for (const edge of relations) {
    if (edge.source_id === id) near.add(edge.target_id);
    if (edge.target_id === id) near.add(edge.source_id);
  }
  return near;
}
