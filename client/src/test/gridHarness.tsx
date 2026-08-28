/**
 * The flex-grid page under test: its fixtures and the DOM contract its specs
 * read it back through.
 *
 * Everything a view-page harness has regardless of what it renders — the fake
 * server, the provider tree, the jsdom stubs, the readers for what was written —
 * is `viewHarness`, shared with `canvasHarness` and re-exported here so a spec
 * has one import.
 *
 * Extracted because more than one spec file drives this page — what the grid
 * *does* (`ViewPageGrid.test.tsx`) and what happens when its writes race
 * something (`ViewPageGridRaces.test.tsx`) — and a harness copied into both is
 * two chances for a fixture to drift from the contract it stands in for.
 *
 * ## The DOM contract these tests hold the renderer to
 *
 * The renderer is free in almost every respect; four seams are pinned because a
 * test cannot find a control it cannot name, and because pinning the *wording*
 * of a header button would pin copy no acceptance criterion chooses.
 *
 *   - `[data-grid-node="<node.id>"]` wraps every node of the tree, leaf and
 *     split alike, and is the element carrying that node's flex sizing. Sizes
 *     are read back as `style.flexGrow`, never as an attribute the renderer
 *     also writes: the grow factor is what actually lays the pane out, and a
 *     `data-size` that disagreed with it would be a passing test over a broken
 *     screen.
 *   - `[data-grid-action="split-horizontal" | "split-vertical" | "close" |
 *     "pick-primitive"]` are `<button>`s inside the leaf's own
 *     `[data-grid-node]`. Inside it, because AC1 puts the split control "on the
 *     container, not in a global toolbar"; `<button>`s, because AC9 wants close
 *     operable from the keyboard and a `<div onClick>` is not.
 *   - `[data-grid-divider="<split.id>:<gapIndex>"]` is the handle between
 *     children `gapIndex` and `gapIndex + 1` of that split.
 *   - Dividers speak **Pointer Events** (`pointerdown`/`pointermove`/
 *     `pointerup`). Not a stylistic choice: pointer capture is the platform's
 *     answer to AC7 ("does not send pointer events to the container beneath"),
 *     and it is a pointer-event API.
 *
 * What jsdom cannot be asked of any of this is `viewHarness`'s header: nothing
 * reflows, so no test below measures a pane in pixels. What is asserted is the
 * *stored* consequence of a drag and what the tree renders from it.
 */

import type { QueryClient } from "@tanstack/react-query";

import type { ViewSummary } from "../lib/viewsApi";
import {
  drag,
  panelContainer,
  pointerDown as pressPointer,
  pointerMove as movePointer,
  pointerUp as releasePointer,
  renderView,
  viewRoute,
  type Json,
} from "./viewHarness";

export {
  POINTER_ID,
  RECT,
  SLUG,
  containersOf,
  installViewHarness as installGridHarness,
  lastLayout,
  mockFetchView,
  mockUpdateView,
  panelContainer,
  setMeasuredRect,
  settle,
  storePatch,
  storedView,
  terminalContainer,
  testClient,
  type Json,
} from "./viewHarness";

const VIEW_ID = "v-grid";

/**
 * The thinnest pane AC9 is willing to call usable, in pixels of `RECT`'s track.
 *
 * No acceptance criterion names a number — "minimum container sizes prevent a
 * drag from producing an unusable sliver" is the whole of it — so this is a
 * floor the tests pick and the renderer is free to exceed. It is stated once,
 * here, rather than spelled into the assertion: the server's own rule is only
 * `size > 0`, and a renderer that stopped there would satisfy the schema with a
 * pane one pixel wide.
 */
export const MIN_PANE_PX = 24;

// ---------------------------------------------------------------------------
// Fixtures. Factories, never constants: the mocked `fetchView` hands the caller
// whatever object it is given, so a shared literal would let one test's in-place
// edit corrupt the next test's input.
// ---------------------------------------------------------------------------

