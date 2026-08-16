/**
 * The canvas arrangement: containers placed freely on a pannable, zoomable
 * surface, dragged to move, resized from edges and corners, and stacked.
 *
 * The renderer owns six things, and each of them is a bug it exists to avoid:
 *
 *   - **Panning is the browser's own scrolling.** The viewport is `overflow:
 *     auto` and a plain wheel is left completely alone, so the innermost
 *     scrollable element under the pointer consumes it — which is AC3 exactly: a
 *     wheel over a terminal's scrollback scrolls the terminal, and only a wheel
 *     over the surface reaches the surface. Intercepting the wheel to pan by hand
 *     is how a canvas ends up fighting every scrollable thing inside it. Zoom is
 *     the one gesture that *is* intercepted, and it is `ctrl`/`⌘`+wheel, which is
 *     also what a trackpad pinch emits.
 *   - **At 100% there is no transform at all.** Zoom is a `scale()` on the
 *     surface, but the `undefined` at `zoom === 1` is load-bearing rather than an
 *     optimisation: AC5 wants every primitive pixel-accurate and correctly
 *     hit-tested at 100%, and the way to guarantee that for a live terminal and a
 *     cross-document iframe is to have no coordinate mapping and no rasterised
 *     layer between them and the user. The tradeoff away from 100% — blurred
 *     glyphs, and iframe hit-testing that goes through a transform some engines
 *     map imprecisely — is the price of not reflowing every embed per zoom step.
 *   - **A gesture holds a local draft.** There is no optimistic update in the
 *     write path, so an item that dropped its drag state on `pointerup` would
 *     snap back to where it started until the PATCH landed — and stay there if it
 *     failed. The draft outlives both.
 *   - **Clamping is in the arithmetic, not in CSS.** A CSS floor stops an item
 *     shrinking on screen while the stored width keeps falling, so the screen and
 *     the record quietly disagree until the next reload. `canvasLayout` clamps,
 *     and the draft is drawn through the same clamp the commit will store.
 *   - **A shield covers the surface while a gesture is open.** Pointer capture
 *     routes moves back to the handle, but an `<iframe>` is a separate document:
 *     without something over it, a drag that crosses a web embed can be swallowed
 *     by it. The shield, and `user-select: none`, are why this works over live
 *     content.
 *   - **Every item is keyed by its item id.** Restacking reorders the array, and
 *     under index keys React hands the terminal at index 2 the instance that was
 *     at index 1 — a new shell with no scrollback in it.
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useParams } from "react-router-dom";

import {
  useViewLayoutEdit,
  useViewLayoutWrite,
} from "../../hooks/useViewLayoutEdit";
import {
  DEFAULT_ITEM_HEIGHT,
  DEFAULT_ITEM_WIDTH,
  addItem,
  contentBounds,
  moveItem,
  readCanvasItems,
  removeItem,
  resizeItem,
  restackItem,
} from "../../lib/canvasLayout";
import {
  HOME_VIEWPORT,
  MAX_ZOOM,
  MIN_ZOOM,
  ZOOM_STEP,
  clampZoom,
  readViewport,
  writeViewport,
  type CanvasViewport,
} from "../../lib/canvasViewport";
import { asJson } from "../../lib/viewLayouts";
import type { ViewLayout } from "../../lib/viewsApi";
import { useSidebarWorkspaceSlug } from "../../state/SidebarWorkspaceContext";
import { CanvasItemView, type CanvasActions } from "./CanvasItemView";
import { ICON_BUTTON } from "./paneChrome";

/** How long the viewport sits still before it is written down. */
const VIEWPORT_SETTLE_MS = 250;

/** Room to place something new beyond the furthest item, in surface pixels. */
const WORKING_MARGIN_PX = 600;

/** The surface is never smaller than this, so an empty canvas is still a surface. */
const MIN_SURFACE_PX = 1200;

const TOOLBAR_BUTTON = "btn-secondary btn-compact";

