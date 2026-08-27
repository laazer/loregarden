/**
 * The canvas page under test: its fixtures and the DOM contract its specs read
 * it back through.
 *
 * Everything a view-page harness has regardless of what it renders — the fake
 * server, the provider tree, the jsdom stubs, the readers for what was written —
 * is `viewHarness`, shared with `gridHarness` and re-exported here so a spec has
 * one import.
 *
 * ## The DOM contract these tests hold the renderer to
 *
 * The renderer is free in almost every respect; five seams are pinned, because a
 * test cannot find a control it cannot name, and because pinning the *wording* of
 * a header button would pin copy no acceptance criterion chooses.
 *
 *   - `[data-canvas-item="<item.id>"]` wraps every placed container and is the
 *     element carrying that item's absolute position, size and `z-index`.
 *     Geometry is read back as `style.left`/`top`/`width`/`height`, never as an
 *     attribute the renderer also writes: those are what actually place the item,
 *     and a `data-x` that disagreed with them would be a passing test over a
 *     broken screen.
 *   - `[data-canvas-drag="<item.id>"]` is the element a move gesture starts on;
 *     `[data-canvas-resize="<item.id>:<dir>"]` are the eight edge and corner
 *     handles, `dir` being one of `n s e w nw ne sw se`.
 *   - `[data-canvas-action=…]` are `<button>`s: `add-container`,
 *     `fit-to-content`, `zoom-in`, `zoom-out`, `zoom-reset` on the toolbar, and
 *     `pick-primitive`, `bring-to-front`, `send-to-back`, `close` inside an
 *     item's own header. `<button>`s, because AC10 wants these reachable from the
 *     keyboard and a `<div onClick>` is not.
 *   - `[data-canvas-surface]` is the transformed surface and `[data-canvas-sizer]`
 *     the element whose size gives the viewport something to scroll.
 *   - Gestures speak **Pointer Events**. Not a stylistic choice: pointer capture
 *     is the platform's answer to "the drag must not reach the terminal
 *     underneath it", and it is a pointer-event API.
 *
 * ## What jsdom cannot be asked
 *
 * `viewHarness`'s header has the general form of it; the canvas's own corollaries
 * are that nothing has a size unless stubbed, `scrollLeft` never moves because
 * nothing overflows, and no CSS transform is ever composited. So **no test below
 * measures anything**:
 *
 *   - Pixel-accuracy at 100% zoom (AC5) is asserted as its *structural cause* —
 *     that the surface carries no `transform` at all at `zoom === 1` — and not as
 *     a measurement. Whether a terminal's glyphs are then crisp, and whether a
 *     click inside an iframe lands on the right element, is browser behaviour
 *     this suite cannot observe and does not claim to.
 *   - Panning is asserted as the *absence* of a wheel handler on the plain-wheel
 *     path plus an `overflow: auto` viewport, which is what makes the browser
 *     give a scrollable container the wheel first. That the browser then chains
 *     the scroll is the browser's job.
 *   - `RECT`'s fixed box is what makes a drag's arithmetic exercisable at all: a
 *     pointer that moves 120px moves the item 120 surface pixels at 100% zoom.
 *
 * What *is* asserted is the stored consequence of each gesture, and what the
 * surface renders from it.
 */

import type { QueryClient } from "@tanstack/react-query";

import type { ViewSummary } from "../lib/viewsApi";
import { panelContainer, renderView, viewRoute, type Json } from "./viewHarness";

export {
  POINTER_ID,
  RECT,
  SLUG,
  containersOf,
  drag,
  installViewHarness as installCanvasHarness,
  lastLayout,
  mockFetchView,
  mockUpdateView,
  panelContainer,
  pointerDown,
  pointerMove,
  pointerUp,
  setMeasuredRect,
  settle,
  storePatch,
  storedView,
  terminalContainer,
  testClient,
  type Json,
} from "./viewHarness";

export const VIEW_ID = "v-canvas";

// ---------------------------------------------------------------------------
// Fixtures. Factories, never constants: the mocked `fetchView` hands the caller
// whatever object it is given, so a shared literal would let one test's in-place
// edit corrupt the next test's input.
// ---------------------------------------------------------------------------

export const emptyCanvas = (): Json => ({ kind: "canvas", containers: {}, items: [] });

export function canvasItem(overrides: Partial<Json> = {}): Json {
  return {
    id: "i-1",
    container_id: "c-1",
    x: 100,
    y: 80,
    width: 400,
    height: 300,
    z_index: 0,
    ...overrides,
  };
}

