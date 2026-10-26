import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useId, useState } from "react";

import { api } from "../api/client";
import { useActiveChatSession } from "../hooks/useActiveChatSession";
import { useComposerAttachments } from "../hooks/useComposerAttachments";
import { composerQueueKey, useComposerCommands } from "../hooks/useComposerCommands";
import { useComposerHostActions } from "../hooks/useComposerHostActions";
import { useTerminalTarget } from "../hooks/useTerminalTarget";
import { useTicketAsides } from "../hooks/useTicketAsides";
import { COMPOSER_PLACEHOLDER, followUpPrompts } from "../lib/dockChatPrompts";
import { useUiStore, useUtilityDockEdge } from "../state/uiStore";
import { checkoutBranchTriage } from "../lib/branchTriageApi";
import { describeError } from "../state/toastStore";
import { formatLogExcerpt } from "../utils/logExcerpt";
import { BaxterAvatar } from "./chat/BaxterAvatar";
import { ChatModePill, type ChatModeFix } from "./ChatModePill";
import { ComposerAttachButton, ComposerAttachmentTray } from "./chat/ComposerAttachments";
import { Button } from "./ui/Button";
import { Input } from "./ui/Input";
import { ComposerCommandMenu } from "./chat/ComposerCommandMenu";
import { ComposerNotes } from "./chat/ComposerNotes";
import { TriageModelModal } from "./TriageModelModal";
import { runtimeSummaryLabel } from "./WorkspaceRuntimeFields";
import { useRuntimeOptionsQuery } from "../hooks/useRuntimeOptionsQuery";
import {
  ActionBarAgentSwitch,
  ActionBarQuickPrompts,
  ActionBarStatus,
  ScreenQuickPrompts,
} from "./actionBar/ActionBarParts";
import { ActionBarScreenControls } from "./actionBar/ActionBarScreenControls";
import { ActionBarSheet } from "./actionBar/ActionBarSheet";

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
  const {
    session,
    label,
    ticketId,
    pendingApprovals,
    branch,
    archive,
    model,
    composedOnScreen,
    screenPrompts,
    agents,
    assistantLabel,
    promptKind,
    composerBlocked,
  } = useActiveChatSession();
  const terminal = useTerminalTarget();

  const chatOpen = useUiStore((s) => s.copilotOpen);
  const setChatOpen = useUiStore((s) => s.setCopilotOpen);
  const terminalOpen = useUiStore((s) => s.terminalOpen);
  const setTerminalOpen = useUiStore((s) => s.setTerminalOpen);
  const utilityDockEdge = useUtilityDockEdge();
  const setUtilityDockEdge = useUiStore((s) => s.setUtilityDockEdge);
  const historyOpen = useUiStore((s) => s.copilotHistoryOpen);
  const setHistoryOpen = useUiStore((s) => s.setCopilotHistoryOpen);

  const [draft, setDraft] = useState("");
  const [autoApprove, setAutoApprove] = useState(false);
  const [attachLogs, setAttachLogs] = useState(false);
  const attachments = useComposerAttachments();
  const qc = useQueryClient();
  const [modelModalOpen, setModelModalOpen] = useState(false);
  const [checkoutError, setCheckoutError] = useState<string | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const sheetId = useId();

  // The badge only offers a fix for causes the server marked remediable, so
  // each branch here has something real to do. Anything else is reported, not
  // offered — a fix button that cannot fix is worse than no button.
  const applyChatModeFix = useCallback(
    (fix: ChatModeFix) => {
      if (fix === "runtime") {
        setModelModalOpen(true);
        return;
      }
      if (fix === "checkout" && terminal.workspaceSlug && branch) {
        setCheckoutError(null);
        checkoutBranchTriage(terminal.workspaceSlug, branch)
          .then(() => {
            // The mode is server-resolved, so re-reading the snapshot is what
            // flips the badge — nothing here decides it locally.
            qc.invalidateQueries({ queryKey: ["branch-triage-chat"] });
            qc.invalidateQueries({ queryKey: ["branch-triage"] });
          })
          .catch((err) => setCheckoutError(describeError(err, "Could not check out the branch")));
      }
    },
    [branch, qc, terminal.workspaceSlug],
  );


  // Same key the dashboard and the terminal target use, so the log lines to
  // attach cost no extra request.
  const { data: ticket } = useQuery({
    queryKey: ["ticket", ticketId],
    queryFn: () => api.ticket(ticketId as string),
    enabled: Boolean(ticketId),
  });
  const modelWorkspace = model?.workspaceSlug ?? "";
  const runtimeOptions = useRuntimeOptionsQuery({
    queryKey: ["runtime-options", modelWorkspace],
    params: modelWorkspace ? { workspace: modelWorkspace } : undefined,
    enabled: Boolean(model),
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
  // With no conversation, no agent to pick and no model to set, the sheet would
  // open empty; the composer then just says what to do instead.
  const hasSheet = Boolean(session || agents || model);
  const sendable = Boolean(session) && !session?.loadError && !composerBlocked;
  // Every surface exposes stop now, so the only question left is whether this
  // one is busy — and aside mode, which routes a busy composer to a read-only
  // question instead of a turn to stop.
  const canStop = Boolean(session?.isBusy && !asideMode);
  // Only the Baxter thread has an attachments endpoint, and an aside is a
  // read-only question to an observer, which takes no files either.
  const canAttach = session?.kind === "baxter-home" && !asideMode;
  const files = canAttach ? attachments.items.map((item) => item.file) : [];

  // A conversation's files belong to it; carried over, they would go out with
  // a message to whichever chat the next screen binds.
  const clearAttachments = attachments.clear;
  useEffect(() => {
    clearAttachments();
  }, [session?.kind, session?.id, clearAttachments]);

  const send = (content: string, skill = "") => {
    if (!session) return;
    const question = content.trim();
    if (!question && !files.length) return;
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
    attachments.clear();
    void session.send(message, { autoApprove, skill, ...(files.length ? { files } : {}) }).catch(() => {
      // silent-ok: send is a mutation carrying meta.errorTitle, so the global
      // MutationCache toast fires and session.error renders in the bar's pill.
    });
  };

  const onAfterNewChat = useCallback(() => {
    setHistoryOpen(false);
    setChatOpen(true);
  }, [setHistoryOpen, setChatOpen]);

  const onBtw = useCallback(
    (message: string) => {
      if (!ticketId) return;
      setChatOpen(true);
      asides.ask.mutate(message);
    },
    [ticketId, asides.ask, setChatOpen],
  );

  const commandActions = useComposerHostActions({
    workspaceSlug: terminal.workspaceSlug,
    ticketId,
    pendingApprovals,
    archive,
    session: asideMode ? null : session,
    onBtw: ticketId ? onBtw : undefined,
    onAfterNewChat,
  });

  const commands = useComposerCommands({
    value: draft,
    onChange: setDraft,
    workspaceSlug: terminal.workspaceSlug,
    // An aside is answered by an observer reading the log, not by the thread —
    // there is no turn to wait on, so there is nothing to queue behind.
    queueKey:
      session && !asideMode
        ? composerQueueKey(session.kind, session.id, terminal.workspaceSlug)
        : null,
    isBusy: Boolean(session?.isBusy),
    onSend: send,
    onSendInNewChat: archive
      ? (content) => {
          setChatOpen(true);
          void archive.sendInNewChat(content).catch(() => {
            // silent-ok: sendInNewChat is a mutation with meta.errorTitle, so
            // the global MutationCache toast already reported the failure.
          });
        }
      : undefined,
    skillsEnabled: session?.kind === "baxter-home" && !asideMode,
    actions: commandActions,
  });

  /** Enter and the send button: a `/command` acts, anything else is a message. */
  const submit = (content: string) => {
    if (commands.submit()) return;
    send(content);
  };

  // ⌘J (Ctrl+J) reaches the composer from anywhere, the way ⌘K reaches search.
  // Left to the code editor and the terminal while they have focus, which bind
  // their own chords.
  const composerRef = commands.inputRef;
  useEffect(() => {
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key.toLowerCase() !== "j" || !(event.metaKey || event.ctrlKey)) return;
      if (event.altKey || event.shiftKey) return;
      if (event.target instanceof Element && event.target.closest(".monaco-editor, .xterm")) return;
      const input = composerRef.current;
      if (!input) return;
      event.preventDefault();
      input.focus();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [composerRef]);

  const screenControls = (
    <ActionBarScreenControls
      terminalSlug={terminal.workspaceSlug}
      terminalOpen={terminalOpen}
      setTerminalOpen={setTerminalOpen}
      utilityDockEdge={utilityDockEdge}
      setUtilityDockEdge={setUtilityDockEdge}
    />
  );

  // The picker is the bar's on every screen, chat pages included — the model a
  // question runs on is a property of the conversation, not of the composer
  // drawing it, and the page's composer would otherwise carry a second copy.
  const modelControl = model ? (
    <>
      <Button
        variant="plain"
        className="app-action-bar-chat-model"
        title={`Choose ${model.assistant}'s provider and model for this conversation`}
        disabled={!runtimeOptions.data || model.isSavingRuntime || model.isBusy}
        onClick={() => setModelModalOpen(true)}
      >
        Model · {runtimeSummaryLabel(model.runtime, runtimeOptions.data)}
      </Button>
      <TriageModelModal
        open={modelModalOpen}
        runtime={model.runtime}
        runtimeOptions={runtimeOptions.data}
        isSaving={model.isSavingRuntime}
        scopeLabel={model.scopeLabel}
        subtitle="Choose a provider, then pick a model for this conversation"
        onClose={() => setModelModalOpen(false)}
        onSave={model.setRuntime}
      />
    </>
  ) : null;

  // Home and the chat page compose for this thread themselves, so the bar keeps
  // only its screen-level controls there: a second composer for the
  // conversation already on screen is noise, and a disabled one is worse. The
  // model picker stays, because the page no longer draws one.
  if (composedOnScreen) {
    return (
      <footer className={`app-action-bar app-action-bar--edge-${utilityDockEdge}`}>
        <span className="app-action-bar-spacer" aria-hidden />
        {screenPrompts ? <ScreenQuickPrompts target={screenPrompts} /> : null}
        {modelControl}
        {screenControls}
      </footer>
    );
  }

  return (
    <footer className={`app-action-bar app-action-bar--edge-${utilityDockEdge}`}>
      <Button
        variant="plain"
        className="app-action-bar-baxter"
        aria-expanded={expanded}
        aria-label={expanded ? `Collapse ${assistantLabel}` : `Expand ${assistantLabel}`}
        title={assistantLabel}
        disabled={!session}
        onClick={() => setChatOpen(!chatOpen)}
      >
        {/* Busy rocks (the wag) rather than bobs: this is the only Baxter in
            the triage chat now that the thread drops its per-reply avatar. */}
        <BaxterAvatar
          variant="head"
          state={session?.isBusy ? "responding" : "idle"}
          size={26}
          label={assistantLabel}
        />
      </Button>

      <div className="app-action-bar-notes">
        <ComposerNotes commands={commands} />
      </div>

      {/* In the row, not the sheet: it decides which conversation the composer
          talks to, and when that agent cannot take a message the composer is
          disabled, cannot take focus, and so could never open a sheet holding
          the switch to another agent. */}
      {agents ? <ActionBarAgentSwitch agents={agents} disabled={Boolean(session?.isBusy)} /> : null}

      {/* The composer and its sheet are one focus group: focus anywhere in it
          keeps the sheet open, and leaving it (or Escape) closes it. */}
      <div
        className="app-action-bar-compose"
        onFocus={() => setSheetOpen(true)}
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) setSheetOpen(false);
        }}
        onKeyDown={(event) => {
          // The `/` menu takes Escape first, and marks it handled.
          if (event.key === "Escape" && sheetOpen && !event.defaultPrevented) {
            event.preventDefault();
            setSheetOpen(false);
          }
        }}
      >
        <div className="lg-composer-commands lg-composer-commands--bar">
          <ComposerCommandMenu
            items={commands.items}
            activeIndex={commands.activeIndex}
            triggerKind={commands.triggerKind}
            anchorRef={commands.inputRef}
            onHover={commands.setActiveIndex}
            onPick={commands.accept}
          />
          <Input
            ref={commands.inputRef as React.Ref<HTMLInputElement>}
            className="app-action-bar-input"
            value={draft}
            disabled={!sendable}
            placeholder={
              asideMode
                ? "btw — ask about the run without interrupting it"
                : composerBlocked
                  ? composerBlocked
                : session
                  ? (COMPOSER_PLACEHOLDER[promptKind] ??
                    "Ask anything, or tell an agent what to do — without leaving this screen")
                  : NO_SESSION_PLACEHOLDER
            }
            aria-label={asideMode ? "Ask an aside about this run" : "Message this conversation"}
            onChange={(e) => commands.handleChange(e.target.value, e.target)}
            onFocus={() => session && setChatOpen(true)}
            onBlur={() => commands.close()}
            onPaste={(e) => {
              if (!canAttach) return;
              const pasted = Array.from(e.clipboardData.files);
              if (!pasted.length) return;
              e.preventDefault();
              attachments.add(pasted);
            }}
            onKeyDown={(e) => {
              // While the menu is open it owns Enter, Tab and the arrows.
              if (commands.handleKeyDown(e)) return;
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit(draft);
              }
            }}
          />
        </div>
        <kbd className="app-action-bar-kbd" aria-hidden="true">
          ⌘J
        </kbd>
        <Button
          variant="plain"
          className={`app-action-bar-send${canStop ? " app-action-bar-send--stop" : ""}`}
          aria-label={
            canStop
              ? session?.isStopping
                ? "Stopping…"
                : "Stop"
              : asideMode
                ? "Ask aside"
                : "Send"
          }
          disabled={
            canStop
              ? Boolean(session?.isStopping)
              : !sendable || (!draft.trim() && !files.length) || asides.isAsking
          }
          onClick={() => {
            // `stop` is required on ChatSession now, so the only question left
            // is whether there is a session at all.
            if (canStop && session) {
              void session.stop().catch(() => {
                // silent-ok: stop is a mutation with meta.errorTitle, so the
                // global MutationCache toast reports a stop that did not take.
              });
              return;
            }
            submit(draft);
          }}
        >
          {canStop ? (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
              <rect x="6" y="6" width="12" height="12" rx="1.5" />
            </svg>
          ) : (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path d="M22 2 11 13M22 2l-7 20-4-9-9-4z" />
            </svg>
          )}
        </Button>
        <ActionBarSheet
          id={sheetId}
          open={sheetOpen && hasSheet}
          controls={
            <>
              {canAttach ? (
                <ComposerAttachButton onFiles={attachments.add} disabled={!sendable} />
              ) : null}
              {modelControl}
              {/* An aside runs read-only, so there is nothing for this to approve. */}
              {session && !asideMode ? (
                <Button
                  variant="plain"
                  className={`app-action-bar-auto${autoApprove ? " is-on" : ""}`}
                  aria-pressed={autoApprove}
                  title="Approve this turn's tool calls without asking"
                  onClick={() => setAutoApprove(!autoApprove)}
                >
                  Auto-approve
                </Button>
              ) : null}
              {/* Redundant in aside mode: the observer is given the log tail server-side. */}
              {ticketId && !asideMode ? (
                <Button
                  variant="plain"
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
                </Button>
              ) : null}
              <span className="app-action-bar-spacer" aria-hidden />
              {/* Only the Baxter thread keeps past conversations — a ticket's triage
                  chat is the ticket's, and there is no other one to open — so these
                  appear with the archive rather than as controls that do nothing. */}
              {archive ? (
                <>
                  <Button
                    variant="plain"
                    className="app-action-bar-chat-new"
                    title={`Start a new conversation with ${assistantLabel}`}
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
                  </Button>
                  <Button
                    variant="plain"
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
                  </Button>
                </>
              ) : null}
            </>
          }
          prompts={
            // The quickest reply to the turn on screen, so they follow the
            // conversation whether its thread is open or not.
            session ? (
              <ActionBarQuickPrompts
                prompts={followUpPrompts(promptKind, branch, session.messages)}
                disabled={!sendable || session.isBusy}
                onPick={(prompt) => submit(prompt)}
              />
            ) : null
          }
          tray={
            canAttach ? (
              <ComposerAttachmentTray items={attachments.items} onRemove={attachments.remove} />
            ) : null
          }
        />
      </div>

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

      {/* Always on: "can act" is as worth knowing as "cannot", and a badge that
          only appears on failure reads as "still loading" the rest of the time.
          Suppressed for an aside, which carries its own label above. */}
      <ChatModePill
        mode={session?.chatMode}
        canAct={session?.canAct}
        asideMode={asideMode}
        onFix={applyChatModeFix}
      />

      {/* A fix that failed must say so here: the badge would otherwise stay
          advisory with no indication the button did anything. */}
      {checkoutError ? (
        <span className="app-action-bar-pill app-action-bar-pill--error" title={checkoutError}>
          checkout failed
        </span>
      ) : null}
      {screenControls}
    </footer>
  );
}
