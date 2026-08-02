import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, type Approval, type TicketSummary } from "../api/client";
import { BaxterAvatar } from "../components/chat/BaxterAvatar";
import { ChatHistorySidebar } from "../components/chat/ChatHistorySidebar";
import { primitiveGallerySections } from "../components/chat/primitiveGallery";
import { StudioChatComposer, StudioChatMessages } from "../components/studio/StudioChat";
import { useBaxterChatSession } from "../hooks/useBaxterChatSession";
import { useChatWorkspace } from "../hooks/useChatWorkspace";
import { ticketPath } from "../lib/appNavigation";
import { takeHomeBaxterPrompt } from "../lib/homeBaxter";
import { useUiStore } from "../state/uiStore";
import "./BaxterChatPage.css";

type ChatRole = "user" | "assistant";

type ChatTurn = {
  id: string;
  role: ChatRole;
  text: string;
  parts?: import("../components/chat/primitives/types").ChatPart[];
  approvals?: Approval[];
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
  busy,
  blocked = false,
}: {
  onSend: (text: string) => void;
  busy: boolean;
  /** No workspace resolved yet — nothing can answer the question. */
  blocked?: boolean;
}) {
  const [draft, setDraft] = useState("");

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
          placeholder="What should we ship today?"
          sendLabel="Ask Baxter"
          isSending={busy}
          disabled={busy || blocked}
          variant="dock"
          iconOnlySend={false}
        />
        <div className="lg-chat-chip-row baxter-chat-chip-row" role="list">
          {EMPTY_CHIPS.map((chip) => (
            <button
              key={chip}
              type="button"
              className="lg-chat-chip baxter-chat-chip"
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
  busy,
  suggestions,
  blocked = false,
}: {
  onSend: (text: string) => void;
  busy: boolean;
  suggestions?: string[];
  /** No workspace resolved yet — nothing can answer the question. */
  blocked?: boolean;
}) {
  const [draft, setDraft] = useState("");

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
              className="lg-chat-chip baxter-chat-chip"
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
        placeholder="Reply to Baxter…"
        sendLabel="Send"
        isSending={busy}
        disabled={busy || blocked}
        variant="dock"
        showShortcut
      />
    </div>
  );
}

export function BaxterChatPage() {
  const navigate = useNavigate();
  const setInboxOpen = useUiStore((s) => s.setInboxOpen);
  const historyOpen = useUiStore((s) => s.baxterHistoryOpen);
  const setHistoryOpen = useUiStore((s) => s.setBaxterHistoryOpen);

  const { slug: workspaceSlug } = useChatWorkspace();
  const chat = useBaxterChatSession(workspaceSlug);

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

  // The inbox route has no workspace filter, so narrow the global list here.
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
  const busy = !inGallery && chat.isBusy;
  const hasThread = inGallery ? galleryTurns.length > 0 : chat.messages.length > 0;
  const isEmpty = !hasThread && !busy;

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

  const respond = (prompt: string) => {
    const content = prompt.trim();
    if (!content || busy || !workspaceSlug) return;
    // Sending from the gallery leaves it: the canned thread is a reference, not
    // a conversation to continue.
    setGalleryTurns(null);
    // The failure is shown from `chat.error`; swallowing here keeps a failed
    // send from surfacing as an unhandled rejection as well.
    void chat.send(content).catch(() => undefined);
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
   * Approvals ride under the newest reply only.
   *
   * The pending inbox is live state, not something a past turn owns — pinning a
   * stale copy to every historical reply would show the operator work that is
   * already resolved, and pinning it to none loses the prompt entirely.
   */
  const approvalsMessageId = useMemo(() => {
    if (galleryTurns || !approvals.length) return null;
    const last = threadMessages[threadMessages.length - 1];
    return last?.role === "assistant" ? last.id : null;
  }, [galleryTurns, approvals.length, threadMessages]);

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
    <div className={`baxter-chat lg-chat-surface${isEmpty ? " baxter-chat--empty" : ""}`}>
      {isEmpty ? (
        <div className="baxter-chat-welcome">
          <header className="baxter-chat-intro">
            <p className="baxter-chat-kicker">{formatDateLine(now)}</p>
            <h1 className="baxter-chat-greeting">{greetingFor(now)}</h1>
            <p className="baxter-chat-summary">{summaryLine}</p>
          </header>

          <BaxterHeroAsk
            onSend={(text) => void respond(text)}
            busy={busy}
            blocked={!workspaceSlug}
          />
          {sendError}
        </div>
      ) : (
        <>
          <div className="baxter-chat-thread baxter-chat-thread--faded" aria-live="polite">
            <StudioChatMessages
              messages={threadMessages}
              isThinking={busy}
              activeTurnId={inGallery ? null : chat.activeTurnId}
              thinkingMessage="Baxter is looking…"
              thinkingSub="Fetching a reply from your workspace model"
              thinkingActivity="typing"
              assistantLabel="Baxter"
              showAssistantAvatar={false}
              onPrimitiveSubmit={(content) => void respond(content)}
              renderAfterMessage={(message) => {
                if (message.id !== approvalsMessageId) return null;
                const turnApprovals = approvals.slice(0, 5);
                return (
                  <div className="baxter-chat-card">
                    <div className="baxter-chat-card-head">
                      <span className="baxter-chat-card-title">
                        ● {turnApprovals.length} approval
                        {turnApprovals.length === 1 ? "" : "s"} waiting on you
                      </span>
                      <button
                        type="button"
                        className="baxter-chat-card-link"
                        onClick={() => {
                          setInboxOpen(true);
                          navigate("/console");
                        }}
                      >
                        Open Triage →
                      </button>
                    </div>
                    <ul className="baxter-chat-card-list">
                      {turnApprovals.map((a) => (
                        <li key={a.id} className="baxter-chat-card-row">
                          <div className="baxter-chat-card-row-main">
                            <span className="baxter-chat-card-row-title">
                              {a.title || a.tool_name || "Approval"}
                            </span>
                            <span className="baxter-chat-card-row-meta">
                              {[a.workspace_slug, a.ticket_external_id, a.kind]
                                .filter(Boolean)
                                .join(" · ")}
                            </span>
                          </div>
                          <button
                            type="button"
                            className="baxter-chat-card-review"
                            onClick={() => {
                              setInboxOpen(true);
                              if (a.ticket_id) {
                                navigate(ticketPath(a.ticket_id));
                              }
                            }}
                          >
                            Review
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              }}
            />
          </div>

          {sendError}
          <BaxterReplyDock
            onSend={(text) => void respond(text)}
            busy={busy}
            suggestions={latestSuggestions}
            blocked={!workspaceSlug}
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