/** One placed container. */
export const oneItemCanvas = (container: Json = panelContainer()): Json => ({
  kind: "canvas",
  containers: { "c-1": container },
  items: [canvasItem()],
});

/** Two overlapping containers, which is the canvas's whole difference from the grid. */
export const overlappingCanvas = (): Json => ({
  kind: "canvas",
  containers: { "c-1": panelContainer(), "c-2": panelContainer() },
  items: [
    canvasItem({ id: "i-1", container_id: "c-1", x: 100, y: 80, z_index: 0 }),
    canvasItem({ id: "i-2", container_id: "c-2", x: 200, y: 140, z_index: 1 }),
  ],
});

/** Three, so "raise" is distinguishable from "swap with the one above". */
export const threeItemCanvas = (): Json => ({
  kind: "canvas",
  containers: { "c-1": panelContainer(), "c-2": panelContainer(), "c-3": panelContainer() },
  items: [
    canvasItem({ id: "i-1", container_id: "c-1", x: 0, y: 0, z_index: 0 }),
    canvasItem({ id: "i-2", container_id: "c-2", x: 60, y: 60, z_index: 1 }),
    canvasItem({ id: "i-3", container_id: "c-3", x: 120, y: 120, z_index: 2 }),
  ],
});

export function viewOf(layout: Json): ViewSummary {
  return {
    id: VIEW_ID,
    kind: "canvas",
    title: "Sketch Surface",
    icon: "",
    layout,
    created_at: "2026-08-01T00:00:00",
    updated_at: "2026-08-01T00:00:00",
  };
}

export function canvasRoute(client: QueryClient) {
  return viewRoute(VIEW_ID, client);
}

export function renderCanvas(layout: Json, client?: QueryClient) {
  return renderView(viewOf(layout), client);
}

// ---------------------------------------------------------------------------
// Reading the rendered surface
// ---------------------------------------------------------------------------

export function canvasItemEl(root: HTMLElement, itemId: string): HTMLElement {
  const found = root.querySelector<HTMLElement>(`[data-canvas-item="${itemId}"]`);
  if (found === null) throw new Error(`No rendered item ${itemId}`);
  return found;
}

/** Where the item is actually drawn, in the CSS that actually places it. */
export function drawnBox(root: HTMLElement, itemId: string) {
  const style = canvasItemEl(root, itemId).style;
  return {
    x: Number.parseFloat(style.left),
    y: Number.parseFloat(style.top),
    width: Number.parseFloat(style.width),
    height: Number.parseFloat(style.height),
    zIndex: Number.parseFloat(style.zIndex),
  };
}

export function dragHandle(root: HTMLElement, itemId: string): HTMLElement {
  const found = root.querySelector<HTMLElement>(`[data-canvas-drag="${itemId}"]`);
  if (found === null) throw new Error(`No drag handle for item ${itemId}`);
  return found;
}

export function resizeHandle(root: HTMLElement, itemId: string, direction: string): HTMLElement {
  const found = root.querySelector<HTMLElement>(
    `[data-canvas-resize="${itemId}:${direction}"]`,
  );
  if (found === null) throw new Error(`No ${direction} resize handle for item ${itemId}`);
  return found;
}

export function toolbarAction(root: HTMLElement, action: string): HTMLElement {
  const found = root.querySelector<HTMLElement>(`button[data-canvas-action="${action}"]`);
  if (found === null) throw new Error(`No ${action} control`);
  return found;
}

export function itemAction(root: HTMLElement, itemId: string, action: string): HTMLElement {
  const found = canvasItemEl(root, itemId).querySelector<HTMLElement>(
    `button[data-canvas-action="${action}"]`,
  );
  if (found === null) throw new Error(`No ${action} control on item ${itemId}`);
  return found;
}

export function surfaceEl(root: HTMLElement): HTMLElement {
  const found = root.querySelector<HTMLElement>("[data-canvas-surface]");
  if (found === null) throw new Error("No canvas surface");
  return found;
}

export function viewportEl(root: HTMLElement): HTMLElement {
  const found = root.querySelector<HTMLElement>("[data-canvas-viewport]");
  if (found === null) throw new Error("No canvas viewport");
  return found;
}

export function itemsOf(layout: Json): Json[] {
  return layout.items as Json[];
}

export function itemById(layout: Json, itemId: string): Json {
  const found = itemsOf(layout).find((item) => item.id === itemId);
  if (found === undefined) throw new Error(`No stored item ${itemId}`);
  return found;
}
