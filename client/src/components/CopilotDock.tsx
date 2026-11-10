import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { useActiveChatSession } from "../hooks/useActiveChatSession";
import { useApprovalResolution } from "../hooks/useApprovalResolution";
import { formatApprovalResolveError } from "../utils/approvalErrors";
import { CopilotDockHeader } from "./CopilotDockHeader";
import { DockResizeHandle } from "./DockResizeHandle";
import { PendingApprovalsSection } from "./PendingApprovalsSection";
import { ChatArtifactsList } from "./chat/ChatArtifactsList";
import { DockChatWelcome } from "./chat/DockChatWelcome";
import { GardenSky } from "./GardenSky";
import { ChatHistoryRail } from "./chat/ChatHistoryRail";
import { showsChatWalker } from "../lib/chatSession";
import { primitiveHistory } from "./chat/primitiveHistory";
import { Button } from "./ui/Button";
import { showChatMessage } from "../lib/chatJump";
import { quickPrompts as promptsFor } from "../lib/dockChatPrompts";
import { useTerminalTarget } from "../hooks/useTerminalTarget";
import { useLayoutDockEdge, useUiStore } from "../state/uiStore";
import { ChatWaitStatus } from "./chat/ChatWaitStatus";
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
  const edge = useLayoutDockEdge();
  const setDockPanelsVisible = useUiStore((s) => s.setDockPanelsVisible);
  const {
    session,
    label,
    ticketId,
    pendingApprovals,
    branch,
    archive,
    agents,
    assistantLabel,
    maximisePath,
    promptKind,
    composerBlocked,
  } = useActiveChatSession();
  const titleId = useId();
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

  // Which tab the panel shows, remembered per conversation: moving to another
  // ticket opens its chat, not the artifacts of the last one.
  const conversation = archive?.sessionId ?? ticketId ?? label;
  const [tabFor, setTabFor] = useState<{ conversation: string; tab: DockTab }>({ conversation, tab: "chat" });
  const tab: DockTab = tabFor.conversation === conversation ? tabFor.tab : "chat";
  const setTab = (next: DockTab) => setTabFor({ conversation, tab: next });
  const chatRef = useRef<HTMLDivElement>(null);
  const messages = session?.messages;
  const artifacts = useMemo(() => primitiveHistory(messages ?? []), [messages]);

  const showTerminal = terminalOpen && Boolean(keptSlug);
  const mountTerminal = Boolean(keptSlug);
  const showChat = open && Boolean(session);
  const panelsVisible = showChat || showTerminal;
  // Published for the layout, which collapses a right-docked column to the bar
  // when nothing is showing. Before paint, so opening a panel never flashes
  // the bottom layout first.
  useLayoutEffect(() => {
    setDockPanelsVisible(panelsVisible);
  }, [panelsVisible, setDockPanelsVisible]);
  useLayoutEffect(() => () => setDockPanelsVisible(false), [setDockPanelsVisible]);
  const edgeClass = edge === "right" ? " copilot-dock--edge-right" : " copilot-dock--edge-bottom";
  const keptOnlyClass = !panelsVisible && mountTerminal ? " copilot-dock--kept-only" : "";

  // Collapsed is the action bar on its own; there is no second bar to draw.
  // A kept-but-hidden shell still mounts so the process survives the toggle.
  if (!panelsVisible && !mountTerminal) return null;

  // The rail is the archive, and only while it was asked for. The openers live
  // in the empty thread instead: a sidebar is too narrow for a rail, and hid them.
  const showHistory = Boolean(archive) && historyOpen;
  const openers = session && !composerBlocked ? promptsFor(promptKind, branch) : [];
  // On a ticket the approvals are the ticket's, not this turn's: its stages
  // raise them whatever the chat is doing. Pinned to the foot of the thread
  // they read as the latest reply and hid the walker, so there they are pinned
  // above the turns instead. Elsewhere an approval is the agent asking in this
  // conversation, and stays in the thread.
  const approvalsInThread = !ticketId;
  const approvalsPinned = !approvalsInThread && pendingApprovals.length > 0;
  const approvals = pendingApprovals.length ? (
    <PendingApprovalsSection
      variant="ask"
      approvals={pendingApprovals}
      submittingApprovalId={
        resolveApproval.isPending
          ? resolveApproval.variables?.id ?? null
          : null
      }
      submitError={
        resolveApproval.isError
          ? formatApprovalResolveError(resolveApproval.error)
          : null
      }
      onApprove={(approval, payload) =>
        resolveApproval.mutate({
          id: approval.id,
          action: "approve",
          ...payload,
        })
      }
      onRecheck={(approval, payload) =>
        resolveApproval.mutate({
          id: approval.id,
          action: "recheck",
          ...payload,
        })
      }
      onReject={(approval, payload) =>
        resolveApproval.mutate({
          id: approval.id,
          action: "reject",
          ...payload,
        })
      }
    />
  ) : null;

  // Loading draws nothing: a welcome flashing in front of a thread that is
  // about to arrive would read as the conversation being gone.
  const showWelcome =
    Boolean(session) &&
    !session?.isLoading &&
    session?.messages.length === 0 &&
    !showsChatWalker(Boolean(session?.isBusy), session?.runStatus) &&
    session?.runStatus !== "waiting" &&
    !(approvalsInThread && pendingApprovals.length > 0);

  const sendQuick = (content: string) => {
    if (!session || session.isBusy || session.loadError || composerBlocked) return;
    void session.send(content, { autoApprove: false }).catch(() => {
      // silent-ok: send is a mutation carrying meta.errorTitle, so the global
      // MutationCache toast reports the failure and session.error renders it.
    });
  };

  return (
    <div
      className={`copilot-dock${edgeClass}${keptOnlyClass}`}
      style={edge === "bottom" && panelsVisible ? { height } : undefined}
      aria-hidden={!panelsVisible ? true : undefined}
    >
      {/* Only with a panel open: there is nothing to resize when the dock is
          just the action bar, and a grip on it would drag a hidden pane. */}
      {panelsVisible ? <DockResizeHandle edge={edge} /> : null}
      <div className={`copilot-dock-body${showTerminal && !showChat ? " copilot-dock-body--bleed" : ""}`}>
        {showChat && session && (
          <section className="copilot-dock-chat" aria-labelledby={titleId}>
            <CopilotDockHeader
              titleId={titleId}
              context={label}
              agents={agents}
              busy={session.isBusy}
              archive={archive}
              maximisePath={maximisePath}
            />
            <DockTabs
              idPrefix={titleId}
              tab={tab}
              artifactCount={artifacts.length}
              onSelect={setTab}
            />
            <div
              className="copilot-dock-tab-panel copilot-dock-artifacts"
              role="tabpanel"
              id={`${titleId}-panel-artifacts`}
              aria-labelledby={`${titleId}-tab-artifacts`}
              hidden={tab !== "artifacts"}
            >
              {tab === "artifacts" ? (
                <ChatArtifactsList
                  entries={artifacts}
                  onSubmit={sendQuick}
                  onShowInChat={(messageId) => {
                    setTab("chat");
                    // After the chat panel is shown again: a hidden turn cannot scroll into view.
                    requestAnimationFrame(() => showChatMessage(messageId, chatRef.current ?? document));
                  }}
                />
              ) : null}
            </div>
            <div
              ref={chatRef}
              className="copilot-dock-chat-row copilot-dock-tab-panel"
              role="tabpanel"
              id={`${titleId}-panel-chat`}
              aria-labelledby={`${titleId}-tab-chat`}
              hidden={tab !== "chat"}
            >
            <div className="copilot-dock-chat-main">
              {/* The chat page's night sky, behind the turns. Its vines stay on
                  the page: a sidebar has no room for them. */}
              <GardenSky />
              {approvalsPinned ? (
                <aside className="copilot-dock-approvals" aria-labelledby={`${titleId}-approvals`}>
                  <h3 id={`${titleId}-approvals`} className="copilot-dock-approvals-title">
                    Waiting on you <span className="copilot-dock-approvals-count">{pendingApprovals.length}</span>
                  </h3>
                  {approvals}
                </aside>
              ) : null}
              {showWelcome ? (
                <DockChatWelcome
                  assistantLabel={assistantLabel}
                  context={label}
                  promptKind={promptKind}
                  prompts={openers}
                  promptsDisabled={session.isBusy}
                  blocked={composerBlocked}
                  loadError={Boolean(session.loadError)}
                  onPick={sendQuick}
                  onOpenHistory={archive && !showHistory ? () => setHistoryOpen(true) : undefined}
                />
              ) : (
              <StudioChatMessages
                messages={session.messages}
                emptyMessage={session.isLoading ? "" : (composerBlocked ?? undefined)}
                isThinking={
                  showsChatWalker(session.isBusy, session.runStatus) &&
                  !(approvalsInThread && approvals)
                }
                trailingStatus={
                  session.runStatus === "waiting" && session.wait ? (
                    <ChatWaitStatus wait={session.wait} />
                  ) : null
                }
                thinkingMessage="Baxter is looking…"
                activeTurnId={session.activeTurnId}
                assistantLabel={assistantLabel}
                // The action bar already shows the agent beside the composer; a
                // head on every reply only spent the thread's width.
                showAssistantAvatar={false}
                // On a ticket the thread can hold nothing focusable (plain-text
                // replies, approvals pinned above it), so the scroller is its
                // own tab stop or the keyboard cannot scroll it.
                className="copilot-dock-messages scroll-focus-ring"
                role="group"
                aria-label="Conversation"
                tabIndex={0}
                onPrimitiveSubmit={(content) => sendQuick(content)}
                // Agent questions arrive as approvals, not messages — show them
                // as the agent's ask inside the thread, not as dock chrome.
                trailingAsk={approvalsInThread ? approvals : null}
              />
              )}
            </div>
            {showHistory && archive && (
              // Beside the turns rather than over them: switching threads is a
              // detour, and the conversation stays readable while it lasts.
              <aside className="copilot-dock-rail">
                <ChatHistoryRail
                  scope={archive.scope}
                  activeSessionId={archive.sessionId}
                  onSelectSession={(id) => {
                    archive.openSession(id);
                    // The rail was a detour: showing the picked conversation
                    // matters more than keeping the list up.
                    setHistoryOpen(false);
                  }}
                />
              </aside>
            )}
            </div>
          </section>
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

type DockTab = "chat" | "artifacts";

const DOCK_TABS: readonly DockTab[] = ["chat", "artifacts"];

/**
 * The conversation, or everything it has made. Arrow keys move between the
 * two as a tablist's do; the panels stay mounted so the thread keeps its
 * scroll position across a switch.
 */
function DockTabs({
  idPrefix,
  tab,
  artifactCount,
  onSelect,
}: {
  idPrefix: string;
  tab: DockTab;
  artifactCount: number;
  onSelect: (tab: DockTab) => void;
}) {
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    const next = DOCK_TABS[(DOCK_TABS.indexOf(tab) + 1) % DOCK_TABS.length];
    onSelect(next);
    document.getElementById(`${idPrefix}-tab-${next}`)?.focus();
  };
  return (
    <div className="copilot-dock-tabs" role="tablist" aria-label="Baxter" onKeyDown={onKeyDown}>
      {DOCK_TABS.map((id) => (
        <Button
          key={id}
          variant="plain"
          role="tab"
          id={`${idPrefix}-tab-${id}`}
          aria-selected={tab === id}
          aria-controls={`${idPrefix}-panel-${id}`}
          tabIndex={tab === id ? 0 : -1}
          className={`copilot-dock-tab${tab === id ? " is-on" : ""}`}
          onClick={() => onSelect(id)}
        >
          {id === "chat" ? "Chat" : "Artifacts"}
          {id === "artifacts" && artifactCount > 0 ? <span className="copilot-dock-tab-count">{artifactCount}</span> : null}
        </Button>
      ))}
    </div>
  );
}
