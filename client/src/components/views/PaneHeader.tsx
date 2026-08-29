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
 *
 * ## The header's shape (556) — the contract 554 builds against
 *
 * The row is three ordered zones, left to right, and a control's zone is decided
 * by *what it acts on*, not by which ticket added it:
 *
 *   1. **the name** — `flex: 1 1 0`, truncating, and the only thing that grows.
 *   2. **contents controls** — what the pane *holds*. "Change contents" (⇄) is
 *      here, and **a pane's settings control belongs here too**, immediately
 *      before the picker, because a settings editor generated from the
 *      primitive's own fields edits the contents and nothing else.
 *   3. **arrangement controls** — where the pane *sits*: split, raise, lower,
 *      and close. Supplied by the arrangement through `buttons`, because only
 *      the arrangement knows them. Close is always last.
 *
 * A `.pane-header-zone-rule` hairline separates zone 2 from zone 3. So the
 * settings-editor ticket adds its control to *this* module between the title and
 * the picker button, not to `buttons` — a settings control passed through
 * `buttons` would land on the wrong side of the rule and be spelled twice, once
 * per arrangement, which is the duplication this component exists to end.
 *
 * ## Icons are never the only carrier of meaning
 *
 * Every control here is an accessible name (`aria-label`, real text in the
 * accessibility tree — 434's rule, and `title` is never the only name) *and* a
 * `title`, which is what gives a sighted pointer user the name too. The glyphs
 * are bare Unicode with no shared metrics and cannot carry a meaning on their
 * own, so a new control needs both attributes, not one.
 */

import { useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

import { EMPTY_PANE_TITLE, ICON_BUTTON, paneTitle, panePrimitive } from "./paneChrome";
import "./paneChrome.css";
import { PaneSettingsEditor } from "./PaneSettingsEditor";
import { PANE_SETTINGS_LABEL } from "./paneSettingsLabel";
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

/**
 * What a header row carries beyond its class: only the properties that turn it
 * into a drag handle. Everything else is `.pane-header` in `paneChrome.css`, so
 * the row is described in one place rather than as seven inline properties per
 * arrangement — the drift 556 was opened to end.
 *
 * That stylesheet lives beside this component rather than in the global
 * `index.css` for a reason that outlives taste: the suites that pin a pane's
 * sizing rules — no pixel heights, no viewport units, `min-height: 0` on every
 * flex column — walk `components/views/` and parse the CSS they find there.
 * Pane styling moved to `index.css` would still render and would silently stop
 * being checked.
 */
const DRAG_HANDLE_STYLE = {
  cursor: "move",
  userSelect: "none",
  touchAction: "none",
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
  const [editing, setEditing] = useState(false);
  const title = paneTitle(container);
  const primitive = panePrimitive(container);
  // No control where there is nothing to configure: a pane that has not picked
  // a primitive yet, one whose primitive this build no longer has, and one whose
  // schema declares no fields. Each would open a form with no inputs in it,
  // which is a control that says a pane is configurable when it is not — the
  // exact lie 554 exists to remove from the empty-pane copy.
  const configurable = primitive !== undefined && primitive.settingsFields.length > 0;

  // The two panels are one panel's worth of room, and both are opened from the
  // same two-button zone: leaving one open behind the other stacks two forms
  // under a header two buttons wide.
  function openPanel(next: "settings" | "picker" | "none") {
    setEditing(next === "settings");
    setPicking(next === "picker");
  }

  return (
    <>
      <div
        className="pane-header"
        {...(drag === undefined ? {} : { [drag.attribute]: drag.id })}
        onPointerDown={drag?.onPointerDown}
        onPointerMove={drag?.onPointerMove}
        onPointerUp={drag?.onPointerUp}
        onPointerCancel={drag?.onPointerCancel}
        // A handle is never text to select, and never a touch scroll.
        style={drag === undefined ? undefined : DRAG_HANDLE_STYLE}
      >
        <span className={`pane-header-title${title === EMPTY_PANE_TITLE ? " is-empty" : ""}`}>
          {title}
        </span>
        {/* Zone 2 — contents: settings, then the picker. Both edit what the pane
            holds; neither is passed in by an arrangement. See the docstring. */}
        {configurable ? (
          <button
            type="button"
            className={ICON_BUTTON}
            {...{ [actionAttribute]: "pane-settings" }}
            aria-label={PANE_SETTINGS_LABEL}
            title={PANE_SETTINGS_LABEL}
            aria-expanded={editing}
            onClick={() => openPanel(editing ? "none" : "settings")}
          >
            <span aria-hidden="true">⚙</span>
          </button>
        ) : null}
        <button
          type="button"
          className={ICON_BUTTON}
          {...{ [actionAttribute]: "pick-primitive" }}
          aria-label="Change contents"
          title="Change contents"
          aria-expanded={picking}
          onClick={() => openPanel(picking ? "none" : "picker")}
        >
          <span aria-hidden="true">⇄</span>
        </button>
        <span className="pane-header-zone-rule" aria-hidden="true" />
        {/* Zone 3 — arrangement. The arrangement's own controls. */}
        {buttons}
      </div>
      {editing && primitive !== undefined ? (
        <div className="pane-settings-panel">
          {/* Keyed by the primitive: a pick made while the editor was open is a
              different schema, and a form that kept its draft across that would
              be holding one primitive's values against another's fields. */}
          <PaneSettingsEditor
            key={primitive.id}
            containerId={containerId}
            container={container}
            primitive={primitive}
            onDone={() => setEditing(false)}
          />
        </div>
      ) : null}
      {picking ? (
        <div className="pane-picker-panel">
          <PrimitivePicker
            legend="Change contents to"
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