export const leafLayout = (container: Json = panelContainer()): Json => ({
  kind: "flex_grid",
  containers: { "c-seed": container },
  root: { node: "leaf", id: "n-seed", size: 1, container_id: "c-seed" },
});

export const pairLayout = (
  sizes: [number, number] = [0.5, 0.5],
  orientation: "horizontal" | "vertical" = "horizontal",
  containers: [Json, Json] = [panelContainer(), panelContainer()],
): Json => ({
  kind: "flex_grid",
  containers: { "c-1": containers[0], "c-2": containers[1] },
  root: {
    node: "split",
    id: "n-root",
    size: 1,
    orientation,
    children: [
      { node: "leaf", id: "n-1", size: sizes[0], container_id: "c-1" },
      { node: "leaf", id: "n-2", size: sizes[1], container_id: "c-2" },
    ],
  },
});

/** Three siblings, deliberately uneven, so "redistribute" is not "halve". */
export const tripleLayout = (
  containers: [Json, Json, Json] = [panelContainer(), panelContainer(), panelContainer()],
): Json => ({
  kind: "flex_grid",
  containers: { "c-1": containers[0], "c-2": containers[1], "c-3": containers[2] },
  root: {
    node: "split",
    id: "n-root",
    size: 1,
    orientation: "horizontal",
    children: [
      { node: "leaf", id: "n-1", size: 0.5, container_id: "c-1" },
      { node: "leaf", id: "n-2", size: 0.25, container_id: "c-2" },
      { node: "leaf", id: "n-3", size: 0.25, container_id: "c-3" },
    ],
  },
});

/** A split inside a split, so a collapse has a real parent size to inherit. */
export const nestedLayout = (): Json => ({
  kind: "flex_grid",
  containers: { "c-1": panelContainer(), "c-2": panelContainer(), "c-3": panelContainer() },
  root: {
    node: "split",
    id: "n-root",
    size: 1,
    orientation: "horizontal",
    children: [
      { node: "leaf", id: "n-1", size: 0.6, container_id: "c-1" },
      {
        node: "split",
        id: "n-inner",
        size: 0.4,
        orientation: "vertical",
        children: [
          { node: "leaf", id: "n-2", size: 0.5, container_id: "c-2" },
          { node: "leaf", id: "n-3", size: 0.5, container_id: "c-3" },
        ],
      },
    ],
  },
});

/**
 * `depth` nested splits, each holding a leaf and the next split — the deepest
 * tree the server accepts when `depth === MAX_SPLIT_DEPTH`.
 */
export function chainLayout(depth: number): Json {
  const containers: Json = {};
  let node: Json = { node: "leaf", id: "n-tail", size: 0.5, container_id: "c-tail" };
  containers["c-tail"] = panelContainer();
  for (let level = depth - 1; level >= 0; level -= 1) {
    const containerId = `c-${level}`;
    containers[containerId] = panelContainer();
    node = {
      node: "split",
      id: `n-split-${level}`,
      size: level === 0 ? 1 : 0.5,
      orientation: "horizontal",
      children: [{ node: "leaf", id: `n-leaf-${level}`, size: 0.5, container_id: containerId }, node],
    };
  }
  return { kind: "flex_grid", containers, root: node };
}

/**
 * One split holding `panes` leaves — the widest grid the server accepts when
 * `panes === MAX_CONTAINERS`.
 *
 * Flat rather than nested so the depth cap has nothing to say about it: the only
 * rule a further split can break here is the registry's own `max_length`.
 */
export function wideLayout(panes: number): Json {
  const containers: Json = {};
  const children: Json[] = [];
  for (let index = 0; index < panes; index += 1) {
    containers[`c-${index}`] = panelContainer();
    children.push({ node: "leaf", id: `n-${index}`, size: 1 / panes, container_id: `c-${index}` });
  }
  return {
    kind: "flex_grid",
    containers,
    root: { node: "split", id: "n-root", size: 1, orientation: "horizontal", children },
  };
}

