/**
 * The half of a view-page harness that is the same whatever the view renders:
 * the fake server behind `fetchView`/`updateView`, the provider tree the page is
 * mounted in, the jsdom gaps a pointer gesture falls through, and the readers
 * for what was written.
 *
 * `gridHarness` and `canvasHarness` are the two callers, and each keeps only
 * what genuinely differs — its fixtures and the DOM contract its renderer is
 * held to. The argument is `gridHarness`'s own: a harness copied into two places
 * is two chances for a fixture to drift from the contract it stands in for, and
 * that applies to the harnesses as much as to the specs they serve.
 *
 * Each spec file still declares its own `jest.mock("../../lib/viewsApi", …)`:
 * the call is hoisted per module and the registry is per file, so the mocked
 * `fetchView`/`updateView` this module imports are that file's own.
 *
 * ## What jsdom cannot be asked
 *
 * There is no layout engine here: every `getBoundingClientRect` is zero-sized
 * unless stubbed, and nothing reflows when a style changes. `RECT` stubs one
 * fixed 1000x800 box for every element, which is what makes a drag's arithmetic
 * exercisable at all — a renderer must derive its result from a measured rect,
 * and with a 1000px-wide box a pointer at clientX 700 is the 0.7 mark. What is
 * asserted is the *stored* consequence of a gesture and what the page renders
 * from it; that the browser then paints it is the browser's job.
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

// ---------------------------------------------------------------------------
// Fixtures every view kind places. Factories, never constants: the mocked
// `fetchView` hands the caller whatever object it is given, so a shared literal
// would let one test's in-place edit corrupt the next test's input.
// ---------------------------------------------------------------------------

export const panelContainer = (): Json => ({ kind: "panel", settings: {} });

/** A terminal that is actually configured — `newContainerFor` leaves the slug empty. */
export const terminalContainer = (): Json => ({
  kind: "terminal",
  settings: { primitive_id: "terminal", workspace_slug: SLUG },
});

// ---------------------------------------------------------------------------
// The fake server
// ---------------------------------------------------------------------------

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

export function viewRoute(viewId: string, client: QueryClient) {
  return (
    <QueryClientProvider client={client}>
      <SidebarWorkspaceProvider slug={SLUG}>
        <MemoryRouter initialEntries={[`/view/${viewId}`]}>
          <Routes>
            <Route path="/view/:viewId" element={<ViewPage />} />
          </Routes>
        </MemoryRouter>
      </SidebarWorkspaceProvider>
    </QueryClientProvider>
  );
}

/** Seed the fake server with `view`, and mount the page on it. */
export function renderView(view: ViewSummary, client?: QueryClient) {
  stored = view;
  return render(viewRoute(view.id, client ?? testClient()));
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
export function installViewHarness(): void {
  beforeAll(() => {
    (globalThis as unknown as { PointerEvent: unknown }).PointerEvent = FakePointerEvent;
    // jsdom implements none of the capture API; a renderer that captures the
    // pointer would otherwise die on `undefined is not a function` rather than
    // fail the assertion that wants it.
    //
    // These track rather than shrug, because a renderer may *ask* whether it
    // still holds a pointer before releasing it (the release throws
    // `NotFoundError` otherwise): a stub that always answered `true` would hide
    // the guard and a stub that always answered `false` would hide the release.
    // `captured` is the smallest thing that answers honestly.
    const captured = new WeakMap<Element, Set<number>>();
    Element.prototype.setPointerCapture = function setPointerCapture(pointerId: number) {
      const held = captured.get(this) ?? new Set<number>();
      held.add(pointerId);
      captured.set(this, held);
    };
    Element.prototype.hasPointerCapture = function hasPointerCapture(pointerId: number) {
      return captured.get(this)?.has(pointerId) ?? false;
    };
    Element.prototype.releasePointerCapture = function releasePointerCapture(pointerId: number) {
      const held = captured.get(this);
      // The real one throws rather than shrugging, and a test that never sees the
      // throw cannot tell a guarded release from an unguarded one.
      if (held === undefined || !held.has(pointerId)) {
        throw new DOMException("No active pointer with the given id", "NotFoundError");
      }
      held.delete(pointerId);
    };
  });

  beforeEach(() => {
    jest.clearAllMocks();
    window.localStorage.clear();
    useToastStore.getState().clear();
    rectSpy = jest.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue(RECT);
    mockFetchView.mockImplementation(async () => stored);
    mockUpdateView.mockImplementation(async (_slug, _viewId, patch) => storePatch(patch));
  });

  afterEach(async () => {
    // A gesture may drop its draft when the write *settles*, and a test that
    // asserted on the request and returned leaves that write one microtask from
    // finishing — so the state update lands after the test body, outside `act`,
    // and React says so. Flushing here rather than making every drag test end
    // with a wait it does not otherwise need.
    await settle();
    rectSpy.mockRestore();
  });
}

// ---------------------------------------------------------------------------
// Reading what was written
// ---------------------------------------------------------------------------

/** The layout of the most recent PATCH. */
export function lastLayout(): Json {
  const calls = mockUpdateView.mock.calls;
  if (calls.length === 0) throw new Error("No layout was written");
  return (calls[calls.length - 1][2] as { layout: Json }).layout;
}

export function containersOf(layout: Json): Record<string, Json> {
  return layout.containers as Record<string, Json>;
}

// ---------------------------------------------------------------------------
// Driving a gesture
// ---------------------------------------------------------------------------

/** Returns false when the handler called `preventDefault`. */
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
export function drag(el: HTMLElement, from: [number, number], to: [number, number]) {
  pointerDown(el, from[0], from[1]);
  pointerMove(el, (from[0] + to[0]) / 2, (from[1] + to[1]) / 2);
  pointerMove(el, to[0], to[1]);
  pointerUp(el, to[0], to[1]);
}
