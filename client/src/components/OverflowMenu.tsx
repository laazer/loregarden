import { useCallback, createContext, useContext, useRef, useState, type ReactNode } from "react";
import { useDismissOnOutside } from "../hooks/useDismissOnOutside";
import { useAnchoredPanelPosition } from "../hooks/useAnchoredPanelPosition";
import "./OverflowMenu.css";

const MenuCloseContext = createContext<(() => void) | null>(null);

export function OverflowMenu({
  label,
  align = "right",
  disabled = false,
  children,
}: {
  label: string;
  align?: "left" | "right";
  disabled?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelStyle = useAnchoredPanelPosition(open, triggerRef, panelRef, { align });

  const closePanel = useCallback(() => setOpen(false), []);
  useDismissOnOutside(open, rootRef, closePanel);

  return (
    <div className="overflow-menu" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="btn-secondary btn-compact overflow-menu-trigger"
        aria-label={label}
        aria-expanded={open}
        aria-haspopup="menu"
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
      >
        ⋯
      </button>
      {open ? (
        <MenuCloseContext.Provider value={() => setOpen(false)}>
          <div
            ref={panelRef}
            className={`overflow-menu-panel overflow-menu-panel--${align} overflow-menu-panel--anchored`}
            style={panelStyle ?? { position: "fixed", visibility: "hidden" }}
            role="menu"
            aria-label={label}
          >
            {children}
          </div>
        </MenuCloseContext.Provider>
      ) : null}
    </div>
  );
}

export function OverflowMenuSection({ title }: { title: string }) {
  return <div className="overflow-menu-section">{title}</div>;
}

export function OverflowMenuItem({
  children,
  onSelect,
  disabled = false,
  title,
  danger = false,
}: {
  children: ReactNode;
  onSelect: () => void;
  disabled?: boolean;
  title?: string;
  danger?: boolean;
}) {
  const close = useContext(MenuCloseContext);
  return (
    <button
      type="button"
      role="menuitem"
      className={`overflow-menu-item${danger ? " overflow-menu-item--danger" : ""}`}
      disabled={disabled}
      title={title}
      onClick={() => {
        if (disabled) return;
        onSelect();
        close?.();
      }}
    >
      {children}
    </button>
  );
}
