import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import "./TopbarDropdown.css";

const DropdownCloseContext = createContext<(() => void) | null>(null);

export function TopbarDropdown({
  label,
  children,
  align = "right",
}: {
  label: ReactNode;
  children: ReactNode;
  align?: "left" | "right";
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
    <div className="topbar-dropdown" ref={rootRef}>
      <button
        type="button"
        className="btn-secondary topbar-dropdown-trigger"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="topbar-dropdown-label">{label}</span>
        <span className="topbar-dropdown-chevron" aria-hidden="true">
          ▾
        </span>
      </button>
      {open ? (
        <DropdownCloseContext.Provider value={() => setOpen(false)}>
          <div className={`topbar-dropdown-menu topbar-dropdown-menu--${align}`} role="menu">
            {children}
          </div>
        </DropdownCloseContext.Provider>
      ) : null}
    </div>
  );
}

export function TopbarDropdownItem({
  active,
  children,
  onSelect,
}: {
  active?: boolean;
  children: ReactNode;
  onSelect: () => void;
}) {
  const close = useContext(DropdownCloseContext);
  return (
    <button
      type="button"
      role="menuitemradio"
      aria-checked={active}
      className={`topbar-dropdown-item ${active ? "active" : ""}`}
      onClick={() => {
        onSelect();
        close?.();
      }}
    >
      {children}
    </button>
  );
}

export function TopbarDropdownSection({ title }: { title: string }) {
  return <div className="topbar-dropdown-section-title">{title}</div>;
}

export function TopbarDropdownPaneRow({
  label,
  visible,
  disabled,
  onChange,
}: {
  label: string;
  visible: boolean;
  disabled?: boolean;
  onChange: (visible: boolean) => void;
}) {
  const switchDisabled = Boolean(disabled && visible);

  return (
    <label className={`topbar-dropdown-pane-row ${switchDisabled ? "disabled" : ""}`}>
      <span className="topbar-dropdown-pane-label">{label}</span>
      <span className="topbar-switch">
        <input
          type="checkbox"
          role="switch"
          className="topbar-switch-input"
          checked={visible}
          disabled={switchDisabled}
          aria-label={`${visible ? "Hide" : "Show"} ${label}`}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span className="topbar-switch-track" aria-hidden="true">
          <span className="topbar-switch-thumb" />
        </span>
      </span>
    </label>
  );
}
