import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

import { useAppPage } from "../lib/useAppNavigation";
import { BrandMark } from "./BrandMark";

type AppIconRailProps = {
  onOpenSettings: () => void;
};

function NavButton({
  active,
  title,
  to,
  children,
}: {
  active: boolean;
  title: string;
  to: string;
  children: ReactNode;
}) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      className={`icon-rail-btn${active ? " icon-rail-btn--active" : ""}`}
      title={title}
      aria-current={active ? "page" : undefined}
    >
      {active ? <span className="icon-rail-btn-bar" aria-hidden /> : null}
      {children}
    </NavLink>
  );
}

export function AppIconRail({ onOpenSettings }: AppIconRailProps) {
  const appPage = useAppPage();

  return (
    <nav className="icon-rail" aria-label="Main navigation">
      <div className="icon-rail-logo">
        <BrandMark />
      </div>

      <NavButton
        active={appPage === "dashboard" || appPage === "editor"}
        title="Console"
        to="/"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <path d="M3 9h18M7 13l2 2-2 2M12 17h4" />
        </svg>
      </NavButton>

      <NavButton active={appPage === "studio"} title="Studios" to="/studio/agents">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
          <path d="M12 3 3 8v8l9 5 9-5V8z" />
          <path d="M3 8l9 5 9-5M12 13v8" />
        </svg>
      </NavButton>

      <NavButton active={appPage === "queue"} title="Parallel Execution" to="/queue">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
          <rect x="3" y="4" width="5" height="16" rx="1.5" />
          <rect x="10" y="4" width="5" height="16" rx="1.5" />
          <rect x="17" y="4" width="4" height="16" rx="1.5" />
        </svg>
      </NavButton>

      <NavButton active={appPage === "mcp"} title="MCP Gateway" to="/mcp">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
          <circle cx="12" cy="12" r="2.5" />
          <path d="M12 3v6.5M12 14.5V21M3 12h6.5M14.5 12H21" />
          <circle cx="5" cy="5" r="1.6" />
          <circle cx="19" cy="19" r="1.6" />
        </svg>
      </NavButton>

      <NavButton active={appPage === "branch-triage"} title="Branch Triage" to="/branch-triage">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
          <circle cx="6" cy="6" r="3" />
          <circle cx="6" cy="18" r="3" />
          <path d="M6 9v6" />
          <circle cx="18" cy="6" r="3" />
          <path d="M18 9a9 9 0 0 1-9 9" />
        </svg>
      </NavButton>

      <div className="icon-rail-spacer" />

      <button type="button" className="icon-rail-settings" title="Settings" onClick={onOpenSettings}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      </button>

      <div className="icon-rail-avatar" aria-hidden>
        LG
      </div>
    </nav>
  );
}
