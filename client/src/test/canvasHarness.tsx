/**
 * The canvas page under test: its fixtures, its fake server, and the DOM contract
 * its specs read it back through.
 *
 * The sibling of `gridHarness`, and deliberately the same shape. Each spec file
 * still declares its own `jest.mock("../../lib/viewsApi", …)`: the call is hoisted
 * per module and the registry is per file, so the mocked `fetchView`/`updateView`
 * this module imports are that file's own.
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
 * There is no layout engine here. Nothing has a size unless it is stubbed,
 * nothing reflows, `scrollLeft` never moves because nothing overflows, and no
 * CSS transform is ever composited. So **no test below measures anything**:
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
 *   - `RECT` stubs one fixed 1000x800 box for every element, which is what makes
 *     a drag's arithmetic exercisable at all: a pointer that moves 120px moves
 *     the item 120 surface pixels at 100% zoom.
 *
 * What *is* asserted is the stored consequence of each gesture, and what the
 * surface renders from it.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { fetchView, updateView, type ViewSummary } from "../lib/viewsApi";
import { ViewPage } from "../pages/ViewPage";
import { SidebarWorkspaceProvider } from "../state/SidebarWorkspaceContext";
import { useToastStore } from "../state/toastStore";

export type Json = Record<string, unknown>;

export const SLUG = "loregarden";
export const VIEW_ID = "v-canvas";
export const POINTER_ID = 7;

export const mockFetchView = fetchView as jest.MockedFunction<typeof fetchView>;
export const mockUpdateView = updateView as jest.MockedFunction<typeof updateView>;

/** One fixed box for every element, so drag arithmetic has something to divide by. */
export const RECT: DOMRect = {
  x: 0,
  y: 0,
  left: 0,
  top: 0,
  right: 1000,
  bottom: 800,
  width: 1000,
  height: 800,
  toJSON: () => ({}),
} as DOMRect;

/**
 * jsdom implements no `PointerEvent`, and RTL's `fireEvent.pointerDown` falls
 * back to a bare `Event` when the constructor is missing — which silently drops
 * `clientX`, making every drag below a drag to the origin.
 */
class FakePointerEvent extends MouseEvent {
  readonly pointerId: number;
  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 1;
  }
}

// ---------------------------------------------------------------------------
// Fixtures. Factories, never constants: the mocked `fetchView` hands the caller
// whatever object it is given, so a shared literal would let one test's in-place
// edit corrupt the next test's input.
// ---------------------------------------------------------------------------

export const panelContainer = (): Json => ({ kind: "panel", settings: {} });

export const terminalContainer = (): Json => ({
  kind: "terminal",
  settings: { primitive_id: "terminal", workspace_slug: SLUG },
});

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

/** `count` placed containers — for the ceilings the server enforces. */
export function fullCanvas(count: number): Json {
  const containers: Json = {};
  const items: Json[] = [];
  for (let index = 0; index < count; index += 1) {
    containers[`c-${index}`] = panelContainer();
    items.push(
      canvasItem({ id: `i-${index}`, container_id: `c-${index}`, x: index, y: 0, z_index: index }),
    );
  }
  return { kind: "canvas", containers, items };
}

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

/** The record the fake server holds; PATCHes land in it, so a reload sees them. */
let stored: ViewSummary;

/**
 * What a read issued now would resolve with.
 *
 * A getter rather than the binding itself, because the fake server *replaces* the
 * record on every PATCH — a test holding the old object is holding the layout
 * from before that write.
 */
export function storedView(): ViewSummary {
  return stored;
}

export function storePatch(patch: unknown): ViewSummary {
  stored = { ...stored, ...(patch as Partial<ViewSummary>) };
  return stored;
}

export function testClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false, retryDelay: 0 } },
  });
}

export function canvasRoute(client: QueryClient) {
  return (
    <QueryClientProvider client={client}>
      <SidebarWorkspaceProvider slug={SLUG}>
        <MemoryRouter initialEntries={[`/view/${VIEW_ID}`]}>
          <Routes>
            <Route path="/view/:viewId" element={<ViewPage />} />
          </Routes>
        </MemoryRouter>
      </SidebarWorkspaceProvider>
    </QueryClientProvider>
  );
}

export function renderCanvas(layout: Json, client?: QueryClient) {
  stored = viewOf(layout);
  return render(canvasRoute(client ?? testClient()));
}

export async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

let rectSpy: jest.SpyInstance;

/** What every element measures from here on — the viewport, effectively. */
export function setMeasuredRect(rect: DOMRect): void {
  rectSpy.mockReturnValue(rect);
}

/**
 * The fake server, the stubbed rect and the fake `PointerEvent`, installed for a
 * whole spec file.
 *
 * Called at module scope, so the `beforeEach` it registers runs before each
 * test's own — which is what lets a test override `fetchView` for one case
 * without leaking it into the next.
 */
export function installCanvasHarness(): void {
  beforeAll(() => {
    (globalThis as unknown as { PointerEvent: unknown }).PointerEvent = FakePointerEvent;
    // jsdom has neither; a renderer that captures the pointer would otherwise die
    // on `undefined is not a function` rather than fail the assertion that wants it.
    Element.prototype.setPointerCapture = function setPointerCapture() {};
    Element.prototype.releasePointerCapture = function releasePointerCapture() {};
  });

  beforeEach(() => {
    jest.clearAllMocks();
    window.localStorage.clear();
    useToastStore.getState().clear();
    rectSpy = jest.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue(RECT);
    mockFetchView.mockImplementation(async () => stored);
    mockUpdateView.mockImplementation(async (_slug, _viewId, patch) => {
      stored = { ...stored, ...(patch as Partial<ViewSummary>) };
      return stored;
    });
  });

  afterEach(() => {
    rectSpy.mockRestore();
  });
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

/** The layout of the most recent PATCH. */
export function lastLayout(): Json {
  const calls = mockUpdateView.mock.calls;
  if (calls.length === 0) throw new Error("No layout was written");
  return (calls[calls.length - 1][2] as { layout: Json }).layout;
}

export function itemsOf(layout: Json): Json[] {
  return layout.items as Json[];
}

export function itemById(layout: Json, itemId: string): Json {
  const found = itemsOf(layout).find((item) => item.id === itemId);
  if (found === undefined) throw new Error(`No stored item ${itemId}`);
  return found;
}

export function containersOf(layout: Json): Record<string, Json> {
  return layout.containers as Record<string, Json>;
}

// ---------------------------------------------------------------------------
// Driving a gesture
// ---------------------------------------------------------------------------

export function pointerDown(el: HTMLElement, clientX: number, clientY: number): boolean {
  return fireEvent.pointerDown(el, {
    pointerId: POINTER_ID,
    button: 0,
    buttons: 1,
    clientX,
    clientY,
  });
}

export function pointerMove(el: HTMLElement, clientX: number, clientY: number) {
  fireEvent.pointerMove(el, { pointerId: POINTER_ID, buttons: 1, clientX, clientY });
}

export function pointerUp(el: HTMLElement, clientX: number, clientY: number) {
  fireEvent.pointerUp(el, { pointerId: POINTER_ID, buttons: 0, clientX, clientY });
}

/** A whole gesture: press, two moves, release. */
export function drag(
  el: HTMLElement,
  from: [number, number],
  to: [number, number],
) {
  pointerDown(el, from[0], from[1]);
  pointerMove(el, (from[0] + to[0]) / 2, (from[1] + to[1]) / 2);
  pointerMove(el, to[0], to[1]);
  pointerUp(el, to[0], to[1]);
}
