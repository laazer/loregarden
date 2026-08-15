/**
 * The oracle's own test.
 *
 * `viewLayoutContract.ts` is a hand transcription of
 * `server/loregarden/models/domain/view_layout.py`, and four suites lean on it
 * to decide whether a layout the client composed would survive the round trip.
 * A transcription that drifts loose does not fail — it silently stops refusing
 * things, and every one of those suites gets weaker without a single red test.
 *
 * Each case below was run against the real `parse_view_layout` and agreed with
 * it, both ways round, when this file was written. Adding a rule to the model
 * means adding its case here.
 */

import { assertServerAcceptableLayout } from "../viewLayoutContract";

const grid = () => ({
  kind: "flex_grid",
  containers: {
    "c-left": { kind: "terminal", settings: { primitive_id: "terminal" } },
    "c-right": { kind: "panel", settings: {} },
  },
  root: {
    node: "split",
    id: "n-root",
    size: 1,
    orientation: "horizontal",
    children: [
      { node: "leaf", id: "n-left", size: 0.5, container_id: "c-left" },
      { node: "leaf", id: "n-right", size: 0.5, container_id: "c-right" },
    ],
  },
});

const canvas = () => ({
  kind: "canvas",
  containers: { "c-a": { kind: "web_embed", settings: {} } },
  items: [{ id: "i-a", container_id: "c-a", x: 10, y: 20, width: 300, height: 200, z_index: 0 }],
});

it("accepts the good ones", () => {
  assertServerAcceptableLayout(grid());
  assertServerAcceptableLayout(canvas());
  assertServerAcceptableLayout({ kind: "canvas", containers: {}, items: [] });
  assertServerAcceptableLayout({
    kind: "flex_grid",
    containers: { a: { kind: "panel", settings: {} } },
    root: { node: "leaf", id: "n", size: 1, container_id: "a" },
  });
  // size and settings and z_index are all defaulted
  assertServerAcceptableLayout({
    kind: "flex_grid",
    containers: { a: { kind: "panel" } },
    root: { node: "leaf", id: "n", container_id: "a" },
  });
  // sibling sums within tolerance
  const l = grid();
  l.root.children[0].size = 1 / 3;
  l.root.children[1].size = 2 / 3;
  assertServerAcceptableLayout(l);
});

const bad: [string, unknown][] = [
  ["unknown view kind", { kind: "kanban", containers: {}, items: [] }],
  ["empty grid", { kind: "flex_grid", containers: {}, root: null }],
  ["null root", { ...grid(), root: null }],
  [
    "orphan container",
    { ...grid(), containers: { ...grid().containers, extra: { kind: "panel", settings: {} } } },
  ],
  [
    "unknown reference",
    (() => {
      const l = grid();
      l.root.children[0].container_id = "nope";
      return l;
    })(),
  ],
  [
    "duplicate node id",
    (() => {
      const l = grid();
      l.root.children[1].id = "n-left";
      return l;
    })(),
  ],
  [
    "sibling sizes off",
    (() => {
      const l = grid();
      l.root.children[0].size = 0.7;
      return l;
    })(),
  ],
  [
    "root size not 1",
    (() => {
      const l = grid();
      l.root.size = 0.5;
      return l;
    })(),
  ],
  [
    "zero size child",
    (() => {
      const l = grid();
      l.root.children[0].size = 0;
      l.root.children[1].size = 1;
      return l;
    })(),
  ],
  [
    "extra field on a node",
    (() => {
      const l = grid();
      (l.root as Record<string, unknown>).colour = "red";
      return l;
    })(),
  ],
  ["extra top-level field", { ...grid(), title: "x" }],
  [
    "bad container kind",
    { ...grid(), containers: { ...grid().containers, "c-left": { kind: "iframe", settings: {} } } },
  ],
  ["empty container id", { ...grid(), containers: { ...grid().containers, "": {} } }],
  [
    "bad orientation",
    (() => {
      const l = grid();
      l.root.orientation = "diagonal";
      return l;
    })(),
  ],
  [
    "split with no children",
    { kind: "flex_grid", containers: {}, root: { node: "split", id: "n", size: 1, orientation: "horizontal", children: [] } },
  ],
  [
    "canvas zero width",
    (() => {
      const l = canvas();
      l.items[0].width = 0;
      return l;
    })(),
  ],
  [
    "canvas infinite x",
    (() => {
      const l = canvas();
      l.items[0].x = Infinity;
      return l;
    })(),
  ],
  [
    "canvas item placed twice",
    (() => {
      const l = canvas();
      l.items.push({ ...l.items[0], id: "i-b" });
      return l;
    })(),
  ],
  [
    "deep nesting",
    (() => {
      type N = Record<string, unknown>;
      let node: N = { node: "leaf", id: "leaf", size: 1, container_id: "a" };
      for (let i = 0; i < 40; i += 1) {
        node = { node: "split", id: `s${i}`, size: 1, orientation: "vertical", children: [node] };
      }
      return { kind: "flex_grid", containers: { a: { kind: "panel", settings: {} } }, root: node };
    })(),
  ],
];

it.each(bad)("refuses %s", (_label, layout) => {
  expect(() => assertServerAcceptableLayout(layout)).toThrow();
});
