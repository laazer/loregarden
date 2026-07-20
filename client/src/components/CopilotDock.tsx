import { useState } from "react";

import { useActiveChatSession } from "../hooks/useActiveChatSession";
import { useUiStore } from "../state/uiStore";
import { StudioChatComposer, StudioChatMessages } from "./studio/StudioChat";
import "./CopilotDock.css";

/**
 * A persistent way into whichever chat the current screen is showing.
 *
 * Not a new assistant and not an autonomous actor: it binds to the session the
 * screen already owns, so a message sent here is the same turn the panel would
 * have sent, and appears in both.
 *
 * Collapsed it is a single bar; expanded it shows the turns above the composer.
 * It mounts above the routes as the only persistent chrome besides the icon
 * rail, which is why the binding is resolved from the route rather than handed
 * down by a page.
 */
export function CopilotDock() {
  const open = useUiStore((s) => s.copilotOpen);
  const setOpen = useUiStore((s) => s.setCopilotOpen);
  const height = useUiStore((s) => s.copilotHeight);
  const { session, label } = useActiveChatSession();

  const [draft, setDraft] = useState("");
  const [autoApprove, setAutoApprove] = useState(false);

  const send = () => {
    const content = draft.trim();
    if (!content || !session) return;
    // Clear optimistically so the composer is ready for the next message; a
    // failed send surfaces through session.error rather than by restoring text.
    setDraft("");
    void session.send(content, { autoApprove }).catch(() => {});
  };

  if (!session) {
    return (
      <div className="copilot-dock copilot-dock--empty">
        <span className="copilot-dock-hint">
          Open a ticket or a branch to chat about it.
        </span>
      </div>
    );
  }

  return (
    <div
      className={`copilot-dock${open ? " copilot-dock--open" : ""}`}
      style={open ? { height } : undefined}
    >
      <div className="copilot-dock-bar">
        <button
          type="button"
          className="copilot-dock-toggle"
          aria-expanded={open}
          aria-label={open ? "Collapse copilot" : "Expand copilot"}
          onClick={() => setOpen(!open)}
        >
          <span className="copilot-dock-chevron" aria-hidden>
            {open ? "▾" : "▴"}
          </span>
          <span className="copilot-dock-label">{label}</span>
        </button>
        {session.isBusy && <span className="copilot-dock-busy">working…</span>}
        {session.loadError && (
          <span className="copilot-dock-error">conversation unavailable</span>
        )}
      </div>

      {open && (
        <div className="copilot-dock-body">
          <StudioChatMessages
            messages={session.messages}
            emptyMessage="No messages yet."
            isThinking={session.isBusy}
            assistantLabel="Baxter"
            className="copilot-dock-messages"
          />
          <StudioChatComposer
            value={draft}
            onChange={setDraft}
            onSubmit={send}
            placeholder="Message about this ticket…"
            isSending={session.isBusy}
            disabled={session.loadError}
            error={session.error}
            optionsRow={
              <label className="copilot-dock-option">
                <input
                  type="checkbox"
                  checked={autoApprove}
                  onChange={(e) => setAutoApprove(e.target.checked)}
                />
                Auto-approve
              </label>
            }
          />
        </div>
      )}
    </div>
  );
}
