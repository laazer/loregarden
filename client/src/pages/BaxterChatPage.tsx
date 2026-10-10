import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { api, type Approval, type TicketSummary } from "../api/client";
import { BaxterAvatar } from "../components/chat/BaxterAvatar";
import { ChatComposerMenu } from "../components/chat/ChatComposerMenu";
import { ChatHistorySidebar } from "../components/chat/ChatHistorySidebar";
import { ChatPromptRow } from "../components/chat/ChatPromptRow";
import { ChatSideCard } from "../components/chat/ChatSideCard";
import {
  ComposerAttachButton,
  ComposerAttachmentTray,
  MessageAttachments,
} from "../components/chat/ComposerAttachments";
import { primitiveGallerySections } from "../components/chat/primitiveGallery";
import { primitiveHistory } from "../components/chat/primitiveHistory";
import { PendingApprovalsSection } from "../components/PendingApprovalsSection";
import { StudioChatComposer, StudioChatMessages } from "../components/studio/StudioChat";
import { useApprovalResolution } from "../hooks/useApprovalResolution";
import type { ChatArchive } from "../hooks/useActiveChatSession";
import { useBaxterChatSession } from "../hooks/useBaxterChatSession";
import {
  composerQueueKey,
  useComposerCommands,
  type UseComposerCommandsOptions,
} from "../hooks/useComposerCommands";
import { useComposerAttachments } from "../hooks/useComposerAttachments";
import { useComposerHostActions } from "../hooks/useComposerHostActions";
import { useChatWorkbench } from "../hooks/useChatWorkbench";
import { useChatMessageActions } from "../hooks/useChatMessageActions";
import { useChatWorkspace } from "../hooks/useChatWorkspace";
import { withReplyPrompts } from "../lib/dockChatPrompts";
import { takeHomeBaxterFiles, takeHomeBaxterPrompt } from "../lib/homeBaxter";
import { useUiStore } from "../state/uiStore";
import { pushToast } from "../state/toastStore";
import { formatApprovalResolveError } from "../utils/approvalErrors";
import "./BaxterChatPage.css";

/**
 * Everything a composer needs for `/` and `@` except its own draft.
 *
 * The two composers on this page own their drafts locally so typing does not
 * re-render the thread; the rest is the page's, and identical for both.
 */
type ComposerHostOptions = Omit<UseComposerCommandsOptions, "value" | "onChange">;

/** Sends a turn: the draft's text and whatever files are attached to it. */
type ComposerSend = (text: string, files: File[]) => void;

type ChatRole = "user" | "assistant";

type ChatTurn = {
  id: string;
  role: ChatRole;
  text: string;
  parts?: import("../components/chat/primitives/types").ChatPart[];
  suggestions?: string[];
};

/** Always offered: the question the workbench exists to make one click. */
const MOST_VALUABLE_TICKET = "Find the most valuable ticket";

/** Openers for a new chat. The composer's placeholder already asks "what should we ship". */
const EMPTY_CHIPS = [
  MOST_VALUABLE_TICKET,
  "What should I look at first?",
  "Review what's waiting on me",
  "Triage the stuck tickets",
] as const;

/** As many as one line under the composer holds at the reading width. */
const PROMPT_LIMIT = 4;

function pendingApprovals(approvals: Approval[] | undefined): Approval[] {
  return (approvals ?? []).filter((a) => !a.status || a.status === "pending");
}

