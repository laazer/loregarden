import { useCallback, createContext, useContext, useRef, useState, type ReactNode } from "react";
import { useDismissOnOutside } from "../hooks/useDismissOnOutside";
import { useAnchoredPanelPosition } from "../hooks/useAnchoredPanelPosition";
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
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelStyle = useAnchoredPanelPosition(open, triggerRef, panelRef, { align });

  const closePanel = useCallback(() => setOpen(false), []);
  useDismissOnOutside(open, rootRef, closePanel);

  return (
    <div className="topbar-dropdown" ref={rootRef}>
      <button
        ref={triggerRef}
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
          <div
            ref={panelRef}
            className={`topbar-dropdown-menu topbar-dropdown-menu--${align} topbar-dropdown-menu--anchored`}
            style={panelStyle ?? { position: "fixed", visibility: "hidden" }}
            role="menu"
          >
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
