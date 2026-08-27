/**
 * The header a pane gets, whichever arrangement is drawing it: the row, the
 * truncating title, the picker button, and the panel it opens.
 *
 * A grid leaf (440) and a canvas item (442) drew this twice — the same span with
 * the same seven style properties, the same `picking` state, the same button, the
 * same panel — and only two things ever differed between the copies. Both are
 * parameters here: the attribute each suite selects its controls by, and which
 * buttons follow the picker one. Duplicated, this is where the two arrangements
 * drift apart: a title that ellipsises in one and clips in the other, or a picker
 * that closes on pick here and stays open there.
 *
 * The pieces it is assembled from — `ICON_BUTTON`, `paneTitle` — are in
 * `paneChrome`, which the canvas toolbar also draws on.
 */

import { useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

import { ICON_BUTTON, paneTitle } from "./paneChrome";
import { PrimitivePicker } from "./PrimitivePicker";

type PaneRowPointerHandler = (event: ReactPointerEvent<HTMLDivElement>) => void;

/**
 * What turns the header row into a move handle.
 *
 * Optional on the header because only one arrangement has anywhere to move a pane
 * *to*: a canvas item is dragged by its header, while a grid leaf is placed by the
 * split that holds it and has no position of its own. `attribute` is a parameter
 * for the same reason the action attribute is — the handle is a seam a suite
 * selects by name, and this module is not the one that names it.
 */
export interface PaneDragHandle {
  attribute: string;
  id: string;
  onPointerDown: PaneRowPointerHandler;
  onPointerMove: PaneRowPointerHandler;
  onPointerUp: PaneRowPointerHandler;
  onPointerCancel: PaneRowPointerHandler;
}

const HEADER_ROW = {
  display: "flex",
  alignItems: "center",
  gap: 4,
  padding: "4px 6px",
  borderBottom: "1px solid var(--bd)",
  minWidth: 0,
} as const;

/**
 * The title: one line, ellipsised. `flex: 1 1 0` with `minWidth: 0` is what makes
 * it give way to the buttons — a flex child defaults to `min-width: auto` and
 * refuses to shrink below its text, which pushes the buttons out of a narrow pane
 * instead of truncating the name.
 */
const HEADER_TITLE = {
  flex: "1 1 0",
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  color: "var(--txl)",
  fontSize: 11.5,
} as const;

/**
 * A fragment rather than a wrapper, because the picker panel is a sibling of the
 * row and not a child of it: on a canvas the row is the drag handle, and a picker
 * nested inside it would start a move gesture on every press that missed one of
 * its buttons.
 */
export function PaneHeader({
  container,
  actionAttribute,
  containerId,
  onPickPrimitive,
  buttons,
  drag,
}: {
  container: unknown;
  /** `data-grid-action` or `data-canvas-action` — see the harness DOM contracts. */
  actionAttribute: string;
  /** Which container the picker rewrites; not the node or item drawing it. */
  containerId: string;
  onPickPrimitive: (containerId: string, primitiveId: string) => void;
  /** The arrangement's own controls, rendered after the picker button. */
  buttons: ReactNode;
  drag?: PaneDragHandle;
}) {
  const [picking, setPicking] = useState(false);

  return (
    <>
      <div
        {...(drag === undefined ? {} : { [drag.attribute]: drag.id })}
        onPointerDown={drag?.onPointerDown}
        onPointerMove={drag?.onPointerMove}
        onPointerUp={drag?.onPointerUp}
        onPointerCancel={drag?.onPointerCancel}
        style={
          drag === undefined
            ? HEADER_ROW
            : // A handle is never text to select, and never a touch scroll.
              { ...HEADER_ROW, cursor: "move", userSelect: "none", touchAction: "none" }
        }
      >
        <span style={HEADER_TITLE}>{paneTitle(container)}</span>
        <button
          type="button"
          className={ICON_BUTTON}
          {...{ [actionAttribute]: "pick-primitive" }}
          aria-label="Change contents"
          aria-expanded={picking}
          onClick={() => setPicking((open) => !open)}
        >
          <span aria-hidden="true">⇄</span>
        </button>
        {buttons}
      </div>
      {picking ? (
        <div style={{ padding: "6px 8px", borderBottom: "1px solid var(--bd)" }}>
          <PrimitivePicker
            onPick={(primitiveId) => {
              setPicking(false);
              onPickPrimitive(containerId, primitiveId);
            }}
          />
        </div>
      ) : null}
    </>
  );
}
