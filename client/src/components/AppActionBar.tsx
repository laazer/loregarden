import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import { useActiveChatSession } from "../hooks/useActiveChatSession";
import { useTerminalTarget } from "../hooks/useTerminalTarget";
import { useTicketAsides } from "../hooks/useTicketAsides";
import {
  COMPOSER_PLACEHOLDER,
  DOCK_QUICK_PROMPT_LIMIT,
  quickPrompts as promptsFor,
} from "../lib/dockChatPrompts";
import { useUiStore, type UtilityDockEdge } from "../state/uiStore";
import { formatLogExcerpt } from "../utils/logExcerpt";
import { BaxterAvatar } from "./chat/BaxterAvatar";

const NO_SESSION_PLACEHOLDER = "Open a ticket or a branch to chat about it";

/**
 * The global bottom bar: one line that is both the composer for whichever chat
 * the screen owns and the switch for the shell beneath it.
 *
 * It replaces the stacked status strip and collapsed dock bar. The conversation
 * is bound from the route, not handed down, because this sits above `<Routes>`;
 * a message sent here is the same turn the on-screen panel would have sent.
 */
export function AppActionBar() {
  const { session, label, ticketId, pendingApprovals, branch, archive, composedOnScreen } =
    useActiveChatSession();
  const terminal = useTerminalTarget();

  const chatOpen = useUiStore((s) => s.copilotOpen);
  const setChatOpen = useUiStore((s) => s.setCopilotOpen);
  const terminalOpen = useUiStore((s) => s.terminalOpen);
  const setTerminalOpen = useUiStore((s) => s.setTerminalOpen);
  const utilityDockEdge = useUiStore((s) => s.utilityDockEdge);
  const setUtilityDockEdge = useUiStore((s) => s.setUtilityDockEdge);
  const historyOpen = useUiStore((s) => s.copilotHistoryOpen);
  const setHistoryOpen = useUiStore((s) => s.setCopilotHistoryOpen);

  const [draft, setDraft] = useState("");
  const [autoApprove, setAutoApprove] = useState(false);
  const [attachLogs, setAttachLogs] = useState(false);

  // Same key the dashboard and the terminal target use, so the log lines to
  // attach cost no extra request.
  const { data: ticket } = useQuery({
    queryKey: ["ticket", ticketId],
    queryFn: () => api.ticket(ticketId as string),
    enabled: Boolean(ticketId),
  });

  const logLines = ticket?.artifacts?.logs ?? [];
  const liveLog = ticket?.artifacts?.live ?? null;
  const hasLogs = logLines.length > 0 || Boolean(liveLog?.trim());

  // A run's output belongs to the ticket it came from; carrying the choice over
  // would attach one ticket's logs to a question about another.
  useEffect(() => {
    setAttachLogs(false);
  }, [ticketId]);

  const asides = useTicketAsides(ticketId ?? undefined);
  // Asking a busy ticket chat is refused outright by `start_triage_run`, which
  // is the moment an operator most wants to ask something. The composer stays
  // open and the message routes to the aside channel instead: answered by an
  // observer reading the run's log, costing the run nothing.
  //
  // Keyed on the ticket's activity rather than `session.isBusy` alone: the
  // latter is derived from `triage_run_status`, which counts only triage turns,
  // so it is false during exactly the stage run this channel exists for.
  // `queued` is excluded deliberately — `find_active_run` looks at RUNNING and
  // AWAITING_PERMISSION only, so a queued ticket still takes an ordinary
  // message and should get one.
  //
  // `isBusy` is still read, because it turns true the moment a chat turn is
  // POSTed while `activity` waits on the next poll. Without it, a second message
  // sent into that gap goes to the chat and comes back a 409.
  const asideMode =
    Boolean(ticketId) &&
    session?.kind === "ticket-triage" &&
    (ticket?.activity === "running" || ticket?.activity === "awaiting" || session.isBusy);

  const expanded = chatOpen && Boolean(session);
  const sendable = Boolean(session) && !session?.loadError;
  const quickPrompts = session
    ? promptsFor(session.kind, branch).slice(0, DOCK_QUICK_PROMPT_LIMIT)
    : [];

  const submit = (content: string) => {
    if (!session || !content.trim()) return;
    const question = content.trim();
    setDraft("");
    // Open the thread on the way out: a reply arriving behind a collapsed dock
    // is a message the operator never sees.
    setChatOpen(true);
    if (asideMode) {
      // No excerpt is attached here — the observer is handed the run's log tail
      // server-side, so pasting one would send the same lines twice.
      asides.ask.mutate(question);
      return;
    }
    const excerpt = attachLogs && hasLogs ? formatLogExcerpt(logLines, liveLog).trim() : "";
    const message = excerpt
      ? `Question about the run logs below:\n\n\`\`\`\n${excerpt}\n\`\`\`\n\n${question}`
      : question;
    void session.send(message, { autoApprove }).catch(() => {});
  };

  const screenControls = (
    <ActionBarScreenControls
      terminalSlug={terminal.workspaceSlug}
      terminalOpen={terminalOpen}
      setTerminalOpen={setTerminalOpen}
      utilityDockEdge={utilityDockEdge}
      setUtilityDockEdge={setUtilityDockEdge}
    />
  );

  // Home and the chat page compose for this thread themselves, so the bar keeps
  // only its screen-level controls there: a second composer for the
  // conversation already on screen is noise, and a disabled one is worse.
  if (composedOnScreen) {
    return (
      <footer className={`app-action-bar app-action-bar--edge-${utilityDockEdge}`}>
        <span className="app-action-bar-spacer" aria-hidden />
        {screenControls}
      </footer>
    );
  }

  return (
    <footer className={`app-action-bar app-action-bar--edge-${utilityDockEdge}`}>
      <button
        type="button"
        className="app-action-bar-baxter"
        aria-expanded={expanded}
        aria-label={expanded ? "Collapse Baxter" : "Expand Baxter"}
        title="Baxter"
        disabled={!session}
        onClick={() => setChatOpen(!chatOpen)}
      >
        <BaxterAvatar
          variant="head"
          state={session?.isBusy ? "typing" : "idle"}
          size={26}
          label="Baxter"
        />
      </button>

      <input
        className="app-action-bar-input"
        value={draft}
        disabled={!sendable}
        placeholder={
          asideMode
            ? "btw — ask about the run without interrupting it"
            : session
              ? (COMPOSER_PLACEHOLDER[session.kind] ??
                "Ask anything, or tell an agent what to do — without leaving this screen")
              : NO_SESSION_PLACEHOLDER
        }
        aria-label={asideMode ? "Ask an aside about this run" : "Message this conversation"}
        onChange={(e) => setDraft(e.target.value)}
        onFocus={() => session && setChatOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit(draft);
          }
        }}
      />

      {!expanded && quickPrompts.length > 0 && (
        <div className="app-action-bar-quick">
          {quickPrompts.map((prompt) => (
            <button
              key={prompt}
              type="button"
              className="app-action-bar-quick-btn"
              disabled={!sendable || session?.isBusy}
              onClick={() => submit(prompt)}
            >
              {prompt}
            </button>
          ))}
        </div>
      )}

      <ActionBarStatus
        label={label}
        waiting={pendingApprovals.length}
        loadError={Boolean(session?.loadError)}
        sendError={asides.askError ?? session?.error ?? null}
        busy={Boolean(session?.isBusy)}
      />

      {asideMode ? (
        <span
          className="app-action-bar-pill"
          title={
            "This ticket is running, so your message is asked as an aside: answered " +
            "by Baxter reading the run's log, without touching the run itself."
          }
        >
          btw
        </span>
      ) : null}

      {/* Redundant in aside mode: the observer is given the log tail server-side. */}
      {ticketId && !asideMode ? (
        <button
          type="button"
          className={`app-action-bar-logs${attachLogs ? " is-on" : ""}`}
          aria-pressed={attachLogs}
          disabled={!hasLogs}
          title={
            hasLogs
              ? "Send the tail of this ticket's run log with your question"
              : "No run log output on this ticket yet"
          }
          onClick={() => setAttachLogs(!attachLogs)}
        >
          Run logs
        </button>
      ) : null}

      {/* Only the Baxter thread keeps past conversations — a ticket's triage
          chat is the ticket's, and there is no other one to open — so these
          appear with the archive rather than as controls that do nothing. */}
      {archive ? (
        <>
          <button
            type="button"
            className="app-action-bar-chat-new"
            title="Start a new conversation with Baxter"
            onClick={() => {
              archive.startNewChat();
              setHistoryOpen(false);
              // Open the thread on the way out, so the new one is visible
              // rather than started behind a collapsed dock.
              setChatOpen(true);
            }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path d="M12 5v14M5 12h14" />
            </svg>
            New chat
          </button>
          <button
            type="button"
            className={`app-action-bar-chat-history${historyOpen ? " is-on" : ""}`}
            aria-pressed={historyOpen}
            title="Open a past conversation"
            onClick={() => {
              const next = !historyOpen;
              setHistoryOpen(next);
              // The archive lists in the dock's rail, so opening it while the
              // dock is collapsed would show nothing.
              if (next) setChatOpen(true);
            }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" aria-hidden>
              <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
              <path d="M3 3v5h5M12 7v5l3 2" />
            </svg>
            History
          </button>
        </>
      ) : null}

      {/* An aside runs read-only, so there is nothing for this to approve. */}
      {session && !asideMode ? (
        <button
          type="button"
          className={`app-action-bar-auto${autoApprove ? " is-on" : ""}`}
          aria-pressed={autoApprove}
          title="Approve this turn's tool calls without asking"
          onClick={() => setAutoApprove(!autoApprove)}
        >
          Auto-approve
        </button>
      ) : null}

      <button
        type="button"
        className="app-action-bar-send"
        aria-label={asideMode ? "Ask aside" : "Send"}
        disabled={!sendable || !draft.trim() || asides.isAsking}
        onClick={() => submit(draft)}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="M22 2 11 13M22 2l-7 20-4-9-9-4z" />
        </svg>
      </button>
      {screenControls}
    </footer>
  );
}