function greetingFor(now: Date): string {
  const hour = now.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

function formatDateLine(now: Date): string {
  return now.toLocaleDateString(undefined, {
    weekday: "long",
    month: "short",
    day: "numeric",
  });
}

function suggestionChips(approvals: Approval[], tickets: TicketSummary[]): string[] {
  const blocked = tickets.filter((t) => t.state === "blocked");
  if (approvals.length) {
    return [
      MOST_VALUABLE_TICKET,
      "Review the first approval",
      "Which is most urgent?",
      "Open the Console",
    ];
  }
  if (blocked.length) {
    return [
      MOST_VALUABLE_TICKET,
      "Open the blocked ticket",
      "What's blocking us?",
      "Show in-progress work",
    ];
  }
  return [
    MOST_VALUABLE_TICKET,
    "What should we ship today?",
    "Open the Console",
    "Start a Ticket Studio session",
  ];
}

/**
 * One Baxter composer's local state: its draft, its attachments, its `/` menu,
 * and the props that wire all three into `StudioChatComposer`.
 *
 * Local to each composer so typing does not re-render the thread.
 */
function useBaxterComposer({
  commandOptions,
  onSend,
  busy,
  blocked,
  menu,
}: {
  commandOptions: ComposerHostOptions;
  onSend: ComposerSend;
  busy: boolean;
  blocked: boolean;
  menu: ReactNode;
}) {
  const [draft, setDraft] = useState("");
  const attachments = useComposerAttachments();
  const commands = useComposerCommands({
    ...commandOptions,
    value: draft,
    onChange: setDraft,
  });

  const submit = () => {
    const text = draft.trim();
    const files = attachments.items.map((item) => item.file);
    if ((!text && !files.length) || busy || blocked) return;
    setDraft("");
    attachments.clear();
    onSend(text, files);
  };

  return {
    value: draft,
    onChange: setDraft,
    onSubmit: submit,
    commands,
    canSendEmpty: attachments.items.length > 0,
    onFiles: attachments.add,
    accessory: <ComposerAttachmentTray items={attachments.items} onRemove={attachments.remove} />,
    toolbar: (
      <>
        <ComposerAttachButton onFiles={attachments.add} disabled={blocked} />
        {menu}
      </>
    ),
  };
}

/** Owns draft locally so typing does not re-render the whole chat page. */
function BaxterHeroAsk({
  onSend,
  onStop,
  busy,
  stopping = false,
  blocked = false,
  commandOptions,
  menu,
  prompts,
}: {
  onSend: ComposerSend;
  onStop?: () => void;
  busy: boolean;
  stopping?: boolean;
  /** No workspace resolved yet — nothing can answer the question. */
  blocked?: boolean;
  commandOptions: ComposerHostOptions;
  menu: ReactNode;
  prompts: ReactNode;
}) {
  const composer = useBaxterComposer({ commandOptions, onSend, busy, blocked, menu });

  return (
    <section className="baxter-chat-hero" aria-label="Ask Baxter">
      <div className="baxter-chat-hero-avatar">
        <BaxterAvatar variant="head" state="idle" size={64} label="Baxter" />
      </div>
      <div className="baxter-chat-hero-body">
        <StudioChatComposer
          {...composer}
          onStop={onStop}
          placeholder="What should we ship today?"
          sendLabel="Ask Baxter"
          isSending={busy}
          isStopping={stopping}
          disabled={blocked}
          variant="dock"
          iconOnlySend={false}
        />
        {prompts}
      </div>
    </section>
  );
}

/** Owns draft locally so typing does not re-render the message thread. */
function BaxterReplyDock({
  onSend,
  onStop,
  busy,
  stopping = false,
  blocked = false,
  commandOptions,
  menu,
  prompts,
}: {
  onSend: ComposerSend;
  onStop?: () => void;
  busy: boolean;
  stopping?: boolean;
  /** No workspace resolved yet — nothing can answer the question. */
  blocked?: boolean;
  commandOptions: ComposerHostOptions;
  menu: ReactNode;
  prompts: ReactNode;
}) {
  const composer = useBaxterComposer({ commandOptions, onSend, busy, blocked, menu });

  return (
    <div className="baxter-chat-dock baxter-chat-dock--fade">
      <StudioChatComposer
        {...composer}
        onStop={onStop}
        placeholder="Reply to Baxter…"
        sendLabel="Send"
        isSending={busy}
        isStopping={stopping}
        disabled={blocked}
        variant="dock"
      />
      {prompts}
    </div>
  );
}

export function BaxterChatPage() {
  const historyOpen = useUiStore((s) => s.baxterHistoryOpen);
  const setHistoryOpen = useUiStore((s) => s.setBaxterHistoryOpen);

  const { slug: workspaceSlug } = useChatWorkspace();
  const chat = useBaxterChatSession(workspaceSlug);
  const resolveApproval = useApprovalResolution(undefined);

  /**
   * The primitive gallery only — a canned thread with nothing behind it.
   *
   * Real conversations live on the server and are read through `chat`; this
   * holds the one that has no server side, so opening the gallery cannot be
   * mistaken for a saved conversation or write one.
   */
  const [galleryTurns, setGalleryTurns] = useState<ChatTurn[] | null>(null);
  const initialPromptRef = useRef(takeHomeBaxterPrompt());
  const initialFilesRef = useRef(takeHomeBaxterFiles());
  const resetNonce = useUiStore((s) => s.baxterChatResetNonce);
  const resetSeenRef = useRef(resetNonce);
  const now = useMemo(() => new Date(), []);

  // Workspace inbox for the welcome summary / chips — not the interactive
  // Home-chat cards (those ride on the session snapshot).
  const approvalsQ = useQuery({
    queryKey: ["baxter-chat-approvals"],
    queryFn: () => api.approvals(),
    refetchInterval: 15_000,
  });
  const ticketsQ = useQuery({
    queryKey: ["baxter-chat-tickets", workspaceSlug],
    queryFn: () =>
      api.tickets({
        workspace: workspaceSlug,
        state: ["in_progress", "blocked"],
      }),
    enabled: Boolean(workspaceSlug),
    refetchInterval: 15_000,
  });
  const historyTicketsQ = useQuery({
    queryKey: ["baxter-chat-history-tickets", workspaceSlug],
    queryFn: () => api.tickets({ workspace: workspaceSlug }),
    enabled: historyOpen && Boolean(workspaceSlug),
    staleTime: 15_000,
  });

  const approvals = useMemo(
    () =>
      pendingApprovals(approvalsQ.data).filter((a) => a.workspace_slug === workspaceSlug),
    [approvalsQ.data, workspaceSlug],
  );
  const tickets = useMemo(() => ticketsQ.data ?? [], [ticketsQ.data]);

  const inGallery = galleryTurns !== null;
  const turnApprovals = inGallery ? [] : chat.pendingApprovals;
  const busy = !inGallery && chat.isBusy;
  const awaitingInput = !inGallery && chat.snapshot?.run_status === "awaiting_input";
  const hasThread = inGallery ? galleryTurns.length > 0 : chat.messages.length > 0;
  // A turn waiting on the operator still owns the thread chrome — don't drop
  // back to the welcome hero while the approval card is the thing to answer.
  const isEmpty = !hasThread && !busy && turnApprovals.length === 0;

  const summaryLine = useMemo(() => {
    const parts: string[] = [];
    if (approvals.length) {
      parts.push(`${approvals.length} approval${approvals.length === 1 ? "" : "s"} waiting`);
    }
    const blocked = tickets.filter((t) => t.state === "blocked").length;
    const inProgress = tickets.filter((t) => t.state === "in_progress").length;
    if (blocked) parts.push(`${blocked} blocked`);
    if (inProgress) parts.push(`${inProgress} in progress`);
    if (!parts.length) parts.push("Nothing urgent — ask what we should ship next");
    return parts.join(" · ");
  }, [approvals.length, tickets]);

  const respond = (prompt: string, skill = "", files: File[] = []) => {
    const content = prompt.trim();
    if ((!content && !files.length) || busy || !workspaceSlug) return;
    // Sending from the gallery leaves it: the canned thread is a reference, not
    // a conversation to continue.
    setGalleryTurns(null);
    void chat.send(content, { skill, files }).catch(() => {
      // silent-ok: `chat.error` renders the failure as `sendError` on this
      // page, and send carries meta.errorTitle so the global toast fires too.
    });
  };

  const archive = useMemo<ChatArchive | null>(() => {
    if (!workspaceSlug) return null;
    return {
      workspaceSlug,
      sessionId: chat.sessionId,
      openSession: chat.openSession,
      startNewChat: chat.startNewChat,
      sendInNewChat: chat.sendInNewChat,
      forkSession: chat.forkSession,
      forkFromMessage: chat.forkFromMessage,
      runtime: chat.runtime,
      setRuntime: chat.setRuntime,
      isSavingRuntime: chat.isSavingRuntime,
    };
  }, [
    workspaceSlug,
    chat.sessionId,
    chat.openSession,
    chat.startNewChat,
    chat.sendInNewChat,
    chat.forkSession,
    chat.forkFromMessage,
    chat.runtime,
    chat.setRuntime,
    chat.isSavingRuntime,
  ]);

  // Copy always; fork and "start as ticket" only on the real conversation —
  // the primitive gallery is a canned reference with no server side to branch.
  const messageActions = useChatMessageActions({
    workspaceSlug,
    archive,
    enabled: !inGallery,
  });

  const onAfterNewChat = useCallback(() => {
    setGalleryTurns(null);
    setHistoryOpen(false);
  }, [setHistoryOpen]);

  const commandActions = useComposerHostActions({
    workspaceSlug,
    ticketId: null,
    pendingApprovals: turnApprovals,
    archive,
    session: inGallery ? null : chat,
    onAfterNewChat,
  });

  const commandOptions: ComposerHostOptions = {
    workspaceSlug,
    queueKey: workspaceSlug ? composerQueueKey("baxter-home", chat.sessionId, workspaceSlug) : null,
    isBusy: busy,
    onSend: (content, skill) => respond(content, skill),
    onSendInNewChat: (content) => {
      setGalleryTurns(null);
      void chat.sendInNewChat(content).catch(() => {
        // silent-ok: sendInNewChat is a mutation with meta.errorTitle, so the
        // global MutationCache toast already reported the failure.
      });
    },
    // This is the one thread whose turn carries a skill to the agent.
    skillsEnabled: true,
    actions: commandActions,
  };

  const openPrimitiveGallery = () => {
    const liveTickets = historyTicketsQ.data ?? tickets;
    const sections = primitiveGallerySections({ tickets: liveTickets });
    setGalleryTurns(
      sections.flatMap((section, index) => [
        {
          id: `primitive-gallery-${section.id}-ask`,
          role: "user" as const,
          text: section.ask,
        },
        {
          id: `primitive-gallery-${section.id}-reply`,
          role: "assistant" as const,
          text: section.reply,
          parts: section.parts,
          suggestions:
            index === sections.length - 1
              ? ["Start a new chat", "Open the Console"]
              : undefined,
        },
      ]),
    );
    setHistoryOpen(false);
  };

  const startNewChat = () => {
    setGalleryTurns(null);
    chat.startNewChat();
  };

  useEffect(() => {
    if (resetNonce === resetSeenRef.current) return;
    resetSeenRef.current = resetNonce;
    startNewChat();
    // Only the nonce should trigger this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetNonce]);

  useEffect(() => {
    const handedOff = initialPromptRef.current;
    const handedOffFiles = initialFilesRef.current;
    if (!handedOff && !handedOffFiles.length) return;
    // A turn's approval card is snapshotted from the inbox, so bootstrapping
    // before it lands would strip the first reply of its context.
    if (!workspaceSlug || approvalsQ.isLoading) return;
    initialPromptRef.current = "";
    initialFilesRef.current = [];
    respond(handedOff, "", handedOffFiles);
    // Bootstrap once the workspace and its inbox are known.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, approvalsQ.isLoading]);

  const threadMessages = useMemo(
    () =>
      galleryTurns
        ? galleryTurns.map((t) => ({
            id: t.id,
            role: t.role,
            content: t.text,
            parts: t.parts,
          }))
        : chat.messages,
    [galleryTurns, chat.messages],
  );

  // The one set of one-click prompts: what the latest reply calls for, then what
  // the workspace's state does (approvals, blocked work).
  const prompts = useMemo<readonly string[]>(() => {
    if (galleryTurns) {
      return (
        [...galleryTurns]
          .reverse()
          .find((t) => t.role === "assistant" && t.suggestions?.length)?.suggestions ??
        EMPTY_CHIPS
      );
    }
    if (!hasThread) return EMPTY_CHIPS;
    return withReplyPrompts(suggestionChips(approvals, tickets), chat.messages).slice(
      0,
      PROMPT_LIMIT,
    );
  }, [galleryTurns, hasThread, approvals, tickets, chat.messages]);

  const promptsDisabled = busy || !workspaceSlug;
  // One place at a time: the workbench while it is open, under the composer
  // while it is closed — so closing the workbench never takes the prompts away.
  const promptRow = (
    <ChatPromptRow prompts={prompts} disabled={promptsDisabled} onPick={(text) => respond(text)} />
  );

  const primitives = useMemo(() => primitiveHistory(threadMessages), [threadMessages]);

  // The server's attachments per user turn; the thread view does not carry them.
  const attachmentsByMessage = useMemo(
    () =>
      new Map(
        inGallery
          ? []
          : (chat.snapshot?.messages ?? [])
              .filter((m) => m.attachments?.length)
              .map((m) => [m.id, m.attachments ?? []] as const),
      ),
    [inGallery, chat.snapshot?.messages],
  );

  const {
    open: workbenchOpen,
    overlays: workbenchOverlays,
    setOpen: setWorkbenchOpen,
  } = useChatWorkbench();

  const jumpToMessage = useCallback((messageId: string) => {
    const node = document.querySelector<HTMLElement>(
      `[data-message-id="${CSS.escape(messageId)}"]`,
    );
    if (!node) {
      pushToast({
        tone: "warning",
        title: "Card not in view",
        message: "That turn isn't loaded in this thread any more.",
      });
      return;
    }
    node.scrollIntoView({ behavior: "smooth", block: "start" });
    node.classList.remove("lg-chat-turn--flash");
    // Restarts the highlight when the same card is picked twice in a row.
    void node.offsetWidth;
    node.classList.add("lg-chat-turn--flash");
  }, []);

  const composerMenu = (
    <ChatComposerMenu
      workspaceSlug={workspaceSlug}
      sessionId={inGallery ? "" : chat.sessionId}
      title={chat.title}
      workbenchOpen={workbenchOpen}
      onToggleWorkbench={() => setWorkbenchOpen(!workbenchOpen)}
      onOpenHistory={() => setHistoryOpen(true)}
      onNewChat={startNewChat}
      onOpenGallery={openPrimitiveGallery}
    />
  );

  /**
   * A failed send, shown as chrome rather than as a reply.
   *
   * The old page pushed the error into the thread as an assistant turn; with
   * the thread server-owned that would be a message Baxter never sent, and it
   * would vanish on the next read anyway.
   */
  const sendError = chat.error ? (
    <p className="baxter-chat-error" role="alert">
      {chat.error}
    </p>
  ) : null;

  const stopTurn = () =>
    void chat.stop().catch(() => {
      // silent-ok: stop is a mutation with meta.errorTitle, so the
      // global MutationCache toast reports a stop that did not take.
    });

  return (
    <div className="baxter-chat-layout">
      <div className="baxter-chat lg-chat-surface">
        {isEmpty ? (
          <div className="baxter-chat-welcome">
            <header className="baxter-chat-intro">
              <p className="baxter-chat-kicker">{formatDateLine(now)}</p>
              <h1 className="baxter-chat-greeting">{greetingFor(now)}</h1>
              <p className="baxter-chat-summary">{summaryLine}</p>
            </header>

            <BaxterHeroAsk
              onSend={(text, files) => respond(text, "", files)}
              onStop={stopTurn}
              busy={busy}
              stopping={chat.isStopping}
              blocked={!workspaceSlug}
              commandOptions={commandOptions}
              menu={composerMenu}
              prompts={workbenchOpen ? null : promptRow}
            />
            {sendError}
          </div>
        ) : (
          <>
            <div className="baxter-chat-thread baxter-chat-thread--faded" aria-live="polite">
              <StudioChatMessages
                messages={threadMessages}
                isThinking={busy && !awaitingInput && turnApprovals.length === 0}
                activeTurnId={inGallery ? null : chat.activeTurnId}
                thinkingMessage="Baxter is looking…"
                thinkingSub="Fetching a reply from your workspace model"
                thinkingActivity="typing"
                assistantLabel="Baxter"
                showAssistantAvatar={false}
                messageActions={messageActions}
                onPrimitiveSubmit={(content) => void respond(content)}
                renderAfterMessage={(message) => {
                  const sent = attachmentsByMessage.get(message.id);
                  return sent ? (
                    <MessageAttachments
                      attachments={sent}
                      workspaceSlug={workspaceSlug}
                      sessionId={chat.sessionId}
                    />
                  ) : null;
                }}
                // AskUserQuestion / permissions arrive as approvals, not messages.
                // Render them as the agent's ask at the end of the thread — not a
                // chrome strip layered above the conversation.
                trailingAsk={
                  turnApprovals.length ? (
                    <PendingApprovalsSection
                      variant="ask"
                      approvals={turnApprovals}
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
                  ) : null
                }
              />
            </div>

            {sendError}
            <BaxterReplyDock
              onSend={(text, files) => respond(text, "", files)}
              onStop={stopTurn}
              busy={busy}
              stopping={chat.isStopping}
              blocked={!workspaceSlug}
              commandOptions={commandOptions}
              menu={composerMenu}
              prompts={workbenchOpen ? null : promptRow}
            />
          </>
        )}
      </div>
      {workbenchOpen ? (
        <ChatSideCard
          overlay={workbenchOverlays}
          prompts={prompts}
          promptsDisabled={promptsDisabled}
          onPrompt={(text) => respond(text)}
          primitives={primitives}
          onJumpTo={jumpToMessage}
          onOpenGallery={openPrimitiveGallery}
          onCollapse={() => setWorkbenchOpen(false)}
        />
      ) : null}
      <ChatHistorySidebar
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onOpenPrimitiveGallery={openPrimitiveGallery}
        workspaceSlug={workspaceSlug}
        activeSessionId={galleryTurns ? "" : chat.sessionId}
        onSelectSession={(id) => {
          setGalleryTurns(null);
          chat.openSession(id);
          setHistoryOpen(false);
        }}
        onDeleteSession={(id) => void chat.deleteSession(id)}
      />
    </div>
  );
}
