import { useAgentPresence } from "../../state/QueueStatusContext";
import { Button } from "../ui/Button";
import type { UtilityDockEdge } from "../../state/uiStore";

/**
 * The half of the bar that belongs to the screen rather than to a conversation:
 * the shell switch and where the utility dock sits.
 *
 * Kept apart because it is the only half Home and the chat page draw — those
 * screens compose for their own thread, and the bar must not offer a second
 * composer for it.
 */
export function ActionBarScreenControls({
  terminalSlug,
  terminalOpen,
  setTerminalOpen,
  utilityDockEdge,
  setUtilityDockEdge,
}: {
  terminalSlug: string;
  terminalOpen: boolean;
  setTerminalOpen: (open: boolean) => void;
  utilityDockEdge: UtilityDockEdge;
  setUtilityDockEdge: (edge: UtilityDockEdge) => void;
}) {
  const nextEdge: UtilityDockEdge = utilityDockEdge === "bottom" ? "right" : "bottom";
  const presence = useAgentPresence();

  return (
    <>
      <span className="app-action-bar-divider" aria-hidden />

      <span
        className="app-action-bar-live"
        role="img"
        aria-label={presence.label}
        title={presence.label}
        data-presence={presence.state}
      >
        <span
          className={`app-action-bar-live-dot is-${presence.state}`}
          aria-hidden
        />
      </span>

      <Button
        variant="plain"
        className={`app-action-bar-terminal${terminalOpen ? " is-on" : ""}`}
        aria-pressed={terminalOpen}
        disabled={!terminalSlug}
        title={terminalSlug ? `Shell in ${terminalSlug}` : "Pick a workspace to open a shell in"}
        onClick={() => setTerminalOpen(!terminalOpen)}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="m4 17 6-6-6-6M12 19h8" />
        </svg>
        Terminal
        <svg
          className={`app-action-bar-chevron${terminalOpen ? " is-open" : ""}`}
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          aria-hidden
        >
          <path d="m18 15-6-6-6 6" />
        </svg>
      </Button>

      <Button
        variant="plain"
        className="app-action-bar-dock-edge"
        aria-label={
          utilityDockEdge === "bottom"
            ? "Dock utility panel to the right"
            : "Dock utility panel to the bottom"
        }
        title={utilityDockEdge === "bottom" ? "Dock right" : "Dock bottom"}
        onClick={() => setUtilityDockEdge(nextEdge)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
          <rect x="3" y="3" width="18" height="18" rx="2" />
          {utilityDockEdge === "bottom" ? <path d="M3 15h18" /> : <path d="M15 3v18" />}
        </svg>
      </Button>
    </>
  );
}

