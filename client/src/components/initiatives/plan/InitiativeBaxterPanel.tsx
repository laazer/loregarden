import { useState } from "react";

import { useInitiativeBaxterSession } from "../../../hooks/useInitiativeBaxterSession";
import { Button } from "../../ui/Button";
import { ChatApprovalsAsk } from "../../chat/ChatApprovalsAsk";
import { StudioChatComposer, StudioChatMessages } from "../../studio/StudioChat";

/**
 * Baxter, the triage agent, focused on this initiative.
 *
 * Answers "what is stuck under this initiative, and what should happen to
 * it?" — the ticket-level questions the planner does not take. The thread is
 * global, so it reads every workspace the milestones live in, and the server
 * gives it the plan. Its questions and permission asks end the thread as
 * approvals to answer in place.
 */
export function InitiativeBaxterPanel({ initiativeId }: { initiativeId: string }) {
  const chat = useInitiativeBaxterSession(initiativeId);
  const [draft, setDraft] = useState("");

  const submit = () => {
    const content = draft.trim();
    if (!content || chat.isBusy) return;
    setDraft("");
    // The mutation's errorTitle toasts the failure and chat.error renders it;
    // the operator gets their words back to retry.
    chat.send(content).catch(() => setDraft(content));
  };

  return (
    <>
      <header className="plan-planner-head">
        <p className="plan-muted">
          Triages the work under this initiative across its workspaces: what is blocked, what to file, what to change.
        </p>
        <Button
          variant="secondary"
          compact
          disabled={!chat.sessionId || chat.isBusy}
          title={chat.sessionId ? undefined : "This is already a new conversation"}
          onClick={chat.startNewChat}
        >
          New chat
        </Button>
      </header>

      <div className="plan-planner-thread">
        {chat.loadError ? (
          <div className="plan-empty" role="alert">
            <p>This conversation could not be loaded. Start a new one, or come back when the server answers.</p>
            <Button variant="secondary" compact onClick={chat.startNewChat}>
              New chat
            </Button>
          </div>
        ) : chat.isLoading ? (
          <div className="plan-skeleton plan-skeleton-thread" aria-busy="true" aria-label="Loading conversation" />
        ) : (
          <StudioChatMessages
            messages={chat.messages}
            isThinking={chat.isBusy && chat.pendingApprovals.length === 0}
            activeTurnId={chat.activeTurnId}
            thinkingMessage="Baxter is looking…"
            thinkingSub="Reading the initiative and its tickets…"
            assistantLabel="Baxter"
            showAssistantAvatar={false}
            onPrimitiveSubmit={(content) => {
              // silent-ok: same mutation as submit — its errorTitle toasts and chat.error renders it.
              chat.send(content).catch(() => undefined);
            }}
            emptyMessage="Ask Baxter something like “which tickets under this initiative are blocked, and why?”"
            // Null, not an empty ask: the thread treats any element as content and hides its empty state.
            trailingAsk={chat.pendingApprovals.length ? <ChatApprovalsAsk approvals={chat.pendingApprovals} /> : null}
          />
        )}
      </div>

      <StudioChatComposer
        value={draft}
        onChange={setDraft}
        onSubmit={submit}
        onStop={() => {
          // silent-ok: stop carries errorTitle, so the global MutationCache toast reports a failure.
          chat.stop().catch(() => undefined);
        }}
        isSending={chat.isBusy}
        isStopping={chat.isStopping}
        disabled={chat.loadError}
        placeholder="Ask Baxter…"
        error={chat.error}
        dense
      />
    </>
  );
}
