import { useState } from "react";

import { useActiveChatSession } from "../hooks/useActiveChatSession";
import { useApprovalResolution } from "../hooks/useApprovalResolution";
import { formatApprovalResolveError } from "../utils/approvalErrors";
import { PendingApprovalsSection } from "./PendingApprovalsSection";
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
/**
 * Openers for the questions this dock is usually opened to ask.
 *
 * Prompt shortcuts, not suggestions: nothing infers them from the ticket, and
 * clicking one only fills the composer. The operator still sends it.
 */
const COMPOSER_PLACEHOLDER: Record<string, string> = {
  "ticket-triage": "Message about this ticket…",
  "branch-triage": "Message about this branch…",
};

const TRY_ASKING: Record<string, string[]> = {
  "ticket-triage": [
    "What is blocking this ticket?",
    "Summarise what the last run changed",
    "Why did the last stage fail?",
  ],
  "branch-triage": [
    "What changed on this branch?",
    "Is this branch safe to delete?",
  ],
};

export function CopilotDock() {
  const open = useUiStore((s) => s.copilotOpen);
  const setOpen = useUiStore((s) => s.setCopilotOpen);
  const height = useUiStore((s) => s.copilotHeight);
  const { session, label, ticketId, pendingApprovals } = useActiveChatSession();
  const resolveApproval = useApprovalResolution(ticketId ?? undefined);

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
        {pendingApprovals.length > 0 && (
          <span className="copilot-dock-waiting">
            {pendingApprovals.length} waiting on you
          </span>
        )}
        {session.isBusy && pendingApprovals.length === 0 && (
          <span className="copilot-dock-busy">working…</span>
        )}
        {session.loadError && (
          <span className="copilot-dock-error">conversation unavailable</span>
        )}
      </div>

      {open && (
        <div className="copilot-dock-body">
          {/* Above the turns: an agent question arrives as an approval, not a
              message, so it would otherwise be invisible here. */}
          <PendingApprovalsSection
            approvals={pendingApprovals}
            submittingApprovalId={
              resolveApproval.isPending ? resolveApproval.variables?.id ?? null : null
            }
            submitError={
              resolveApproval.isError ? formatApprovalResolveError(resolveApproval.error) : null
            }
            onApprove={(approval, payload) =>
              resolveApproval.mutate({ id: approval.id, action: "approve", ...payload })
            }
            onReject={(approval, payload) =>
              resolveApproval.mutate({ id: approval.id, action: "reject", ...payload })
            }
          />
          <StudioChatMessages
            messages={session.messages}
            emptyMessage="No messages yet."
            isThinking={session.isBusy}
            assistantLabel="Baxter"
            className="copilot-dock-messages"
          />
          {session.messages.length === 0 && (TRY_ASKING[session.kind]?.length ?? 0) > 0 && (
            <div className="copilot-dock-chips">
              <span className="copilot-dock-chips-label">Try asking</span>
              {TRY_ASKING[session.kind].map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  className="copilot-dock-chip"
                  onClick={() => setDraft(prompt)}
                >
                  {prompt}
                </button>
              ))}
            </div>
          )}
          <StudioChatComposer
            value={draft}
            onChange={setDraft}
            onSubmit={send}
            placeholder={COMPOSER_PLACEHOLDER[session.kind] ?? "Message this conversation…"}
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
