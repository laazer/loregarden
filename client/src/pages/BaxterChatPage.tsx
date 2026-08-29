import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, type Approval, type TicketSummary } from "../api/client";
import { BaxterAvatar } from "../components/chat/BaxterAvatar";
import { ChatHistorySidebar } from "../components/chat/ChatHistorySidebar";
import { primitiveGallerySections } from "../components/chat/primitiveGallery";
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
import { useComposerHostActions } from "../hooks/useComposerHostActions";
import { useChatWorkspace } from "../hooks/useChatWorkspace";
import { takeHomeBaxterPrompt } from "../lib/homeBaxter";
import { useUiStore } from "../state/uiStore";
import { formatApprovalResolveError } from "../utils/approvalErrors";
import "./BaxterChatPage.css";

/**
 * Everything a composer needs for `/` and `@` except its own draft.
 *
 * The two composers on this page own their drafts locally so typing does not
 * re-render the thread; the rest is the page's, and identical for both.
 */
type ComposerHostOptions = Omit<UseComposerCommandsOptions, "value" | "onChange">;

type ChatRole = "user" | "assistant";

type ChatTurn = {
  id: string;
  role: ChatRole;
  text: string;
  parts?: import("../components/chat/primitives/types").ChatPart[];
  suggestions?: string[];
};

const EMPTY_CHIPS = [
  "What should we ship today?",
  "What should I look at first?",
  "Review what's waiting on me",
  "Triage the stuck tickets",
] as const;

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
    return ["Review the first approval", "Which is most urgent?", "Open the Console"];
  }
  if (blocked.length) {
    return ["Open the blocked ticket", "What's blocking us?", "Show in-progress work"];
  }
  return ["What should we ship today?", "Open the Console", "Start a Ticket Studio session"];
}

/** Owns draft locally so typing does not re-render the whole chat page. */
function BaxterHeroAsk({
  onSend,
  onStop,
  busy,
  stopping = false,
  blocked = false,
  commandOptions,
}: {
  onSend: (text: string) => void;
  onStop?: () => void;
  busy: boolean;
  stopping?: boolean;
  /** No workspace resolved yet — nothing can answer the question. */
  blocked?: boolean;
  commandOptions: ComposerHostOptions;
}) {
  const [draft, setDraft] = useState("");
  const commands = useComposerCommands({
    ...commandOptions,
    value: draft,
    onChange: setDraft,
  });

  const submit = () => {
    const text = draft.trim();
    if (!text || busy || blocked) return;
    setDraft("");
    onSend(text);
  };

  return (
    <section className="baxter-chat-hero" aria-label="Ask Baxter">
      <div className="baxter-chat-hero-avatar">
        <BaxterAvatar variant="head" state="idle" size={64} label="Baxter" />
      </div>
      <div className="baxter-chat-hero-body">
        <StudioChatComposer
          value={draft}
          onChange={setDraft}
          onSubmit={submit}
          onStop={onStop}
          placeholder="What should we ship today?"
          sendLabel="Ask Baxter"
          isSending={busy}
          isStopping={stopping}
          disabled={blocked}
          variant="dock"
          iconOnlySend={false}
          commands={commands}
        />
        <div className="lg-chat-chip-row" role="list">
          {EMPTY_CHIPS.map((chip) => (
            <button
              key={chip}
              type="button"
              className="lg-chat-chip"
              role="listitem"
              onClick={() => setDraft(chip)}
            >
              {chip}
            </button>
          ))}
        </div>
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
  suggestions,
  blocked = false,
  commandOptions,
}: {
  onSend: (text: string) => void;
  onStop?: () => void;
  busy: boolean;
  stopping?: boolean;
  suggestions?: string[];
  /** No workspace resolved yet — nothing can answer the question. */
  blocked?: boolean;
  commandOptions: ComposerHostOptions;
}) {
  const [draft, setDraft] = useState("");
  const commands = useComposerCommands({
    ...commandOptions,
    value: draft,
    onChange: setDraft,
  });

  const submit = () => {
    const text = draft.trim();
    if (!text || busy || blocked) return;
    setDraft("");
    onSend(text);
  };

  return (
    <div className="baxter-chat-dock baxter-chat-dock--fade">
      {suggestions && suggestions.length > 0 && !busy ? (
        <div className="lg-chat-chip-row baxter-chat-suggestions">
          {suggestions.map((chip) => (
            <button
              key={chip}
              type="button"
              className="lg-chat-chip"
              onClick={() => onSend(chip)}
            >
              {chip}
            </button>
          ))}
        </div>
      ) : null}

      <StudioChatComposer
        value={draft}
        onChange={setDraft}
        onSubmit={submit}
        onStop={onStop}
        placeholder="Reply to Baxter…"
        sendLabel="Send"
        isSending={busy}
        isStopping={stopping}
        disabled={blocked}
        variant="dock"
        showShortcut
        commands={commands}
      />
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
  const tickets = ticketsQ.data ?? [];

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

  const respond = (prompt: string, skill = "") => {
    const content = prompt.trim();
    if (!content || busy || !workspaceSlug) return;
    // Sending from the gallery leaves it: the canned thread is a reference, not
    // a conversation to continue.
    setGalleryTurns(null);
    // The failure is shown from `chat.error`; swallowing here keeps a failed
    // send from surfacing as an unhandled rejection as well.
    void chat.send(content, { skill }).catch(() => undefined);
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
    chat.runtime,
    chat.setRuntime,
    chat.isSavingRuntime,
  ]);

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
      void chat.sendInNewChat(content).catch(() => undefined);
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
    if (!handedOff) return;
    // A turn's approval card is snapshotted from the inbox, so bootstrapping
    // before it lands would strip the first reply of its context.
    if (!workspaceSlug || approvalsQ.isLoading) return;
    initialPromptRef.current = "";
    respond(handedOff);
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

  const latestSuggestions = useMemo(() => {
    if (galleryTurns) {
      return [...galleryTurns]
        .reverse()
        .find((t) => t.role === "assistant" && t.suggestions?.length)?.suggestions;
    }
    const last = threadMessages[threadMessages.length - 1];
    return last?.role === "assistant" ? suggestionChips(approvals, tickets) : undefined;
  }, [galleryTurns, threadMessages, approvals, tickets]);

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

  return (
    <div className="baxter-chat lg-chat-surface">
      {isEmpty ? (
        <div className="baxter-chat-welcome">
          <header className="baxter-chat-intro">
            <p className="baxter-chat-kicker">{formatDateLine(now)}</p>
            <h1 className="baxter-chat-greeting">{greetingFor(now)}</h1>
            <p className="baxter-chat-summary">{summaryLine}</p>
          </header>

          <BaxterHeroAsk
            onSend={(text) => void respond(text)}
            onStop={() => void chat.stop().catch(() => undefined)}
            busy={busy}
            stopping={chat.isStopping}
            blocked={!workspaceSlug}
            commandOptions={commandOptions}
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
              onPrimitiveSubmit={(content) => void respond(content)}
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
            onSend={(text) => void respond(text)}
            onStop={() => void chat.stop().catch(() => undefined)}
            busy={busy}
            stopping={chat.isStopping}
            suggestions={latestSuggestions}
            blocked={!workspaceSlug}
            commandOptions={commandOptions}
          />
        </>
      )}
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
