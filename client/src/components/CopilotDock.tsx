import { useActiveChatSession } from "../hooks/useActiveChatSession";
import { useApprovalResolution } from "../hooks/useApprovalResolution";
import { formatApprovalResolveError } from "../utils/approvalErrors";
import { PendingApprovalsSection } from "./PendingApprovalsSection";
import { TRY_ASKING } from "../lib/dockChatPrompts";
import { useTerminalTarget } from "../hooks/useTerminalTarget";
import { useUiStore } from "../state/uiStore";
import { StudioChatMessages } from "./studio/StudioChat";
import { TerminalPanel } from "./TerminalPanel";
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
  const { session, ticketId, pendingApprovals } = useActiveChatSession();
  const resolveApproval = useApprovalResolution(ticketId ?? undefined);
  const terminalOpen = useUiStore((s) => s.terminalOpen);
  const terminal = useTerminalTarget();
  // Chat and terminal open independently. A shell is not an accessory to a
  // conversation: wanting one says nothing about wanting the other, and gating
  // the terminal on the chat's expanded state meant you could never have just a
  // terminal. Mounting the panel spawns a real shell, so it still takes an
  // explicit ask and a workspace to run in.
  const showTerminal = terminalOpen && Boolean(terminal.workspaceSlug);
  const showChat = open && Boolean(session);
  const edgeClass = edge === "right" ? " copilot-dock--edge-right" : " copilot-dock--edge-bottom";

  // Collapsed is the action bar on its own; there is no second bar to draw.
  if (!showChat && !showTerminal) return null;

  const sendQuick = (content: string) => {
    if (!session || session.isBusy || session.loadError) return;
    void session.send(content, { autoApprove: false }).catch(() => {});
  };

  return (
    <div
      className={`copilot-dock copilot-dock--open${edgeClass}`}
      style={edge === "bottom" ? { height } : undefined}
    >
      <div className="copilot-dock-body">
        {showChat && session && (
          <div className="copilot-dock-chat">
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
              <div className="copilot-dock-chips lg-chat-chip-row">
                <span className="copilot-dock-chips-label">Try asking</span>
                {TRY_ASKING[session.kind].map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    className="lg-chat-chip copilot-dock-chip"
                    disabled={session.isBusy || session.loadError}
                    onClick={() => sendQuick(prompt)}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {showTerminal && (
          <div className="copilot-dock-terminal">
            <TerminalPanel
              // Remount on a workspace change rather than letting the panel
              // swap repos under a live shell: the old shell is reaped and a
              // new one starts in the right place.
              key={terminal.workspaceSlug}
              workspaceSlug={terminal.workspaceSlug}
              agent={terminal.agent}
            />
          </div>
        )}
      </div>
    </div>
  );
}
