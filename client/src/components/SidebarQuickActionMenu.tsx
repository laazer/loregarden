/**
 * The menu a right-click on a sidebar page opens, at the pointer.
 *
 * It is portalled to `document.body` because the rail clips its own overflow
 * and a folded rail is 60px wide; a menu inside it would be cut off.
 *
 * Keyboard: the ContextMenu key and Shift+F10 fire the same `contextmenu`
 * event on the focused row, so the menu opens without a pointer. Focus moves to
 * the first item, Up/Down move between items, Escape or Tab closes it, and
 * focus goes back to the row it was opened from.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

import { useDismissOnOutside } from "../hooks/useDismissOnOutside";
import type { SidebarQuickAction } from "./appSidebarQuickActions";
import { Button } from "./ui/Button";
import "./OverflowMenu.css";

/** Kept clear of the viewport edge, so the panel's shadow is not cut off. */
const EDGE = 8;

export interface QuickActionMenuState {
  label: string;
  actions: readonly SidebarQuickAction[];
  x: number;
  y: number;
  /** The row the menu was opened from; focus returns to it on close. */
  origin: HTMLElement | null;
}

export function SidebarQuickActionMenu({
  menu,
  onSelect,
  onClose,
}: {
  menu: QuickActionMenuState;
  onSelect: (action: SidebarQuickAction) => void;
  onClose: () => void;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);

  const close = useCallback(() => {
    onClose();
    menu.origin?.focus();
  }, [onClose, menu.origin]);

  useDismissOnOutside(true, panelRef, close);

  // Measure before paint, then pull the panel back inside the viewport when the
  // pointer was near its right or bottom edge.
  useLayoutEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    const { width, height } = panel.getBoundingClientRect();
    setPosition({
      left: Math.max(EDGE, Math.min(menu.x, window.innerWidth - width - EDGE)),
      top: Math.max(EDGE, Math.min(menu.y, window.innerHeight - height - EDGE)),
    });
  }, [menu.x, menu.y]);

  // Not before it is positioned: until then the panel is `visibility: hidden`,
  // and the browser refuses focus to a hidden element.
  const placed = position !== null;
  useEffect(() => {
    if (placed) panelRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, [placed]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Tab") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = Array.from(
      panelRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [],
    );
    if (items.length === 0) return;
    const at = items.indexOf(document.activeElement as HTMLElement);
    const step = event.key === "ArrowDown" ? 1 : -1;
    items[(at + step + items.length) % items.length].focus();
  };

  return createPortal(
    <div
      ref={panelRef}
      className="overflow-menu-panel overflow-menu-panel--anchored"
      style={position ?? { left: menu.x, top: menu.y, visibility: "hidden" }}
      role="menu"
      aria-label={`${menu.label} quick actions`}
      onKeyDown={onKeyDown}
      onContextMenu={(event) => event.preventDefault()}
    >
      {menu.actions.map((action) => (
        <Button
          key={action.id}
          variant="plain"
          role="menuitem"
          className="overflow-menu-item"
          onClick={() => {
            onClose();
            onSelect(action);
          }}
        >
          {action.label}
        </Button>
      ))}
    </div>,
    document.body,
  );
}
