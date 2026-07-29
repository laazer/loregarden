import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, api, type Approval, type TicketSummary } from "../api/client";
import { BaxterAvatar } from "../components/chat/BaxterAvatar";
import { ChatHistorySidebar } from "../components/chat/ChatHistorySidebar";
import { primitiveGalleryParts } from "../components/chat/primitiveGallery";
import { StudioChatComposer, StudioChatMessages } from "../components/studio/StudioChat";
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

function nextId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
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
}: {
  onSend: (text: string) => void;
  busy: boolean;
}) {
  const [draft, setDraft] = useState("");

  const submit = () => {
    const text = draft.trim();
    if (!text || busy) return;
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
          disabled={busy}
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
}: {
  onSend: (text: string) => void;
  busy: boolean;
  suggestions?: string[];
}) {
  const [draft, setDraft] = useState("");

  const submit = () => {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    onSend(text);
  };

  return (
    <div className="baxter-chat-dock">
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
        disabled={busy}
        variant="dock"
        showShortcut
      />
    </div>
  );
}

export function BaxterChatPage() {
  const navigate = useNavigate();
  const workspace = useUiStore((s) => s.workspace);
  const setInboxOpen = useUiStore((s) => s.setInboxOpen);
  const historyOpen = useUiStore((s) => s.baxterHistoryOpen);
  const setHistoryOpen = useUiStore((s) => s.setBaxterHistoryOpen);

  const workspacesQ = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const workspaceSlug =
    workspace && workspace !== "all"
      ? workspace
      : (workspacesQ.data?.[0]?.slug ?? "loregarden");
  const workspaceParam = workspace && workspace !== "all" ? workspace : undefined;

  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [busy, setBusy] = useState(false);
  const initialPromptRef = useRef(takeHomeBaxterPrompt());
  const turnsRef = useRef<ChatTurn[]>([]);
  const resetNonce = useUiStore((s) => s.baxterChatResetNonce);
  const resetSeenRef = useRef(resetNonce);
  const now = useMemo(() => new Date(), []);

  const approvalsQ = useQuery({
    queryKey: ["baxter-chat-approvals"],
    queryFn: () => api.approvals(),
    refetchInterval: 15_000,
  });
  const ticketsQ = useQuery({
    queryKey: ["baxter-chat-tickets", workspaceParam],
    queryFn: () =>
      api.tickets({
        workspace: workspaceParam,
        state: ["in_progress", "blocked"],
      }),
    refetchInterval: 15_000,
  });
  const historyTicketsQ = useQuery({
    queryKey: ["baxter-chat-history-tickets", workspaceParam],
    queryFn: () => api.tickets({ workspace: workspaceParam }),
    enabled: historyOpen,
    staleTime: 15_000,
  });

  const approvals = useMemo(() => pendingApprovals(approvalsQ.data), [approvalsQ.data]);
  const tickets = ticketsQ.data ?? [];
  const latestSuggestions = [...turns].reverse().find((t) => t.role === "assistant" && t.suggestions?.length)
    ?.suggestions;
  const isEmpty = turns.length === 0 && !busy;

  useEffect(() => {
    turnsRef.current = turns;
  }, [turns]);

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

  const respond = async (prompt: string) => {
    const content = prompt.trim();
    if (!content || busy) return;
    setBusy(true);
    const history = turnsRef.current.map((t) => ({ role: t.role, content: t.text }));
    const userTurn: ChatTurn = { id: nextId("user"), role: "user", text: content };
    setTurns((prev) => [...prev, userTurn]);

    try {
      const { reply, parts } = await api.sendBaxterChatMessage(workspaceSlug, {
        content,
        history,
      });
      setTurns((prev) => [
        ...prev,
        {
          id: nextId("assistant"),
          role: "assistant",
          text: reply.trim() || "Baxter returned an empty reply.",
          parts: parts as ChatTurn["parts"],
          approvals: approvals.slice(0, 5),
          suggestions: suggestionChips(approvals, tickets),
        },
      ]);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Baxter could not answer right now.";
      setTurns((prev) => [
        ...prev,
        {
          id: nextId("assistant"),
          role: "assistant",
          text: message,
          suggestions: ["Try again", "Open the Console", "Check workspace runtime"],
        },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const startNewChat = () => {
    setTurns([]);
    setBusy(false);
  };

  const openPrimitiveGallery = () => {
    const liveTickets = historyTicketsQ.data ?? tickets;
    setTurns([
      {
        id: "primitive-gallery-user",
        role: "user",
        text: "Show me examples of every chat UI primitive.",
      },
      {
        id: "primitive-gallery-assistant",
        role: "assistant",
        text: "Here is the complete Chat UI Primitive gallery.",
        parts: primitiveGalleryParts({ tickets: liveTickets }),
        suggestions: ["Start a new chat", "Open the Console"],
      },
    ]);
    setBusy(false);
    setHistoryOpen(false);
  };

  useEffect(() => {
    if (resetNonce === resetSeenRef.current) return;
    resetSeenRef.current = resetNonce;
    startNewChat();
  }, [resetNonce]);

  useEffect(() => {
    const handedOff = initialPromptRef.current;
    if (!handedOff) return;
    if (workspacesQ.isLoading) return;
    initialPromptRef.current = "";
    void respond(handedOff);
    // Bootstrap once workspace slug is known.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspacesQ.isLoading, workspaceSlug]);

  const threadMessages = useMemo(
    () =>
      turns.map((t) => ({
        id: t.id,
        role: t.role,
        content: t.text,
        parts: t.parts,
      })),
    [turns],
  );

  const approvalsByTurnId = useMemo(() => {
    const map = new Map<string, Approval[]>();
    for (const turn of turns) {
      if (turn.approvals?.length) map.set(turn.id, turn.approvals);
    }
    return map;
  }, [turns]);

  return (
    <div className={`baxter-chat lg-chat-surface${isEmpty ? " baxter-chat--empty" : ""}`}>
      {isEmpty ? (
        <div className="baxter-chat-welcome">
          <header className="baxter-chat-intro">
            <p className="baxter-chat-kicker">{formatDateLine(now)}</p>
            <h1 className="baxter-chat-greeting">{greetingFor(now)}</h1>
            <p className="baxter-chat-summary">{summaryLine}</p>
          </header>

          <BaxterHeroAsk onSend={(text) => void respond(text)} busy={busy} />
        </div>
      ) : (
        <>
          <div className="baxter-chat-thread" aria-live="polite">
            <StudioChatMessages
              messages={threadMessages}
              isThinking={busy}
              thinkingMessage="Baxter is looking…"
              thinkingSub="Fetching a reply from your workspace model"
              thinkingActivity="typing"
              assistantLabel="Baxter"
              showAssistantAvatar={false}
              renderAfterMessage={(message) => {
                const turnApprovals = approvalsByTurnId.get(message.id);
                if (!turnApprovals?.length) return null;
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
                              navigate(ticketPath(a.ticket_id, "triage"));
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

          <BaxterReplyDock
            onSend={(text) => void respond(text)}
            busy={busy}
            suggestions={latestSuggestions}
          />
        </>
      )}
      <ChatHistorySidebar
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onOpenPrimitiveGallery={openPrimitiveGallery}
      />
    </div>
  );
}
