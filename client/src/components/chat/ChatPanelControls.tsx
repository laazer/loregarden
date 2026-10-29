import { Button } from "../ui/Button";

/**
 * History, New chat and the workbench toggle, kept together in one corner.
 *
 * Drawn in the workbench's head while it is open and over the thread's top
 * right while it is closed, so the same button that hides the workbench is the
 * one that brings it back, and History and New chat never go with it.
 */
export function ChatPanelControls({
  workbenchOpen,
  historyOpen,
  onToggleWorkbench,
  onToggleHistory,
  onNewChat,
}: {
  workbenchOpen: boolean;
  historyOpen: boolean;
  onToggleWorkbench: () => void;
  onToggleHistory: () => void;
  onNewChat: () => void;
}) {
  const workbenchLabel = workbenchOpen ? "Hide the workbench" : "Show the workbench";
  return (
    <div className="chat-panel-controls" role="group" aria-label="Chat panels">
      <Button
        variant="plain"
        className="chat-side-card-icon-btn"
        aria-label="History"
        title="History"
        aria-pressed={historyOpen}
        onClick={onToggleHistory}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" aria-hidden>
          <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
          <path d="M3 3v5h5M12 7v5l3 2" />
        </svg>
      </Button>
      <Button
        variant="plain"
        className="chat-side-card-icon-btn"
        aria-label="New chat"
        title="New chat"
        onClick={onNewChat}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="M12 5v14M5 12h14" />
        </svg>
      </Button>
      <Button
        variant="plain"
        className="chat-side-card-icon-btn"
        aria-label={workbenchLabel}
        title={workbenchLabel}
        aria-expanded={workbenchOpen}
        onClick={onToggleWorkbench}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d={workbenchOpen ? "m9 18 6-6-6-6" : "m15 18-6-6 6-6"} />
        </svg>
      </Button>
    </div>
  );
}
