/**
 * The flex-grid page under test: its fixtures, its fake server, and the DOM
 * contract its specs read it back through.
 *
 * Extracted because more than one spec file drives this page — what the grid
 * *does* (`ViewPageGrid.test.tsx`) and what happens when its writes race
 * something (`ViewPageGridRaces.test.tsx`) — and a harness copied into both is
 * two chances for a fixture to drift from the contract it stands in for.
 *
 * Each spec file still declares its own `jest.mock("../../lib/viewsApi", …)`:
 * the call is hoisted per module and the registry is per file, so the mocked
 * `fetchView`/`updateView` this module imports are that file's own.
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
 * ## What jsdom cannot be asked
 *
 * There is no layout engine here: every `getBoundingClientRect` is zero-sized
 * unless stubbed, and nothing reflows when a flex-grow changes. So no test
 * measures a pane in pixels. `RECT` stubs one fixed 1000x800 box for every
 * element, which is what makes a drag's arithmetic exercisable at all — the
 * renderer must derive its new fractions from a measured rect, and with a
 * 1000px-wide split a pointer at clientX 700 is the 0.7 mark. What is asserted
 * is the *stored* consequence of a drag and what the tree renders from it; that
 * the browser then paints those fractions is flexbox's job.
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
 * `clientX`, making every drag below a drag to the origin. The subclass is the
 * smallest thing that carries coordinates and a pointer id.
 */
class FakePointerEvent extends MouseEvent {
  readonly pointerId: number;
  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 1;
  }
}

export const POINTER_ID = 7;

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

export const panelContainer = (): Json => ({ kind: "panel", settings: {} });

/** A terminal that is actually configured — `newContainerFor` leaves the slug empty. */
export const terminalContainer = (): Json => ({
  kind: "terminal",
  settings: { primitive_id: "terminal", workspace_slug: SLUG },
});

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
    id: "v-grid",
    kind: "flex_grid",
    title: "Build Board",
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
 * A getter rather than the binding itself, because the fake server *replaces*
 * the record on every PATCH — a test holding the old object is holding the
 * layout from before that write, which is exactly what a stale read carries.
 */
export function storedView(): ViewSummary {
  return stored;
}

/**
 * Apply a PATCH body to the fake server's record, and return the result.
 *
 * For the tests that replace `updateView` with a deferred promise: the record
 * has to change when the request is *made*, as the server's would, not when the
 * test chooses to resolve it.
 */
export function storePatch(patch: unknown): ViewSummary {
  stored = { ...stored, ...(patch as Partial<ViewSummary>) };
  return stored;
}

export function testClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false, retryDelay: 0 } },
  });
}

export function gridRoute(client: QueryClient) {
  return (
    <QueryClientProvider client={client}>
      <SidebarWorkspaceProvider slug={SLUG}>
        <MemoryRouter initialEntries={["/view/v-grid"]}>
          <Routes>
            <Route path="/view/:viewId" element={<ViewPage />} />
          </Routes>
        </MemoryRouter>
      </SidebarWorkspaceProvider>
    </QueryClientProvider>
  );
}

export function renderGrid(layout: Json, client?: QueryClient) {
  stored = viewOf(layout);
  return render(gridRoute(client ?? testClient()));
}

export async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

/**
 * The fake server, the stubbed rect and the fake `PointerEvent`, installed for
 * a whole spec file.
 *
 * Called at module scope, so the `beforeEach` it registers runs before each
 * test's own — which is what lets a test override `fetchView` for one case
 * without leaking it into the next.
 */
let rectSpy: jest.SpyInstance;

/** What every element measures from here on — the viewport, effectively. */
export function setMeasuredRect(rect: DOMRect): void {
  rectSpy.mockReturnValue(rect);
}

export function installGridHarness(): void {

  beforeAll(() => {
    (globalThis as unknown as { PointerEvent: unknown }).PointerEvent = FakePointerEvent;
    // jsdom has neither; a renderer that captures the pointer would otherwise die
    // on `undefined is not a function` rather than fail the assertion that wants it.
    Element.prototype.setPointerCapture = function setPointerCapture() {};
    Element.prototype.releasePointerCapture = function releasePointerCapture() {};
  });

  beforeEach(() => {
    jest.clearAllMocks();
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

/** The layout of the most recent PATCH. */
export function lastLayout(): Json {
  const calls = mockUpdateView.mock.calls;
  if (calls.length === 0) throw new Error("No layout was written");
  return (calls[calls.length - 1][2] as { layout: Json }).layout;
}

export function containersOf(layout: Json): Record<string, Json> {
  return layout.containers as Record<string, Json>;
}

export function childrenOf(node: Json): Json[] {
  return node.children as Json[];
}

// ---------------------------------------------------------------------------
// Driving a divider
// ---------------------------------------------------------------------------

type Axis = "x" | "y";

export function at(axis: Axis, value: number): { clientX: number; clientY: number } {
  return axis === "x" ? { clientX: value, clientY: 400 } : { clientX: 500, clientY: value };
}

/** Returns false when the handler called `preventDefault` — AC7's text-selection half. */
export function pointerDown(el: HTMLElement, axis: Axis, value: number): boolean {
  return fireEvent.pointerDown(el, {
    pointerId: POINTER_ID,
    button: 0,
    buttons: 1,
    ...at(axis, value),
  });
}

export function pointerMove(el: HTMLElement, axis: Axis, value: number) {
  fireEvent.pointerMove(el, { pointerId: POINTER_ID, buttons: 1, ...at(axis, value) });
}

export function pointerUp(el: HTMLElement, axis: Axis, value: number) {
  fireEvent.pointerUp(el, { pointerId: POINTER_ID, buttons: 0, ...at(axis, value) });
}

/** A whole drag: press, two moves, release. */
export function dragDivider(el: HTMLElement, axis: Axis, from: number, to: number) {
  pointerDown(el, axis, from);
  pointerMove(el, axis, (from + to) / 2);
  pointerMove(el, axis, to);
  pointerUp(el, axis, to);
}
