/**
 * The modal an operator picks a pane's contents from (557).
 *
 * ## Why it stopped being an inline panel
 *
 * The picker was a list dropped into the pane it was picking for. Three options
 * fit there. Sixteen do not, and 554's review had already measured the failure:
 * a 167px form inside a 149px pane, its Save button below the fold with no
 * scrollbar to say so. The same arithmetic applies to a list four times longer
 * than the panel it was drawn in — so the picker leaves the pane entirely.
 *
 * ## Why it is a portal, and why it does not live under `components/views/`
 *
 * `.modal-panel` is `position: fixed`, which is positioned against the viewport
 * *until* an ancestor has a transform — and 442's canvas applies
 * `transform: scale()` at every zoom that is not 100%. A modal rendered in the
 * React tree beneath a zoomed canvas would be laid out inside that scaled
 * surface: offset, and drawn at the canvas's scale. `createPortal` to
 * `document.body` puts it outside any transformed ancestor, so the pane it was
 * opened from cannot move it. `AgentPreviewModal` is the precedent.
 *
 * It sits here rather than beside the pane chrome because the suites under
 * `components/views/` parse every stylesheet and source they find there and
 * reject `position: fixed` and viewport units — correctly, for something drawn
 * *inside* a pane. This is drawn over the whole app, is styled from the app's
 * own `.modal-*` and `.input` classes, and is not pane chrome.
 *
 * ## Adding a primitive changes nothing here
 *
 * The groups are the registry's own `category` values in registration order,
 * and search reads `displayName`, `category` and `id`. This module names no
 * primitive, and neither does the grid or the canvas.
 */

import { useEffect, useId, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import { IconCloseButton } from "./IconCloseButton";
import { PrimitivePicker } from "./views/PrimitivePicker";
import { CONTAINER_PRIMITIVES } from "./views/primitives/registry";
import type { RegisteredPrimitive } from "./views/primitives/types";

/** Whether `entry` answers `query`, matched against everything the row shows. */
function matches(entry: RegisteredPrimitive, query: string): boolean {
  if (query === "") return true;
  const haystack = `${entry.displayName} ${entry.category} ${entry.id}`.toLowerCase();
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter((term) => term !== "")
    // Every term, not any: typing two words narrows rather than widens, which
    // is what a search box with sixteen options in it is for.
    .every((term) => haystack.includes(term));
}

/**
 * The categories, in the order the registry first mentions each.
 *
 * Registration order rather than alphabetical: the registry is already grouped
 * by hand, and sorting it would scatter the three 436 shipped with among the
 * thirteen this ticket added.
 */
export function groupByCategory(
  entries: RegisteredPrimitive[],
): { category: string; entries: RegisteredPrimitive[] }[] {
  const groups = new Map<string, RegisteredPrimitive[]>();
  for (const entry of entries) {
    const bucket = groups.get(entry.category);
    if (bucket === undefined) groups.set(entry.category, [entry]);
    else bucket.push(entry);
  }
  return [...groups].map(([category, grouped]) => ({ category, entries: grouped }));
}

export interface PrimitivePickerModalProps {
  /** What this dialog is offering — its title, and the search field's context. */
  legend: string;
  entries?: RegisteredPrimitive[];
  onPick: (primitiveId: string) => void;
  onClose: () => void;
}

export function PrimitivePickerModal({
  legend,
  entries = CONTAINER_PRIMITIVES,
  onPick,
  onClose,
}: PrimitivePickerModalProps) {
  const [query, setQuery] = useState("");
  // Two pickers can be open in one document — a grid of panes each has a
  // header — so the label/input pairing is per instance rather than a constant.
  const searchId = useId();
  const titleId = useId();

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const groups = useMemo(
    () =>
      groupByCategory(entries.filter((entry) => matches(entry, query.trim()))).filter(
        (group) => group.entries.length > 0,
      ),
    [entries, query],
  );

  return createPortal(
    <>
      <div className="modal-overlay" onClick={onClose} role="presentation" />
      <div className="modal-panel" role="dialog" aria-labelledby={titleId} aria-modal="true">
        <div className="modal-header">
          <div>
            <div className="state-label">Pane</div>
            <h2 id={titleId} className="modal-title">
              {legend}
            </h2>
            <p className="modal-subtitle">
              Every primitive this build can put in a pane. You can change it later.
            </p>
          </div>
          <IconCloseButton onClick={onClose} />
        </div>

        <div className="modal-body">
          <div className="modal-field">
            <label className="field-label" htmlFor={searchId}>
              Search
            </label>
            <input
              id={searchId}
              className="input"
              type="search"
              value={query}
              autoFocus
              placeholder="Ticket, branch, terminal…"
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>

          {groups.length === 0 ? (
            <p className="modal-hint">Nothing matches that.</p>
          ) : (
            groups.map((group) => (
              // Wrapped, and the wrapper is load-bearing rather than tidy:
              // `.primitive-picker` declares `min-height: 0`, which is right in
              // a pane — a flex child that cannot shrink is what overflows one —
              // and wrong as a child of `.modal-body`, which is itself a flex
              // column. Left to shrink there, every list collapsed to nothing
              // and its rows drew on top of the next group's heading. Seen in a
              // browser; jsdom reports every element at 0px and cannot show it.
              //
              // The category is the group's heading *and* the list's accessible
              // name, which is what `PrimitivePicker`'s legend already does.
              // Reused rather than reimplemented so the option row is drawn in
              // one place, and so this module still names no primitive.
              <div className="primitive-picker-group" key={group.category}>
                <PrimitivePicker
                  legend={group.category}
                  entries={group.entries}
                  onPick={onPick}
                />
              </div>
            ))
          )}
        </div>
      </div>
    </>,
    document.body,
  );
}