/**
 * The half of the bar that belongs to the screen rather than to a conversation:
 * the shell switch and where the utility dock sits.
 *
 * Kept apart because it is the only half Home and the chat page draw — those
 * screens compose for their own thread, and the bar must not offer a second
 * composer for it.
 */
function ActionBarScreenControls({
  terminalSlug,
  terminalOpen,
  setTerminalOpen,
  utilityDockEdge,
  setUtilityDockEdge,
}: {
  terminalSlug: string;
  terminalOpen: boolean;
  setTerminalOpen: (open: boolean) => void;
  utilityDockEdge: UtilityDockEdge;
  setUtilityDockEdge: (edge: UtilityDockEdge) => void;
}) {
  const nextEdge: UtilityDockEdge = utilityDockEdge === "bottom" ? "right" : "bottom";

  return (
    <>
      <span className="app-action-bar-divider" aria-hidden />

      <span className="app-action-bar-live" role="img" aria-label="agents online" title="agents online">
        <span className="app-action-bar-live-dot" aria-hidden />
      </span>

      <button
        type="button"
        className={`app-action-bar-terminal${terminalOpen ? " is-on" : ""}`}
        aria-pressed={terminalOpen}
        disabled={!terminalSlug}
        title={terminalSlug ? `Shell in ${terminalSlug}` : "Pick a workspace to open a shell in"}
        onClick={() => setTerminalOpen(!terminalOpen)}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="m4 17 6-6-6-6M12 19h8" />
        </svg>
        Terminal
        <svg
          className={`app-action-bar-chevron${terminalOpen ? " is-open" : ""}`}
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          aria-hidden
        >
          <path d="m18 15-6-6-6 6" />
        </svg>
      </button>

      <button
        type="button"
        className="app-action-bar-dock-edge"
        aria-label={
          utilityDockEdge === "bottom"
            ? "Dock utility panel to the right"
            : "Dock utility panel to the bottom"
        }
        title={utilityDockEdge === "bottom" ? "Dock right" : "Dock bottom"}
        onClick={() => setUtilityDockEdge(nextEdge)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
          <rect x="3" y="3" width="18" height="18" rx="2" />
          {utilityDockEdge === "bottom" ? <path d="M3 15h18" /> : <path d="M15 3v18" />}
        </svg>
      </button>
    </>
  );
}

/**
 * What the bar says about the bound conversation.
 *
 * A decision waiting on the operator outranks everything: an agent question
 * arrives as an approval rather than a message, so a bar showing only "working…"
 * would sit there with something unanswered.
 */
function ActionBarStatus({
  label,
  waiting,
  loadError,
  sendError,
  busy,
}: {
  label: string;
  waiting: number;
  loadError: boolean;
  sendError: string | null;
  busy: boolean;
}) {
  if (waiting > 0) {
    return (
      <span className="app-action-bar-pill app-action-bar-pill--waiting">
        {waiting} waiting on you
      </span>
    );
  }
  if (loadError) {
    return (
      <span className="app-action-bar-pill app-action-bar-pill--error">
        conversation unavailable
      </span>
    );
  }
  if (sendError) {
    return <span className="app-action-bar-pill app-action-bar-pill--error">{sendError}</span>;
  }
  if (busy) {
    return <span className="app-action-bar-pill">working…</span>;
  }
  if (!label) return null;
  return (
    <span className="app-action-bar-pill app-action-bar-pill--context">
      <span className="app-action-bar-pill-dot" aria-hidden />
      On {label}
    </span>
  );
}
