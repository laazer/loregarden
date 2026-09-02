import { useEffect } from "react";
import type { RefObject } from "react";

/**
 * Close an open panel when the pointer goes down outside it, or Escape is
 * pressed.
 *
 * Three components had grown byte-identical copies of this effect
 * (TicketPaneFilters, TopbarDropdown, OverflowMenu), which the organization
 * gate flagged as duplication. It is worth sharing beyond tidiness: the listener
 * pair has to be registered and removed symmetrically, and a copy that loses one
 * half leaks a document listener per mount while still looking correct.
 *
 * Listeners are attached only while `open`, so a closed panel costs nothing.
 */
export function useDismissOnOutside(
  open: boolean,
  rootRef: RefObject<HTMLElement | null>,
  onDismiss: () => void,
): void {
  useEffect(() => {
    if (!open) return;

    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        onDismiss();
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onDismiss();
      }
    };

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, rootRef, onDismiss]);
}
