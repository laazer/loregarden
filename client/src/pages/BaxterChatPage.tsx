import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, api, type Approval, type TicketSummary } from "../api/client";
import { BaxterAvatar } from "../components/chat/BaxterAvatar";
import { MarkdownContent } from "../components/chat/MarkdownContent";
import { StudioChatComposer } from "../components/studio/StudioChat";
import { ticketPath } from "../lib/appNavigation";
import { takeHomeBaxterPrompt } from "../lib/homeBaxter";
import { useUiStore } from "../state/uiStore";
import "./BaxterChatPage.css";

type ChatRole = "user" | "assistant";

type ChatTurn = {
  id: string;
  role: ChatRole;
  text: string;
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

export function BaxterChatPage() {
  const navigate = useNavigate();
  const workspace = useUiStore((s) => s.workspace);
  const setInboxOpen = useUiStore((s) => s.setInboxOpen);

  const workspacesQ = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const workspaceSlug =
    workspace && workspace !== "all"
      ? workspace
      : (workspacesQ.data?.[0]?.slug ?? "loregarden");
  const workspaceParam = workspace && workspace !== "all" ? workspace : undefined;

  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [busy, setBusy] = useState(false);
  const threadEndRef = useRef<HTMLDivElement | null>(null);
  const initialPromptRef = useRef(takeHomeBaxterPrompt());
  const turnsRef = useRef<ChatTurn[]>([]);
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
    setDraft("");
    const history = turnsRef.current.map((t) => ({ role: t.role, content: t.text }));
    const userTurn: ChatTurn = { id: nextId("user"), role: "user", text: content };
    setTurns((prev) => [...prev, userTurn]);

    try {
      const { reply } = await api.sendBaxterChatMessage(workspaceSlug, {
        content,
        history,
      });
      setTurns((prev) => [
        ...prev,
        {
          id: nextId("assistant"),
          role: "assistant",
          text: reply.trim() || "Baxter returned an empty reply.",
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
    setDraft("");
    setBusy(false);
  };

  useEffect(() => {
    const handedOff = initialPromptRef.current;
    if (!handedOff) return;
    if (workspacesQ.isLoading) return;
    initialPromptRef.current = "";
    void respond(handedOff);
    // Bootstrap once workspace slug is known.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspacesQ.isLoading, workspaceSlug]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [turns.length, busy]);

  return (
    <div className={`baxter-chat${isEmpty ? " baxter-chat--empty" : ""}`}>
      <header className="baxter-chat-top">
        <div className="baxter-chat-brand">
          <BaxterAvatar state={busy ? "thinking" : "idle"} size={28} />
          <span className="baxter-chat-name">Baxter</span>
          <span className="baxter-chat-context">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path d="M3 10.5 12 3l9 7.5" />
              <path d="M5 9.5V21h14V9.5" />
            </svg>
            On Home
          </span>
        </div>
        <button type="button" className="baxter-chat-new" onClick={startNewChat}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <path d="M12 5v14M5 12h14" />
          </svg>
          New chat
        </button>
      </header>

      {isEmpty ? (
        <div className="baxter-chat-welcome">
          <header className="baxter-chat-intro">
            <p className="baxter-chat-kicker">{formatDateLine(now)}</p>
            <h1 className="baxter-chat-greeting">{greetingFor(now)}</h1>
            <p className="baxter-chat-summary">{summaryLine}</p>
          </header>

          <section className="baxter-chat-hero" aria-label="Ask Baxter">
            <div className="baxter-chat-hero-avatar">
              <BaxterAvatar state="idle" label="Baxter" />
            </div>
            <div className="baxter-chat-hero-body">
              <StudioChatComposer
                value={draft}
                onChange={setDraft}
                onSubmit={() => void respond(draft)}
                placeholder="What should we ship today?"
                sendLabel="Ask Baxter"
              />
              <div className="baxter-chat-chip-row" role="list">
                {EMPTY_CHIPS.map((chip) => (
                  <button
                    key={chip}
                    type="button"
                    className="baxter-chat-chip"
                    role="listitem"
                    onClick={() => setDraft(chip)}
                  >
                    {chip}
                  </button>
                ))}
              </div>
            </div>
          </section>
        </div>
      ) : (
        <>
          <div className="baxter-chat-thread" aria-live="polite">
            {turns.map((turn) => (
              <div
                key={turn.id}
                className={`baxter-chat-turn baxter-chat-turn--${turn.role}`}
              >
                {turn.role === "assistant" ? (
                  <div className="baxter-chat-assistant-col">
                    <div className="baxter-chat-reply">
                      <MarkdownContent content={turn.text} className="baxter-chat-text" />
                    </div>
                    {turn.approvals && turn.approvals.length > 0 ? (
                      <div className="baxter-chat-card">
                        <div className="baxter-chat-card-head">
                          <span className="baxter-chat-card-title">
                            ● {turn.approvals.length} approval
                            {turn.approvals.length === 1 ? "" : "s"} waiting on you
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
                          {turn.approvals.map((a) => (
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
                    ) : null}
                  </div>
                ) : (
                  <div className="baxter-chat-user-bubble">{turn.text}</div>
                )}
              </div>
            ))}

            {busy ? (
              <div className="baxter-chat-turn baxter-chat-turn--assistant">
                <p className="baxter-chat-thinking">Baxter is looking…</p>
              </div>
            ) : null}
            <div ref={threadEndRef} />
          </div>

          <div className="baxter-chat-dock">
            {latestSuggestions && latestSuggestions.length > 0 && !busy ? (
              <div className="baxter-chat-suggestions">
                {latestSuggestions.map((chip) => (
                  <button
                    key={chip}
                    type="button"
                    className="baxter-chat-chip"
                    onClick={() => void respond(chip)}
                  >
                    {chip}
                  </button>
                ))}
              </div>
            ) : null}

            <form
              className="baxter-chat-composer"
              onSubmit={(e) => {
                e.preventDefault();
                void respond(draft);
              }}
            >
              <input
                className="baxter-chat-input"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Reply to Baxter…"
                disabled={busy}
                aria-label="Message Baxter"
              />
              <span className="baxter-chat-shortcut" aria-hidden>
                ⌘J
              </span>
              <button
                type="submit"
                className="baxter-chat-send"
                disabled={busy || !draft.trim()}
                aria-label="Send"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
                  <path d="M22 2 11 13M22 2l-7 20-4-9-9-4z" />
                </svg>
              </button>
            </form>
          </div>
        </>
      )}
    </div>
  );
}
