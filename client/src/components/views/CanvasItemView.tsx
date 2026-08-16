/**
 * One placed container on the canvas: its chrome, and the gestures that move and
 * resize it.
 *
 * Split from `CanvasSurface` because the two share only the `CanvasActions`
 * interface: everything here is about one item's own draft and pointer capture,
 * and everything there is about the viewport the items sit in. The four
 * properties that are load-bearing here, each of them a bug first:
 *
 *   - **A gesture holds a local draft, until the write settles.** There is no
 *     optimistic update in the write path, so an item that dropped its drag state
 *     on `pointerup` would snap back to where it started until the PATCH landed.
 *     The draft covers that gap — and is dropped when the write *settles*, not
 *     when it succeeds, because a draft kept past a refused PATCH leaves the item
 *     drawn somewhere the record does not agree with, silently and forever.
 *   - **The gesture is per item and carries its pointer id.** A touchscreen can
 *     hold two, and pointer capture routes every later event to the element that
 *     took it — so a second pointer crossing that element delivers moves and ups
 *     against a drag it is no part of.
 *   - **Every pointer delta is divided by the zoom.** A pointer moves in screen
 *     pixels and the stored geometry is in surface pixels; without the division a
 *     drag at 50% moves the item twice as far as the cursor.
 *   - **Clamping is in the arithmetic, not in CSS.** The draft is drawn through
 *     the same clamp the commit will store, so an item never appears to follow the
 *     cursor past the edge and then jump back on release.
 */

