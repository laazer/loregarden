import { useEffect, useState } from "react";

import { useActiveChatSession } from "../hooks/useActiveChatSession";
import { useApprovalResolution } from "../hooks/useApprovalResolution";
import { formatApprovalResolveError } from "../utils/approvalErrors";
import { PendingApprovalsSection } from "./PendingApprovalsSection";
import { ChatHistoryRail } from "./chat/ChatHistoryRail";
import { quickPrompts as promptsFor } from "../lib/dockChatPrompts";
import { useTerminalTarget } from "../hooks/useTerminalTarget";
import { useUiStore } from "../state/uiStore";
import { StudioChatMessages } from "./studio/StudioChat";
import { TerminalWorkspace } from "./TerminalWorkspace";
import "./CopilotDock.css";

/**
 * The panels the global action bar opens: the turns of whichever chat the screen
 * owns, and a shell in the workspace it names.
 *
 * It binds to the session the screen already owns, so a turn shown here is the
 * same turn the on-screen panel shows. Composing happens in `AppActionBar`;
 * this is the thread, not the input.
 */
export function CopilotDock() {
  const open = useUiStore((s) => s.copilotOpen);
  const height = useUiStore((s) => s.copilotHeight);
  const edge = useUiStore((s) => s.utilityDockEdge);
  const { session, ticketId, pendingApprovals, branch, archive } = useActiveChatSession();
  const historyOpen = useUiStore((s) => s.copilotHistoryOpen);
  const setHistoryOpen = useUiStore((s) => s.setCopilotHistoryOpen);
  const resolveApproval = useApprovalResolution(ticketId ?? undefined);
  const terminalOpen = useUiStore((s) => s.terminalOpen);
  const terminal = useTerminalTarget();
  // Chat and terminal open independently. A shell is not an accessory to a
  // conversation: wanting one says nothing about wanting the other, and gating
  // the terminal on the chat's expanded state meant you could never have just a
  // terminal. Mounting the panel spawns a real shell, so it still takes an
  // explicit ask and a workspace to run in.
  //
  // Closing the panel must not reap the shell. Toggling the omnibar is hide /
  // show, not kill / spawn — otherwise cwd, jobs, and scrollback vanish every
  // time the dock collapses. Keep the panel mounted under the last workspace it
  // was opened for until the screen names a different one (or never opened).
  const [keptSlug, setKeptSlug] = useState<string | null>(null);
  useEffect(() => {
    if (terminalOpen && terminal.workspaceSlug) {
      setKeptSlug(terminal.workspaceSlug);
      return;
    }
    if (
      !terminalOpen &&
      keptSlug !== null &&
      terminal.workspaceSlug !== "" &&
      terminal.workspaceSlug !== keptSlug
    ) {
      setKeptSlug(null);
    }
  }, [terminalOpen, terminal.workspaceSlug, keptSlug]);

  const showTerminal = terminalOpen && Boolean(keptSlug);
  const mountTerminal = Boolean(keptSlug);
  const showChat = open && Boolean(session);
  const panelsVisible = showChat || showTerminal;
  const edgeClass = edge === "right" ? " copilot-dock--edge-right" : " copilot-dock--edge-bottom";
  const keptOnlyClass = !panelsVisible && mountTerminal ? " copilot-dock--kept-only" : "";

  // Collapsed is the action bar on its own; there is no second bar to draw.
  // A kept-but-hidden shell still mounts so the process survives the toggle.
  if (!panelsVisible && !mountTerminal) return null;

  // The rail holds one thing at a time, and the archive is the thing that was
  // asked for: openers are always a click away again once a thread is picked.
  const showHistory = Boolean(archive) && historyOpen;
  const openers = session ? promptsFor(session.kind, branch) : [];
  const showOpeners = !showHistory && session?.messages.length === 0 && openers.length > 0;

  const sendQuick = (content: string) => {
    if (!session || session.isBusy || session.loadError) return;
    void session.send(content, { autoApprove: false }).catch(() => {});
  };

  return (
    <div
      className={`copilot-dock${edgeClass}${keptOnlyClass}`}
      style={edge === "bottom" && panelsVisible ? { height } : undefined}
      aria-hidden={!panelsVisible ? true : undefined}
    >
      <div className="copilot-dock-body">
        {showChat && session && (
          <div className="copilot-dock-chat">
            <div className="copilot-dock-chat-main">
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
                activeTurnId={session.activeTurnId}
                assistantLabel="Baxter"
                className="copilot-dock-messages"
                onPrimitiveSubmit={(content) => sendQuick(content)}
              />
            </div>
            {(showHistory || showOpeners) && (
              // Beside the turns rather than beneath them: stacked, the openers
              // sat between the thread and the composer and pushed the turns up
              // every time the thread was empty.
              <aside className="copilot-dock-rail">
                {showHistory && archive ? (
                  <ChatHistoryRail
                    workspaceSlug={archive.workspaceSlug}
                    activeSessionId={archive.sessionId}
                    onSelectSession={(id) => {
                      archive.openSession(id);
                      // The rail was a detour: showing the picked conversation
                      // matters more than keeping the list up.
                      setHistoryOpen(false);
                    }}
                  />
                ) : (
                  <>
                    <span className="copilot-dock-rail-label">Try asking</span>
                    {openers.map((prompt) => (
                      <button
                        key={prompt}
                        type="button"
                        className="copilot-dock-rail-btn"
                        disabled={session.isBusy || session.loadError}
                        onClick={() => sendQuick(prompt)}
                      >
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="var(--ac2)" aria-hidden>
                          <ellipse cx="7" cy="8" rx="1.9" ry="2.5" />
                          <ellipse cx="12" cy="6" rx="1.9" ry="2.7" />
                          <ellipse cx="17" cy="8" rx="1.9" ry="2.5" />
                          <path d="M12 11c3 0 5 2.1 5 4.3 0 1.9-1.7 2.5-3 2.5-.9 0-1.3-.4-2-.4s-1.1.4-2 .4c-1.3 0-3-.6-3-2.5C7 13.1 9 11 12 11z" />
                        </svg>
                        <span>{prompt}</span>
                      </button>
                    ))}
                  </>
                )}
              </aside>
            )}
          </div>
        )}

        {mountTerminal && keptSlug && (
          <div
            className={`copilot-dock-terminal${showTerminal ? "" : " is-kept"}`}
            // Remount on a workspace change rather than letting the panel
            // swap repos under a live shell: the old shell is reaped and a
            // new one starts in the right place.
            key={keptSlug}
          >
            <TerminalWorkspace
              workspaceSlug={keptSlug}
              visible={showTerminal}
              onEmpty={() => useUiStore.getState().setTerminalOpen(false)}
            />
          </div>
        )}
      </div>
    </div>
  );
}