/**
 * `panes` leaves, each wrapped in a split of its own — `1 + 2 * panes` nodes for
 * `panes` containers.
 *
 * A one-child split is legal (`children` is `min_length=1`, and a lone sibling
 * sums to 1.0 by itself), and it is the only way to reach the node ceiling with
 * containers to spare. Splitting cannot *produce* this shape, but the server
 * stores it, so a grid can be opened on one.
 */
export function tallLayout(panes: number): Json {
  const containers: Json = {};
  const children: Json[] = [];
  for (let index = 0; index < panes; index += 1) {
    containers[`c-${index}`] = panelContainer();
    children.push({
      node: "split",
      id: `n-wrap-${index}`,
      size: 1 / panes,
      orientation: "vertical",
      children: [{ node: "leaf", id: `n-${index}`, size: 1, container_id: `c-${index}` }],
    });
  }
  return {
    kind: "flex_grid",
    containers,
    root: { node: "split", id: "n-root", size: 1, orientation: "horizontal", children },
  };
}

export function viewOf(layout: Json): ViewSummary {
  return {
    id: VIEW_ID,
    kind: "flex_grid",
    title: "Build Board",
    icon: "",
    layout,
    // A grid arranges itself to the pane it is drawn in, so it has no pan or
    // zoom to remember: its stored viewport is always the absent one.
    viewport: {},
    created_at: "2026-08-01T00:00:00",
    updated_at: "2026-08-01T00:00:00",
  };
}

export function gridRoute(client: QueryClient) {
  return viewRoute(VIEW_ID, client);
}

export function renderGrid(layout: Json, client?: QueryClient) {
  return renderView(viewOf(layout), client);
}

// ---------------------------------------------------------------------------
// Reading the rendered tree
// ---------------------------------------------------------------------------

export function gridNode(root: HTMLElement, nodeId: string): HTMLElement {
  const found = root.querySelector<HTMLElement>(`[data-grid-node="${nodeId}"]`);
  if (found === null) throw new Error(`No rendered node ${nodeId}`);
  return found;
}

/** The grow factor the pane is actually laid out with. */
export function sizeOf(root: HTMLElement, nodeId: string): number {
  return Number.parseFloat(gridNode(root, nodeId).style.flexGrow);
}

export function control(root: HTMLElement, nodeId: string, action: string): HTMLElement {
  const found = gridNode(root, nodeId).querySelector<HTMLElement>(
    `button[data-grid-action="${action}"]`,
  );
  if (found === null) throw new Error(`No ${action} control on node ${nodeId}`);
  return found;
}

export function divider(root: HTMLElement, splitId: string, gap = 0): HTMLElement {
  const found = root.querySelector<HTMLElement>(`[data-grid-divider="${splitId}:${gap}"]`);
  if (found === null) throw new Error(`No divider ${splitId}:${gap}`);
  return found;
}

export function childrenOf(node: Json): Json[] {
  return node.children as Json[];
}

// ---------------------------------------------------------------------------
// Driving a divider — one axis at a time, since a divider only moves along one.
// ---------------------------------------------------------------------------

type Axis = "x" | "y";

export function at(axis: Axis, value: number): { clientX: number; clientY: number } {
  return axis === "x" ? { clientX: value, clientY: 400 } : { clientX: 500, clientY: value };
}

function point(axis: Axis, value: number): [number, number] {
  const { clientX, clientY } = at(axis, value);
  return [clientX, clientY];
}

/** Returns false when the handler called `preventDefault` — AC7's text-selection half. */
export function pointerDown(el: HTMLElement, axis: Axis, value: number): boolean {
  return pressPointer(el, ...point(axis, value));
}

export function pointerMove(el: HTMLElement, axis: Axis, value: number) {
  movePointer(el, ...point(axis, value));
}

export function pointerUp(el: HTMLElement, axis: Axis, value: number) {
  releasePointer(el, ...point(axis, value));
}

/** A whole drag: press, two moves, release. */
export function dragDivider(el: HTMLElement, axis: Axis, from: number, to: number) {
  drag(el, point(axis, from), point(axis, to));
}
