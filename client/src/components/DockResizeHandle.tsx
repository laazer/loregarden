import { useCallback, useRef, type KeyboardEvent, type PointerEvent } from "react";

import {
  DEFAULT_COPILOT_HEIGHT,
  DEFAULT_COPILOT_WIDTH,
  MAX_COPILOT_HEIGHT,
  MAX_COPILOT_HEIGHT_VIEWPORT_FRACTION,
  MAX_COPILOT_WIDTH,
  MIN_COPILOT_HEIGHT,
  MIN_COPILOT_WIDTH,
  useUiStore,
  type UtilityDockEdge,
} from "../state/uiStore";

/** One arrow-key press. Coarse enough to cross the range without holding it. */
const KEYBOARD_STEP = 24;

/**
 * The grip that resizes the utility dock.
 *
 * `copilotHeight` and `copilotWidth` have been in the store all along —
 * clamped, persisted, and documented as holding "a restored or dragged" size —
 * but nothing outside the store's own test ever called their setters. The dock
 * was pinned to its default, which is why a long answer had to be read through
 * a 340px slot.
 *
 * Both docks grow *towards* the page: a bottom dock's top edge and a right
 * dock's left edge, so in each case dragging away from the dock's own corner
 * adds size. That is the one sign convention here, and it is why the delta is
 * `origin - now` rather than the other way round.
 */
export function DockResizeHandle({ edge }: { edge: UtilityDockEdge }) {
  const height = useUiStore((s) => s.copilotHeight);
  const width = useUiStore((s) => s.copilotWidth);
  const setCopilotHeight = useUiStore((s) => s.setCopilotHeight);
  const setCopilotWidth = useUiStore((s) => s.setCopilotWidth);

  const vertical = edge === "bottom";
  const size = vertical ? height : width;
  const drag = useRef<{ origin: number; size: number } | null>(null);

  // The store clamps to what is a sane value to *store*; this clamps to what
  // the dock can actually show right now. Without it the top half of the drag
  // range renders identically on a short window while the handle keeps
  // reporting progress.
  const apply = useCallback(
    (next: number) => {
      if (!vertical) {
        setCopilotWidth(next);
        return;
      }
      const visibleMax = Math.min(
        MAX_COPILOT_HEIGHT,
        Math.round(window.innerHeight * MAX_COPILOT_HEIGHT_VIEWPORT_FRACTION),
      );
      setCopilotHeight(Math.min(next, visibleMax));
    },
    [setCopilotHeight, setCopilotWidth, vertical],
  );

  const onPointerDown = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      // Left button only: a right-click here belongs to the context menu.
      if (event.button !== 0) return;
      drag.current = { origin: vertical ? event.clientY : event.clientX, size };
      // Capture keeps the move events coming once the pointer leaves this 7px
      // strip, which is most of any real drag. Feature-checked rather than
      // assumed: jsdom has no Pointer Capture API, and an unguarded call there
      // throws before the drag has begun. Without it the drag still works, just
      // only while the pointer stays on the grip.
      if (typeof event.currentTarget.setPointerCapture === "function") {
        event.currentTarget.setPointerCapture(event.pointerId);
      }
      event.preventDefault();
    },
    [size, vertical],
  );

  const onPointerMove = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      const start = drag.current;
      if (!start) return;
      const now = vertical ? event.clientY : event.clientX;
      apply(start.size + (start.origin - now));
    },
    [apply, vertical],
  );

  const endDrag = useCallback((event: PointerEvent<HTMLDivElement>) => {
    drag.current = null;
    if (
      typeof event.currentTarget.hasPointerCapture === "function" &&
      event.currentTarget.hasPointerCapture(event.pointerId)
    ) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }, []);

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      const grow = vertical ? "ArrowUp" : "ArrowLeft";
      const shrink = vertical ? "ArrowDown" : "ArrowRight";
      if (event.key === grow) apply(size + KEYBOARD_STEP);
      else if (event.key === shrink) apply(size - KEYBOARD_STEP);
      else return;
      // Only once a key was ours: the page still owns every other arrow press.
      event.preventDefault();
    },
    [apply, size, vertical],
  );

  const reset = useCallback(
    () => apply(vertical ? DEFAULT_COPILOT_HEIGHT : DEFAULT_COPILOT_WIDTH),
    [apply, vertical],
  );

  return (
    <div
      className={`dock-resize-handle dock-resize-handle--${vertical ? "horizontal" : "vertical"}`}
      role="separator"
      tabIndex={0}
      aria-orientation={vertical ? "horizontal" : "vertical"}
      aria-label={vertical ? "Resize the panel height" : "Resize the panel width"}
      aria-valuenow={size}
      aria-valuemin={vertical ? MIN_COPILOT_HEIGHT : MIN_COPILOT_WIDTH}
      aria-valuemax={vertical ? MAX_COPILOT_HEIGHT : MAX_COPILOT_WIDTH}
      title="Drag to resize · arrow keys to nudge · double-click to reset"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onKeyDown={onKeyDown}
      onDoubleClick={reset}
    >
      <span className="dock-resize-handle-grip" aria-hidden />
    </div>
  );
}