/**
 * The canvas, already parsed into a layout object by the page.
 *
 * The layout arrives whole rather than as items and containers separately: every
 * edit this renderer makes is composed from the *cache's* newest layout inside
 * `useViewLayoutEdit`, and what is passed here is only what to draw.
 */
export function CanvasSurface({ layout }: { layout: ViewLayout }) {
  const slug = useSidebarWorkspaceSlug();
  // Outside the view route there is no id, and the write refuses to compose a
  // PATCH without one — a canvas can only reach the screen underneath it.
  const { viewId = "" } = useParams<{ viewId: string }>();
  const edit = useViewLayoutEdit(slug, viewId);

  const viewport = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(() => readViewport(slug, viewId).zoom);
  /**
   * The zoom a handler outside the render should read.
   *
   * Not an optimisation and not a duplicate source of truth: the scroll
   * adjustment a zoom makes is a *side effect*, and under `StrictMode` React
   * invokes a `setState` updater twice — so an adjustment computed inside one
   * would read a `scrollLeft` it had already moved and land somewhere else
   * entirely the second time. The ref is what lets `zoomAround` read the current
   * zoom without doing its work inside an updater.
   */
  const zoomRef = useRef(zoom);
  const [gesturing, setGesturing] = useState(false);

  const items = useMemo(() => readCanvasItems(layout), [layout]);
  const containers = useMemo(() => asJson(layout.containers) ?? {}, [layout]);

  /**
   * The surface's own size, in surface pixels.
   *
   * Content plus a working margin rather than the full reachable extent: an
   * element 100,000px wide gives a scrollbar thumb too small to grab and a
   * scroll position no one can aim. The margin is what makes room to drop
   * something new past the furthest item.
   */
  const surface = useMemo(() => {
    const bounds = contentBounds(items);
    const right = bounds === undefined ? 0 : bounds.x + bounds.width;
    const bottom = bounds === undefined ? 0 : bounds.y + bounds.height;
    return {
      width: Math.max(MIN_SURFACE_PX, right + WORKING_MARGIN_PX),
      height: Math.max(MIN_SURFACE_PX, bottom + WORKING_MARGIN_PX),
    };
  }, [items]);

  /** Remember where this canvas is being looked at, once it stops moving. */
  const settle = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const remember = useCallback(
    (next: CanvasViewport) => {
      if (settle.current !== undefined) clearTimeout(settle.current);
      settle.current = setTimeout(
        () => writeViewport(slug, viewId, next),
        VIEWPORT_SETTLE_MS,
      );
    },
    [slug, viewId],
  );

  const rememberNow = useCallback(() => {
    const element = viewport.current;
    if (element === null) return;
    remember({ panX: element.scrollLeft, panY: element.scrollTop, zoom });
  }, [remember, zoom]);

  // AC8: the stored viewport is applied once the surface exists to scroll. A
  // layout effect rather than an effect, so the restored position is in place
  // before the first paint and the canvas does not visibly jump from the origin.
  // Once per mount, and once per mount is once per view *because the page keys
  // this renderer by view id* (`ViewPage`'s `<ViewSurface key={loaded.id} …>`).
  // Without that key a second view would never restore, and `rememberNow` would
  // write the first view's scroll under the second view's storage key.
  const restored = useRef(false);
  useLayoutEffect(() => {
    if (restored.current) return;
    const element = viewport.current;
    if (element === null) return;
    restored.current = true;
    const stored = readViewport(slug, viewId);
    element.scrollLeft = stored.panX;
    element.scrollTop = stored.panY;
  }, [slug, viewId]);

  useEffect(() => {
    return () => {
      if (settle.current !== undefined) clearTimeout(settle.current);
    };
  }, []);

  /**
   * Zoom to `next`, keeping the surface point under `clientX`/`clientY` still.
   *
   * Without the anchor a zoom walks the content out from under the cursor, and
   * the user chases it with the scrollbars.
   */
  /**
   * A scroll position that must wait for the surface to be re-laid out.
   *
   * A browser clamps an assignment to `scrollLeft` against the element's
   * *current* `scrollWidth`. The sizer's width is `surface.width * zoom` and
   * `setZoom` is asynchronous, so scrolling in the same statement that changes
   * the zoom scrolls against the old, smaller extent — and every zoom that grows
   * the surface has its scroll silently truncated. The layout effect below
   * applies it after the new size is in the DOM and before the frame is painted.
   */
  const pendingScroll = useRef<{ x: number; y: number } | undefined>(undefined);

  useLayoutEffect(() => {
    const element = viewport.current;
    const target = pendingScroll.current;
    if (element === null || target === undefined) return;
    pendingScroll.current = undefined;
    element.scrollLeft = target.x;
    element.scrollTop = target.y;
    remember({ panX: element.scrollLeft, panY: element.scrollTop, zoom });
  }, [remember, zoom]);

  const zoomAround = useCallback(
    (
      next: number,
      clientX: number | undefined,
      clientY: number | undefined,
    ) => {
      const current = zoomRef.current;
      const target = clampZoom(next);
      if (current === target) return;

      const element = viewport.current;
      if (element !== null) {
        // Measured *before* the zoom changes: this is where the user is looking
        // now, and keeping that point still is the whole job.
        const rect = element.getBoundingClientRect();
        const anchorX =
          clientX === undefined ? rect.width / 2 : clientX - rect.left;
        const anchorY =
          clientY === undefined ? rect.height / 2 : clientY - rect.top;
        const surfaceX = (element.scrollLeft + anchorX) / current;
        const surfaceY = (element.scrollTop + anchorY) / current;
        pendingScroll.current = {
          x: Math.max(0, surfaceX * target - anchorX),
          y: Math.max(0, surfaceY * target - anchorY),
        };
      }
      zoomRef.current = target;
      setZoom(target);
    },
    [],
  );

  /**
   * `ctrl`/`⌘`+wheel zooms; every other wheel is left entirely alone.
   *
   * A native non-passive listener rather than React's `onWheel`, because React
   * registers `wheel` passively at the root — `preventDefault` from a synthetic
   * handler is ignored with a console warning, and the page zooms instead of the
   * canvas. Left alone means *no handler runs at all* for a plain wheel, which is
   * what lets the browser's own scroll chaining give the event to a terminal's
   * scrollback before the surface ever sees it.
   */
  useEffect(() => {
    const element = viewport.current;
    if (element === null) return undefined;
    function onWheel(event: WheelEvent) {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      // Read through the ref, so this listener is registered once rather than
      // torn down and rebuilt on every step of a continuous pinch.
      zoomAround(
        zoomRef.current * (event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP),
        event.clientX,
        event.clientY,
      );
    }
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [zoomAround]);

  /** Where the middle of the viewport is, in surface pixels. */
  const viewportCentre = useCallback((): { x: number; y: number } => {
    const element = viewport.current;
    if (element === null) return { x: 0, y: 0 };
    const rect = element.getBoundingClientRect();
    return {
      x: (element.scrollLeft + rect.width / 2) / zoom,
      y: (element.scrollTop + rect.height / 2) / zoom,
    };
  }, [zoom]);

  /** A client point as a surface point — where a double-click actually landed. */
  const surfacePointOf = useCallback(
    (clientX: number, clientY: number): { x: number; y: number } => {
      const element = viewport.current;
      if (element === null) return { x: 0, y: 0 };
      const rect = element.getBoundingClientRect();
      return {
        x: (element.scrollLeft + clientX - rect.left) / zoom,
        y: (element.scrollTop + clientY - rect.top) / zoom,
      };
    },
    [zoom],
  );

  // The header's picker sits outside `ContainerPane` — which obtains its own
  // write — so it reaches the shared pick rather than a second mutation, and the
  // canvas mints containers through the registry's factory alone. That is AC4:
  // no primitive *component* is imported here.
  const pickPrimitive = useViewLayoutWrite(slug, viewId);

  const actions = useMemo<CanvasActions>(
    () => ({
      // Each edit is composed at the front of the view's write queue, from the
      // newest layout this client holds — never from the items that were on
      // screen when the gesture started, which a still-open PATCH would revert.
      move: (itemId, x, y, onSettled) =>
        edit((current) => moveItem(current, itemId, x, y), onSettled),
      resize: (itemId, geometry, onSettled) =>
        edit((current) => resizeItem(current, itemId, geometry), onSettled),
      restack: (itemId, toFront) =>
        edit((current) => restackItem(current, itemId, toFront)),
      remove: (itemId) => edit((current) => removeItem(current, itemId)),
      pickPrimitive,
      setGesturing,
    }),
    [edit, pickPrimitive],
  );

  const place = useCallback(
    (x: number, y: number) => {
      edit((current) => addItem(current, x, y));
    },
    [edit],
  );

  /**
   * AC9's rescue: bring every item into view, whatever the pan and zoom were.
   *
   * On an empty canvas it is a way home rather than a no-op — a user who panned
   * a long way from an empty surface has nothing on screen to aim at.
   */
  const fitToContent = useCallback(() => {
    const element = viewport.current;
    if (element === null) return;
    const bounds = contentBounds(items);
    if (bounds === undefined) {
      pendingScroll.current = { x: 0, y: 0 };
      zoomRef.current = 1;
      setZoom(1);
      remember({ ...HOME_VIEWPORT });
      return;
    }
    const rect = element.getBoundingClientRect();
    // A zero-sized viewport (not yet laid out, or measured in an environment
    // with no layout engine) would divide into `Infinity` and store a zoom the
    // server has no opinion about but the surface cannot draw.
    const next =
      rect.width > 0 && rect.height > 0
        ? clampZoom(
            Math.min(rect.width / bounds.width, rect.height / bounds.height),
          )
        : 1;
    // The ref moves with the state: every handler outside the render reads the
    // zoom from it, and a `setZoom` that left it behind would have the next
    // pinch compute its anchor from the zoom before this one.
    zoomRef.current = next;
    // Queued, not assigned: at any zoom that grows the surface the sizer is still
    // the old size on this line, and the browser would clamp the scroll to it.
    pendingScroll.current = {
      x: Math.max(0, bounds.x * next),
      y: Math.max(0, bounds.y * next),
    };
    setZoom(next);
  }, [items, remember]);

  return (
    <div
      data-testid="view-canvas"
      style={{
        position: "relative",
        display: "flex",
        flexDirection: "column",
        flex: "1 1 0",
        minHeight: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "4px 8px",
          borderBottom: "1px solid var(--bd)",
        }}
      >
        <button
          type="button"
          className={TOOLBAR_BUTTON}
          data-canvas-action="add-container"
          onClick={() => {
            // Centred on the point, like the double-click below: `place` takes a
            // top-left corner, and an item hung from the centre point sits low
            // and to the right of where the user was looking.
            const centre = viewportCentre();
            place(
              centre.x - DEFAULT_ITEM_WIDTH / 2,
              centre.y - DEFAULT_ITEM_HEIGHT / 2,
            );
          }}
        >
          Add container
        </button>
        <button
          type="button"
          className={TOOLBAR_BUTTON}
          data-canvas-action="fit-to-content"
          onClick={fitToContent}
        >
          Fit to content
        </button>
        <span style={{ flex: "1 1 0" }} />
        <button
          type="button"
          className={ICON_BUTTON}
          data-canvas-action="zoom-out"
          aria-label="Zoom out"
          disabled={zoom <= MIN_ZOOM}
          onClick={() => zoomAround(zoom / ZOOM_STEP, undefined, undefined)}
        >
          <span aria-hidden="true">−</span>
        </button>
        <button
          type="button"
          className={TOOLBAR_BUTTON}
          data-canvas-action="zoom-reset"
          aria-label={`Zoom ${Math.round(zoom * 100)}%. Reset to 100%`}
          onClick={() => zoomAround(1, undefined, undefined)}
        >
          {`${Math.round(zoom * 100)}%`}
        </button>
        <button
          type="button"
          className={ICON_BUTTON}
          data-canvas-action="zoom-in"
          aria-label="Zoom in"
          disabled={zoom >= MAX_ZOOM}
          onClick={() => zoomAround(zoom * ZOOM_STEP, undefined, undefined)}
        >
          <span aria-hidden="true">+</span>
        </button>
      </div>

      {/* The viewport's own frame. The scroller is taken out of flow inside it
          so the empty state can be laid over the viewport rather than placed
          after a surface that is 1200 pixels tall — where it renders below the
          fold and the user is shown a blank scroll area instead of the message
          that tells a new canvas from a broken one. */}
      <div style={{ position: "relative", flex: "1 1 0", minHeight: 0 }}>
        <div
          ref={viewport}
          data-canvas-viewport={viewId}
          onScroll={rememberNow}
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            // Panning *is* this: the wheel, the trackpad and the scrollbars all
            // drive it, and a scrollable container inside gets the wheel first.
            overflow: "auto",
            // A drag that crosses a text run must not start selecting it.
            userSelect: gesturing ? "none" : undefined,
          }}
        >
          {/* The scrollable extent, which is the surface *after* zoom — a
            transformed child does not enlarge its parent's scroll area. */}
          <div
            data-canvas-sizer=""
            style={{
              position: "relative",
              width: surface.width * zoom,
              height: surface.height * zoom,
              // A stacking context, so every item's `z-index` competes only with
              // its siblings. Without it they compete with the shield below, whose
              // job is to be above all of them — and an item's z-index comes from
              // the stored layout, which the server bounds only to `int`.
              zIndex: 0,
            }}
          >
            <div
              data-canvas-surface=""
              data-zoom={zoom}
              onDoubleClick={(event) => {
                // Only the bare surface places something: a double-click inside a
                // container belongs to the container.
                if (event.target !== event.currentTarget) return;
                const point = surfacePointOf(event.clientX, event.clientY);
                place(
                  point.x - DEFAULT_ITEM_WIDTH / 2,
                  point.y - DEFAULT_ITEM_HEIGHT / 2,
                );
              }}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: surface.width,
                height: surface.height,
                transformOrigin: "0 0",
                // At 100% there is deliberately no transform at all — see the note
                // at the top of this file. This `undefined` is AC5.
                transform: zoom === 1 ? undefined : `scale(${zoom})`,
              }}
            >
              {items.map((item) => (
                <CanvasItemView
                  key={item.id}
                  item={item}
                  container={containers[item.container_id]}
                  actions={actions}
                  zoom={zoom}
                />
              ))}
            </div>
          </div>

          {/* An `<iframe>` is a separate document and will happily swallow a
            pointer move that crosses it, capture or no capture. The shield is
            what makes a drag over a web embed survive.

            Absolutely positioned over the scrollable extent, never fixed: a view
            is drawn inside a pane and owns no part of the screen outside it, and
            a fixed overlay would cover the app's own chrome for the duration of
            every drag. Sized to the sizer, so it covers every item rather than
            only the ones currently scrolled into sight. */}
          {gesturing ? (
            <div
              data-canvas-shield=""
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: surface.width * zoom,
                height: surface.height * zoom,
                zIndex: 1,
                cursor: "grabbing",
              }}
            />
          ) : null}
        </div>

        {/* Over the viewport, not after it, and transparent to the pointer so a
          double-click still places a container through it. */}
        {items.length === 0 ? (
          <div
            className="queue-page-empty"
            data-testid="view-canvas-empty"
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              pointerEvents: "none",
            }}
          >
            <p style={{ maxWidth: 520 }}>
              This canvas is empty. Add a container, or double-click anywhere on
              the surface to place one.
            </p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