import {
  useCallback,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";

import {
  MIN_ITEM_PX,
  clampGeometry,
  resizedGeometry,
  withGeometry,
  type CanvasItemModel,
  type ItemGeometry,
  type ResizeEdges,
} from "../../lib/canvasLayout";
import { ContainerPane } from "./ContainerPane";
import { ICON_BUTTON, paneTitle } from "./paneChrome";
import { PrimitivePicker } from "./PrimitivePicker";

/** How far one arrow-key press moves or resizes an item, in surface pixels. */
const KEY_STEP_PX = 8;

/** One handle: the edges it drags, plus how it is drawn and named. */
interface ResizeDirection extends ResizeEdges {
  readonly key: string;
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

export interface CanvasActions {
  /**
   * `onSettled` fires when the write finishes, however it finished. The item
   * hands the screen back to the record there — see `commit` below.
   */
  move: (itemId: string, x: number, y: number, onSettled: () => void) => void;
  resize: (itemId: string, geometry: ItemGeometry, onSettled: () => void) => void;
  restack: (itemId: string, toFront: boolean) => void;
  remove: (itemId: string) => void;
  pickPrimitive: (containerId: string, primitiveId: string) => void;
  /** Raised while a gesture is open, so the surface can shield its embeds. */
  setGesturing: (open: boolean) => void;
}

/**
 * An adjustment waiting to be written, and which write it needs.
 *
 * `resizing` travels with the geometry rather than being re-derived when the
 * write is sent: a resize committed through the move path stores x and y only,
 * silently dropping the size the user set.
 */
interface Adjustment {
  geometry: ItemGeometry;
  resizing: boolean;
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

export function CanvasItemView({
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
  const pending = useRef<Adjustment | undefined>(undefined);
  const [draft, setDraft] = useState<ItemGeometry | undefined>(undefined);
  const [picking, setPicking] = useState(false);

  // The draft is what the user is looking at while a gesture is open and while
  // the PATCH it produced is in flight; the record takes over the moment that
  // write settles, whichever way it settled.
  const drawn = draft ?? item;

  const apply = useCallback((adjustment: Adjustment) => {
    pending.current = adjustment;
    setDraft(adjustment.geometry);
  }, []);

  /**
   * Send the adjustment, and hand the screen back to the record when it settles.
   *
   * Dropping the draft on **settled** rather than on success is the whole point.
   * A refused PATCH — a 400 for an oversized layout, a 404 for a view deleted
   * underneath the gesture, a dropped connection — otherwise leaves the item
   * drawn where the user let go of it while the record still holds the old
   * geometry, forever and with no error on screen. The next edit then composes
   * from that record and quietly re-stores the position the user thought they had
   * changed. On success the draft and the record agree, so dropping it changes
   * nothing on screen.
   */
  function commit(adjustment: Adjustment) {
    pending.current = undefined;
    const settled = () => setDraft(undefined);
    if (adjustment.resizing) actions.resize(item.id, adjustment.geometry, settled);
    else actions.move(item.id, adjustment.geometry.x, adjustment.geometry.y, settled);
  }

  function gestureFor(event: ReactPointerEvent<HTMLElement>): Gesture | undefined {
    const open = gesture.current;
    if (open === undefined || open.pointerId !== event.pointerId) return undefined;
    return open;
  }

  function geometryAt(open: Gesture, event: ReactPointerEvent<HTMLElement>): ItemGeometry {
    const dx = (event.clientX - open.originX) / zoom;
    const dy = (event.clientY - open.originY) / zoom;
    if (open.direction !== undefined) return resizedGeometry(open.base, open.direction, dx, dy);
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
    if (open !== undefined) {
      apply({ geometry: geometryAt(open, event), resizing: open.direction !== undefined });
    }
  }

  function endGesture(event: ReactPointerEvent<HTMLElement>, adjustment: Adjustment | undefined) {
    gesture.current = undefined;
    actions.setGesturing(false);
    if (adjustment !== undefined) {
      apply(adjustment);
      commit(adjustment);
    }
    // Last, because releasing capture is cleanup and storing the gesture is the
    // point: a throw ahead of the commit drops the user's drag in silence. Guarded
    // rather than merely ordered, because `releasePointerCapture` raises
    // `NotFoundError` for a pointer the element no longer holds — and after a
    // `pointercancel` the pointer is gone by definition, so whether it is still
    // "captured" during the handler is up to the engine.
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function onPointerUp(event: ReactPointerEvent<HTMLElement>) {
    const open = gestureFor(event);
    if (open === undefined) return;
    endGesture(event, {
      geometry: geometryAt(open, event),
      resizing: open.direction !== undefined,
    });
  }

  function onPointerCancel(event: ReactPointerEvent<HTMLElement>) {
    const open = gestureFor(event);
    // The gesture was interrupted, but what is on screen is still where the user
    // dragged to; storing it is kinder than reverting it without a word.
    if (open === undefined) return;
    endGesture(event, pending.current);
  }

  /**
   * AC10's keyboard half: arrows move, `shift`+arrows resize from the bottom
   * right — the corner that changes the size without moving the item.
   */
  function onKeyDown(event: ReactKeyboardEvent<HTMLElement>) {
    // Only the item's own frame moves the item. This handler is on the wrapper
    // and keyboard events bubble, so without the guard every arrow key pressed
    // *inside* a container reaches it: a Left in a shell prompt would lose the
    // caret move to `preventDefault` and slide the terminal 8px sideways. The
    // drag path has the same rule, spelled as the `closest("button")` check.
    if (event.target !== event.currentTarget) return;
    const dx = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    const dy = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
    if (dx === 0 && dy === 0) return;
    event.preventDefault();
    // The arrow must not also scroll the surface underneath the item.
    event.stopPropagation();
    const base = { ...item, ...(pending.current?.geometry ?? drawn) };
    const step = KEY_STEP_PX;
    if (event.shiftKey) {
      apply({
        resizing: true,
        geometry: clampGeometry({
          ...base,
          width: Math.max(MIN_ITEM_PX, base.width + dx * step),
          height: Math.max(MIN_ITEM_PX, base.height + dy * step),
        }),
      });
      return;
    }
    apply({
      resizing: false,
      geometry: clampGeometry({ ...base, x: base.x + dx * step, y: base.y + dy * step }),
    });
  }

  /**
   * One adjustment, one PATCH. A held arrow key repeats its keydown and the draft
   * follows every repeat; the write waits for the key to come up, or for the item
   * to lose focus with an adjustment still unsaved.
   *
   * What kind of adjustment it is comes from the adjustment itself, never from
   * the modifier state at settle time. `keyup` on the *Shift key* reports
   * `shiftKey: false`, so releasing Shift before the arrow — an entirely ordinary
   * ordering — would have sent a resize through the move path, which writes x and
   * y only and reverts the size the user just set.
   */
  function onSettle() {
    const adjusted = pending.current;
    if (adjusted === undefined) return;
    commit(adjusted);
  }

  return (
    <div
      data-canvas-item={item.id}
      tabIndex={0}
      role="group"
      aria-label={`${paneTitle(container)} container`}
      // AC2's second half. `PointerDownCapture` rather than `onFocus` alone: a
      // click inside a terminal or an iframe never focuses this element, and the
      // container the user just clicked into is exactly the one to raise.
      onPointerDownCapture={() => actions.restack(item.id, true)}
      onFocus={() => actions.restack(item.id, true)}
      onKeyDown={onKeyDown}
      onKeyUp={onSettle}
      onBlur={onSettle}
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
          className={ICON_BUTTON}
          data-canvas-action="pick-primitive"
          aria-label="Change contents"
          aria-expanded={picking}
          onClick={() => setPicking((open) => !open)}
        >
          <span aria-hidden="true">⇄</span>
        </button>
        <button
          type="button"
          className={ICON_BUTTON}
          data-canvas-action="bring-to-front"
          aria-label="Bring to front"
          onClick={() => actions.restack(item.id, true)}
        >
          <span aria-hidden="true">▲</span>
        </button>
        <button
          type="button"
          className={ICON_BUTTON}
          data-canvas-action="send-to-back"
          aria-label="Send to back"
          onClick={() => actions.restack(item.id, false)}
        >
          <span aria-hidden="true">▼</span>
        </button>
        <button
          type="button"
          className={ICON_BUTTON}
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
