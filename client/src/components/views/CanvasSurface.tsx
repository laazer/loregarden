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
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { useParams } from "react-router-dom";

import { useViewLayoutEdit, useViewLayoutWrite } from "../../hooks/useViewLayoutEdit";
import {
  DEFAULT_ITEM_HEIGHT,
  DEFAULT_ITEM_WIDTH,
  MIN_ITEM_PX,
  addItem,
  clampGeometry,
  contentBounds,
  moveItem,
  readCanvasItems,
  removeItem,
  resizeItem,
  restackItem,
  withGeometry,
  type CanvasItemModel,
  type ItemGeometry,
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
import { ContainerPane } from "./ContainerPane";
import { HEADER_BUTTON, paneTitle } from "./paneChrome";
import { PrimitivePicker } from "./PrimitivePicker";

/** How far one arrow-key press moves or resizes an item, in surface pixels. */
const KEY_STEP_PX = 8;

/** How long the viewport sits still before it is written down. */
const VIEWPORT_SETTLE_MS = 250;

/** Room to place something new beyond the furthest item, in surface pixels. */
const WORKING_MARGIN_PX = 600;

/** The surface is never smaller than this, so an empty canvas is still a surface. */
const MIN_SURFACE_PX = 1200;

/** Which edges a resize handle drags. */
interface ResizeDirection {
  readonly key: string;
  readonly north: boolean;
  readonly south: boolean;
  readonly west: boolean;
  readonly east: boolean;
  readonly cursor: string;
  readonly label: string;
}

/**
 * The eight handles, edges and corners alike — AC1 names both.
 *
 * One table rather than eight elements written out: the geometry each produces is
 * the same arithmetic with different flags, and a hand-written south-east handle
 * is where a sign error hides.
 */
const RESIZE_DIRECTIONS: ResizeDirection[] = [
  { key: "n", north: true, south: false, west: false, east: false, cursor: "ns-resize", label: "Resize from the top edge" },
  { key: "s", north: false, south: true, west: false, east: false, cursor: "ns-resize", label: "Resize from the bottom edge" },
  { key: "w", north: false, south: false, west: true, east: false, cursor: "ew-resize", label: "Resize from the left edge" },
  { key: "e", north: false, south: false, west: false, east: true, cursor: "ew-resize", label: "Resize from the right edge" },
  { key: "nw", north: true, south: false, west: true, east: false, cursor: "nwse-resize", label: "Resize from the top-left corner" },
  { key: "ne", north: true, south: false, west: false, east: true, cursor: "nesw-resize", label: "Resize from the top-right corner" },
  { key: "sw", north: false, south: true, west: true, east: false, cursor: "nesw-resize", label: "Resize from the bottom-left corner" },
  { key: "se", north: false, south: true, west: false, east: true, cursor: "nwse-resize", label: "Resize from the bottom-right corner" },
];

/** The thickness of a handle's hit area, in screen pixels. */
const HANDLE_PX = 8;

function handleStyle(direction: ResizeDirection): CSSProperties {
  const style: CSSProperties = {
    position: "absolute",
    cursor: direction.cursor,
    // The handle is never text to select, and never a touch scroll.
    userSelect: "none",
    touchAction: "none",
    zIndex: 1,
  };
  const inset = -HANDLE_PX / 2;
  if (direction.north) style.top = inset;
  if (direction.south) style.bottom = inset;
  if (direction.west) style.left = inset;
  if (direction.east) style.right = inset;
  if (!direction.north && !direction.south) {
    style.top = HANDLE_PX / 2;
    style.bottom = HANDLE_PX / 2;
    style.width = HANDLE_PX;
  } else {
    style.height = HANDLE_PX;
  }
  if (!direction.west && !direction.east) {
    style.left = HANDLE_PX / 2;
    style.right = HANDLE_PX / 2;
  } else if (direction.north || direction.south) {
    style.width = HANDLE_PX;
  }
  return style;
}

/**
 * `item`'s geometry after dragging `direction` by `dx`, `dy` surface pixels.
 *
 * The minimum is applied to the *size* and the moving edge follows it, so a
 * north-west drag past the floor stops the top-left corner rather than letting it
 * carry on while the box inverts. `clampGeometry` then applies the surface bounds
 * — the two floors are not the same one, and applying only the second is how an
 * item becomes a sliver that cannot be grabbed again.
 */
function draggedGeometry(
  item: CanvasItemModel,
  direction: ResizeDirection,
  dx: number,
  dy: number,
): ItemGeometry {
  let { x, y, width, height } = item;
  if (direction.east) width = Math.max(MIN_ITEM_PX, item.width + dx);
  if (direction.west) {
    width = Math.max(MIN_ITEM_PX, item.width - dx);
    x = item.x + (item.width - width);
  }
  if (direction.south) height = Math.max(MIN_ITEM_PX, item.height + dy);
  if (direction.north) {
    height = Math.max(MIN_ITEM_PX, item.height - dy);
    y = item.y + (item.height - height);
  }
  return clampGeometry({ x, y, width, height });
}

interface CanvasActions {
  move: (itemId: string, x: number, y: number) => void;
  resize: (itemId: string, geometry: ItemGeometry) => void;
  restack: (itemId: string, toFront: boolean) => void;
  remove: (itemId: string) => void;
  pickPrimitive: (containerId: string, primitiveId: string) => void;
  /** Raised while a gesture is open, so the surface can shield its embeds. */
  setGesturing: (open: boolean) => void;
}

/**
 * One item's open gesture: which pointer owns it, where it started, and what it
 * started from.
 *
 * Stored per item rather than per surface for the same reason the grid stores one
 * per gap: a touchscreen can hold two of them, and one shared slot lets the
 * second press overwrite the first's origin so the first finger's moves then move
 * the *other* item from the wrong starting point.
 *
 * `pointerId` is stored for the same gesture: capture routes every later event to
 * the element that took it, so a second pointer crossing that element delivers
 * moves and ups against a drag it is no part of.
 */
interface Gesture {
  pointerId: number;
  originX: number;
  originY: number;
  /** `undefined` for a move; the edges being dragged for a resize. */
  direction: ResizeDirection | undefined;
  base: CanvasItemModel;
}

function CanvasItemView({
  item,
  container,
  actions,
  zoom,
}: {
  item: CanvasItemModel;
  container: unknown;
  actions: CanvasActions;
  /**
   * Screen pixels per surface pixel. A pointer moves in screen pixels and the
   * stored geometry is in surface pixels, so every delta is divided by it —
   * without which a drag at 50% zoom moves an item twice as far as the cursor.
   */
  zoom: number;
}) {
  const gesture = useRef<Gesture | undefined>(undefined);
  /** Adjusted but not yet committed — for keyup, blur and cancel. */
  const pending = useRef<ItemGeometry | undefined>(undefined);
  const [draft, setDraft] = useState<{ id: string; geometry: ItemGeometry } | undefined>(undefined);
  const [picking, setPicking] = useState(false);

  // The draft survives both the in-flight PATCH and a rejected one, and is
  // dropped only when it belongs to an item this element is no longer drawing.
  const drawn = draft !== undefined && draft.id === item.id ? draft.geometry : item;

  const apply = useCallback(
    (geometry: ItemGeometry) => {
      pending.current = geometry;
      setDraft({ id: item.id, geometry });
    },
    [item.id],
  );

  function commit(geometry: ItemGeometry, direction: ResizeDirection | undefined) {
    pending.current = undefined;
    if (direction === undefined) actions.move(item.id, geometry.x, geometry.y);
    else actions.resize(item.id, geometry);
  }

  function gestureFor(event: ReactPointerEvent<HTMLElement>): Gesture | undefined {
    const open = gesture.current;
    if (open === undefined || open.pointerId !== event.pointerId) return undefined;
    return open;
  }

  function geometryAt(open: Gesture, event: ReactPointerEvent<HTMLElement>): ItemGeometry {
    const dx = (event.clientX - open.originX) / zoom;
    const dy = (event.clientY - open.originY) / zoom;
    if (open.direction !== undefined) return draggedGeometry(open.base, open.direction, dx, dy);
    return withGeometry(open.base, { ...open.base, x: open.base.x + dx, y: open.base.y + dy });
  }

  function beginGesture(
    event: ReactPointerEvent<HTMLElement>,
    direction: ResizeDirection | undefined,
  ) {
    // Without this the browser starts a native text selection (or an image drag)
    // that runs for the whole gesture.
    event.preventDefault();
    // A second pointer landing on an element already being dragged is not a new
    // gesture: the first still owns the capture, and adopting the second would
    // move the origin out from under the moves still arriving for it.
    if (gesture.current !== undefined) return;
    // Capture is what keeps a move over a terminal from reaching the terminal.
    event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = {
      pointerId: event.pointerId,
      originX: event.clientX,
      originY: event.clientY,
      direction,
      // The item as drawn, so a second drag started before the first's PATCH
      // landed continues from where the user sees the item.
      base: { ...item, ...drawn },
    };
    actions.setGesturing(true);
  }

  function onPointerMove(event: ReactPointerEvent<HTMLElement>) {
    const open = gestureFor(event);
    // Drawn, not written: persistence is on gesture end, never per pointer move.
    if (open !== undefined) apply(geometryAt(open, event));
  }

  function endGesture(
    event: ReactPointerEvent<HTMLElement>,
    geometry: ItemGeometry | undefined,
    direction: ResizeDirection | undefined,
  ) {
    gesture.current = undefined;
    actions.setGesturing(false);
    if (geometry !== undefined) {
      apply(geometry);
      commit(geometry, direction);
    }
    // Last, because releasing capture is cleanup and storing the gesture is the
    // point: `releasePointerCapture` throws on a pointer the element no longer
    // holds, and a throw ahead of the commit drops the user's drag in silence.
    event.currentTarget.releasePointerCapture(event.pointerId);
  }

  function onPointerUp(event: ReactPointerEvent<HTMLElement>) {
    const open = gestureFor(event);
    if (open === undefined) return;
    endGesture(event, geometryAt(open, event), open.direction);
  }

  function onPointerCancel(event: ReactPointerEvent<HTMLElement>) {
    const open = gestureFor(event);
    // The gesture was interrupted, but what is on screen is still where the user
    // dragged to; storing it is kinder than reverting it without a word.
    if (open === undefined) return;
    endGesture(event, pending.current, open.direction);
  }

  /**
   * AC10's keyboard half: arrows move, `shift`+arrows resize from the bottom
   * right — the corner that changes the size without moving the item.
   */
  function onKeyDown(event: ReactKeyboardEvent<HTMLElement>) {
    const dx = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    const dy = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
    if (dx === 0 && dy === 0) return;
    event.preventDefault();
    // The arrow must not also scroll the surface underneath the item.
    event.stopPropagation();
    const base = { ...item, ...(pending.current ?? drawn) };
    const step = KEY_STEP_PX;
    if (event.shiftKey) {
      apply(
        clampGeometry({
          ...base,
          width: Math.max(MIN_ITEM_PX, base.width + dx * step),
          height: Math.max(MIN_ITEM_PX, base.height + dy * step),
        }),
      );
      return;
    }
    apply(clampGeometry({ ...base, x: base.x + dx * step, y: base.y + dy * step }));
  }

  /**
   * One adjustment, one PATCH. A held arrow key repeats its keydown and the draft
   * follows every repeat; the write waits for the key to come up, or for the item
   * to lose focus with an adjustment still unsaved.
   */
  function onSettle(resizing: boolean) {
    const adjusted = pending.current;
    if (adjusted === undefined) return;
    pending.current = undefined;
    if (resizing) actions.resize(item.id, adjusted);
    else actions.move(item.id, adjusted.x, adjusted.y);
  }

  return (
    <div
      data-canvas-item={item.id}
      data-z-index={item.z_index}
      tabIndex={0}
      role="group"
      aria-label={`${paneTitle(container)} container`}
      // AC2's second half. `PointerDownCapture` rather than `onFocus` alone: a
      // click inside a terminal or an iframe never focuses this element, and the
      // container the user just clicked into is exactly the one to raise.
      onPointerDownCapture={() => actions.restack(item.id, true)}
      onFocus={() => actions.restack(item.id, true)}
      onKeyDown={onKeyDown}
      onKeyUp={(event) => onSettle(event.shiftKey)}
      onBlur={() => onSettle(false)}
      style={{
        position: "absolute",
        left: drawn.x,
        top: drawn.y,
        width: drawn.width,
        height: drawn.height,
        zIndex: item.z_index,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        background: "var(--bg)",
        border: "1px solid var(--bd)",
        borderRadius: 6,
        // Overlap is the point of a canvas, so an item must occlude what is under
        // it rather than letting it show through.
        boxShadow: "0 2px 10px rgba(0,0,0,0.25)",
        minWidth: 0,
        minHeight: 0,
      }}
    >
      <div
        data-canvas-drag={item.id}
        onPointerDown={(event) => {
          // The header's own buttons are not a drag handle; a press that starts
          // on one must still be able to become a click.
          if ((event.target as HTMLElement).closest("button") !== null) return;
          beginGesture(event, undefined);
        }}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          padding: "4px 6px",
          borderBottom: "1px solid var(--bd)",
          cursor: "move",
          userSelect: "none",
          touchAction: "none",
          minWidth: 0,
        }}
      >
        <span
          style={{
            flex: "1 1 0",
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            color: "var(--txl)",
            fontSize: 11.5,
          }}
        >
          {paneTitle(container)}
        </span>
        <button
          type="button"
          className={HEADER_BUTTON}
          data-canvas-action="pick-primitive"
          aria-label="Change contents"
          aria-expanded={picking}
          onClick={() => setPicking((open) => !open)}
        >
          <span aria-hidden="true">⇄</span>
        </button>
        <button
          type="button"
          className={HEADER_BUTTON}
          data-canvas-action="bring-to-front"
          aria-label="Bring to front"
          onClick={() => actions.restack(item.id, true)}
        >
          <span aria-hidden="true">▲</span>
        </button>
        <button
          type="button"
          className={HEADER_BUTTON}
          data-canvas-action="send-to-back"
          aria-label="Send to back"
          onClick={() => actions.restack(item.id, false)}
        >
          <span aria-hidden="true">▼</span>
        </button>
        <button
          type="button"
          className={HEADER_BUTTON}
          data-canvas-action="close"
          aria-label="Remove this container"
          onClick={() => actions.remove(item.id)}
        >
          <span aria-hidden="true">✕</span>
        </button>
      </div>
      {picking ? (
        <div style={{ padding: "6px 8px", borderBottom: "1px solid var(--bd)" }}>
          <PrimitivePicker
            onPick={(primitiveId) => {
              setPicking(false);
              actions.pickPrimitive(item.container_id, primitiveId);
            }}
          />
        </div>
      ) : null}
      <div style={{ display: "flex", flex: "1 1 0", minHeight: 0, minWidth: 0 }}>
        <ContainerPane containerId={item.container_id} container={container} />
      </div>
      {RESIZE_DIRECTIONS.map((direction) => (
        <div
          key={direction.key}
          data-canvas-resize={`${item.id}:${direction.key}`}
          role="separator"
          aria-label={direction.label}
          onPointerDown={(event) => beginGesture(event, direction)}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerCancel}
          style={handleStyle(direction)}
        />
      ))}
    </div>
  );
}

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
      settle.current = setTimeout(() => writeViewport(slug, viewId, next), VIEWPORT_SETTLE_MS);
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
  const zoomAround = useCallback(
    (next: number, clientX: number | undefined, clientY: number | undefined) => {
      const element = viewport.current;
      const target = clampZoom(next);
      setZoom((current) => {
        if (element === null || current === target) return target;
        const rect = element.getBoundingClientRect();
        const anchorX = clientX === undefined ? rect.width / 2 : clientX - rect.left;
        const anchorY = clientY === undefined ? rect.height / 2 : clientY - rect.top;
        const surfaceX = (element.scrollLeft + anchorX) / current;
        const surfaceY = (element.scrollTop + anchorY) / current;
        element.scrollLeft = Math.max(0, surfaceX * target - anchorX);
        element.scrollTop = Math.max(0, surfaceY * target - anchorY);
        remember({ panX: element.scrollLeft, panY: element.scrollTop, zoom: target });
        return target;
      });
    },
    [remember],
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
      zoomAround(
        zoom * (event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP),
        event.clientX,
        event.clientY,
      );
    }
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [zoom, zoomAround]);

  /** Where the middle of the viewport is, in surface pixels. */
  const viewportCentre = useCallback((): { x: number; y: number } => {
    const element = viewport.current;
    if (element === null) return { x: 0, y: 0 };
    const rect = element.getBoundingClientRect();
    return {
      x: (element.scrollLeft + rect.width / 2) / zoom - DEFAULT_ITEM_WIDTH / 2,
      y: (element.scrollTop + rect.height / 2) / zoom - DEFAULT_ITEM_HEIGHT / 2,
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
      move: (itemId, x, y) => edit((current) => moveItem(current, itemId, x, y)),
      resize: (itemId, geometry) => edit((current) => resizeItem(current, itemId, geometry)),
      restack: (itemId, toFront) => edit((current) => restackItem(current, itemId, toFront)),
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
      element.scrollLeft = 0;
      element.scrollTop = 0;
      zoomAround(1, undefined, undefined);
      remember({ ...HOME_VIEWPORT });
      return;
    }
    const rect = element.getBoundingClientRect();
    // A zero-sized viewport (not yet laid out, or measured in an environment
    // with no layout engine) would divide into `Infinity` and store a zoom the
    // server has no opinion about but the surface cannot draw.
    const next =
      rect.width > 0 && rect.height > 0
        ? clampZoom(Math.min(rect.width / bounds.width, rect.height / bounds.height))
        : 1;
    setZoom(next);
    element.scrollLeft = Math.max(0, bounds.x * next);
    element.scrollTop = Math.max(0, bounds.y * next);
    remember({ panX: element.scrollLeft, panY: element.scrollTop, zoom: next });
  }, [items, remember, zoomAround]);

  return (
    <div
      data-testid="view-canvas"
      style={{ position: "relative", display: "flex", flexDirection: "column", flex: "1 1 0", minHeight: 0 }}
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
            const centre = viewportCentre();
            place(centre.x, centre.y);
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
          className={HEADER_BUTTON}
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
          aria-label="Reset zoom to 100%"
          onClick={() => zoomAround(1, undefined, undefined)}
        >
          {`${Math.round(zoom * 100)}%`}
        </button>
        <button
          type="button"
          className={HEADER_BUTTON}
          data-canvas-action="zoom-in"
          aria-label="Zoom in"
          disabled={zoom >= MAX_ZOOM}
          onClick={() => zoomAround(zoom * ZOOM_STEP, undefined, undefined)}
        >
          <span aria-hidden="true">+</span>
        </button>
      </div>

      <div
        ref={viewport}
        data-canvas-viewport={viewId}
        onScroll={rememberNow}
        style={{
          position: "relative",
          flex: "1 1 0",
          minHeight: 0,
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
              place(point.x - DEFAULT_ITEM_WIDTH / 2, point.y - DEFAULT_ITEM_HEIGHT / 2);
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

        {items.length === 0 ? (
          <div className="queue-page-empty" data-testid="view-canvas-empty">
            <p style={{ maxWidth: 520 }}>
              This canvas is empty. Add a container, or double-click anywhere on the surface to
              place one.
            </p>
          </div>
        ) : null}

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
    </div>
  );
}
