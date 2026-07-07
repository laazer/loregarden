import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
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

  useEffect(() => {
    if (!open) return;

    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className="overflow-menu" ref={rootRef}>
      <button
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
          <div className={`overflow-menu-panel overflow-menu-panel--${align}`} role="menu" aria-label={label}>
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
