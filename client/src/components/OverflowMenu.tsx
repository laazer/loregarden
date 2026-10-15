import {
  useCallback,
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
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

  // Return focus to the trigger whenever the panel closes — selecting an item,
  // Escape, or a click outside. The menu's slow item hands the stage to a
  // terminal, and without this a keyboard operator is dropped on `<body>` for
  // the seconds that takes, with no way back to the control they just used.
  // Gated on having been open, so this never pulls focus as a ticket's twelve
  // stage rows mount.
  const hasOpened = useRef(false);
  useEffect(() => {
    if (open) {
      hasOpened.current = true;
    } else if (hasOpened.current) {
      triggerRef.current?.focus();
    }
  }, [open]);

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
        <MenuCloseContext.Provider value={closePanel}>
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
