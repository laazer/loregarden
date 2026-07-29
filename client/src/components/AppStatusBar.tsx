import { useQuery } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";

import { api } from "../api/client";
import { ticketIdFromPath } from "../lib/appNavigation";
import { useTerminalTarget } from "../hooks/useTerminalTarget";
import { useUiStore, type UtilityDockEdge } from "../state/uiStore";

export function AppStatusBar() {
  const { pathname } = useLocation();
  const terminal = useTerminalTarget();
  const terminalOpen = useUiStore((s) => s.terminalOpen);
  const setTerminalOpen = useUiStore((s) => s.setTerminalOpen);
  const utilityDockEdge = useUiStore((s) => s.utilityDockEdge);
  const setUtilityDockEdge = useUiStore((s) => s.setUtilityDockEdge);
  const branchTriageBranch = useUiStore((s) => s.branchTriageBranch);

  const onBranchTriage = pathname.startsWith("/branch-triage");
  const ticketId = onBranchTriage ? null : ticketIdFromPath(pathname);

  const { data: ticket } = useQuery({
    queryKey: ["ticket", ticketId],
    queryFn: () => api.ticket(ticketId as string),
    enabled: Boolean(ticketId),
  });

  const branch = onBranchTriage ? branchTriageBranch || null : ticket?.branch || null;
  const runCode = ticket?.run_code || null;

  const nextEdge: UtilityDockEdge = utilityDockEdge === "bottom" ? "right" : "bottom";

  return (
    <footer className={`status-bar status-bar--edge-${utilityDockEdge}`}>
      {branch ? (
        <span className="status-bar-branch">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--txl)" strokeWidth="2" aria-hidden>
            <circle cx="6" cy="6" r="3" />
            <circle cx="6" cy="18" r="3" />
            <path d="M6 9v6" />
            <circle cx="18" cy="6" r="3" />
            <path d="M18 9a9 9 0 0 1-9 9" />
          </svg>
          {branch}
        </span>
      ) : null}
      {runCode ? <span className="status-bar-run">{runCode}</span> : null}
      <span className="status-bar-live">
        <span className="status-bar-live-dot" />
        agents online
      </span>
      <div className="status-bar-spacer" />
      <button
        type="button"
        className="status-bar-dock-edge"
        aria-label={utilityDockEdge === "bottom" ? "Dock utility panel to the right" : "Dock utility panel to the bottom"}
        title={utilityDockEdge === "bottom" ? "Dock right" : "Dock bottom"}
        onClick={() => setUtilityDockEdge(nextEdge)}
      >
        {utilityDockEdge === "bottom" ? "Dock right" : "Dock bottom"}
      </button>
      <button
        type="button"
        className={`status-bar-terminal${terminalOpen ? " is-on" : ""}`}
        aria-pressed={terminalOpen}
        disabled={!terminal.workspaceSlug}
        title={
          terminal.workspaceSlug
            ? `Shell in ${terminal.workspaceSlug}`
            : "Pick a workspace to open a shell in"
        }
        onClick={() => setTerminalOpen(!terminalOpen)}
      >
        Terminal
      </button>
    </footer>
  );
}
