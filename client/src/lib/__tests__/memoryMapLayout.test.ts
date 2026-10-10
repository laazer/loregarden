import type { GraphNode, GraphRelation } from "../../api/memoryApi";
import { graphShape, layoutMemoryMap, neighbourhood, radiusFor } from "../memoryMapLayout";

function node(id: string, title = `Record ${id}`): GraphNode {
  return {
    id,
    title,
    excerpt: "",
    node_type: "learning",
    tags: [],
    ticket_id: "",
    discredited: false,
    created_at: "2026-09-01T00:00:00",
    updated_at: "2026-09-01T00:00:00",
    origin_kind: null,
    origin_ref: null,
  };
}

function edge(source: string, target: string, relation = "related"): GraphRelation {
  return {
    id: `${source}-${target}`,
    source_id: source,
    target_id: target,
    relation_type: relation,
    created_at: "2026-09-01T00:00:00",
  };
}

const NODES = ["a", "b", "c", "d", "e"].map((id) => node(id));
const EDGES = [edge("a", "b"), edge("b", "c"), edge("a", "c", "contradicts")];

const distance = (p: { x: number; y: number }, q: { x: number; y: number }) =>
  Math.hypot(p.x - q.x, p.y - q.y);

it("draws the same graph the same way, whatever order the server sent it in", () => {
  const first = layoutMemoryMap(NODES, EDGES);
  const second = layoutMemoryMap([...NODES].reverse(), [...EDGES].reverse());
  for (const id of ["a", "b", "c", "d", "e"]) {
    expect(second.points.get(id)?.x).toBeCloseTo(first.points.get(id)?.x ?? NaN, 6);
    expect(second.points.get(id)?.y).toBeCloseTo(first.points.get(id)?.y ?? NaN, 6);
  }
});

it("pulls linked records together and leaves unlinked ones further out", () => {
  const { points } = layoutMemoryMap(NODES, EDGES);
  const a = points.get("a")!;
  const b = points.get("b")!;
  const d = points.get("d")!;
  expect(distance(a, b)).toBeLessThan(distance(a, d));
});

it("sizes a record by its links and keeps every point inside the view box", () => {
  const { points, viewBox } = layoutMemoryMap(NODES, EDGES);
  expect(points.get("a")!.r).toBe(radiusFor(2));
  expect(points.get("d")!.r).toBe(radiusFor(0));
  for (const p of points.values()) {
    expect(p.x - p.r).toBeGreaterThanOrEqual(viewBox.x);
    expect(p.y - p.r).toBeGreaterThanOrEqual(viewBox.y);
    expect(p.x + p.r).toBeLessThanOrEqual(viewBox.x + viewBox.width);
    expect(p.y + p.r).toBeLessThanOrEqual(viewBox.y + viewBox.height);
  }
});

it("lays out nothing without failing", () => {
  expect(layoutMemoryMap([], []).points.size).toBe(0);
});

it("reports clusters, unlinked records and the most connected", () => {
  const shape = graphShape([...NODES, node("f")], [...EDGES, edge("d", "f")]);
  expect(shape.clusters).toBe(2);
  expect(shape.unlinked).toEqual(["e"]);
  expect(shape.mostConnected.slice(0, 3).map((entry) => entry.id)).toEqual(["a", "b", "c"]);
});

it("finds a record's neighbours in both directions", () => {
  expect([...neighbourhood("b", EDGES)].sort()).toEqual(["a", "b", "c"]);
});
