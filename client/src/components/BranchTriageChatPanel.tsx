import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, type WorkspaceRuntimeSettings } from "../api/client";
import { ticketPath } from "../lib/appNavigation";
import { TRIAGE_AGENT_NAME } from "../lib/triageAgent";
import {
  type BranchTriageChatSnapshot,
  type BranchTriageEntry,
} from "../lib/branchTriageApi";
import { StudioChatComposer, StudioChatMessages } from "./studio/StudioChat";
import { useBranchChatSession } from "../hooks/useBranchChatSession";
import { BranchTriageCurrentTag } from "./BranchTriageCurrentTag";
import { TriageModelModal } from "./TriageModelModal";
import { runtimeSummaryLabel } from "./WorkspaceRuntimeFields";
import { DEFAULT_RUNTIME } from "../lib/runtimeSettings";

export function BranchTriageChatPanel({
  workspaceSlug,
  branch,
  branchEntry,
}: {
  workspaceSlug: string;
  branch: string;
  branchEntry?: BranchTriageEntry;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [draft, setDraft] = useState("");
  const [modelModalOpen, setModelModalOpen] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);

  const runtimeOptions = useQuery({
    queryKey: ["runtime-options"],
    queryFn: api.runtimeOptions,
  });

  const session = useBranchChatSession(workspaceSlug, branch);
  const chatQueryKey = useMemo(
    () => ["branch-triage-chat", workspaceSlug, branch] as const,
    [workspaceSlug, branch],
  );

  const linkedTicketId = session.snapshot?.linked_ticket_id ?? null;
  const savedRuntime = session.snapshot?.runtime ?? DEFAULT_RUNTIME;

  const saveTicketRuntime = useMutation({
    mutationFn: (runtime: WorkspaceRuntimeSettings) =>
      api.setTriageRuntime(linkedTicketId!, runtime),
    onSuccess: (saved) => {
      qc.setQueryData(chatQueryKey, (current: BranchTriageChatSnapshot | undefined) =>
        current ? { ...current, runtime: saved } : current,
      );
    },
  });

  const saveWorkspaceRuntime = useMutation({
    mutationFn: (runtime: WorkspaceRuntimeSettings) => api.setWorkspaceRuntime(workspaceSlug, runtime),
    onSuccess: (saved) => {
      qc.setQueryData(chatQueryKey, (current: BranchTriageChatSnapshot | undefined) =>
        current ? { ...current, runtime: saved } : current,
      );
    },
  });

  useEffect(() => {
    setAutoScroll(true);
  }, [branch]);

  // Cover the gap between the POST resolving and the next poll reporting "running".
  const busy = session.isBusy;

  const submitChat = () => {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    session.send(text).catch(() => {});
  };

  const sendQuickMessage = (content: string) => {
    if (busy || session.isLoading) return;
    session.send(content).catch(() => {});
  };

  const modelLabel = runtimeSummaryLabel(savedRuntime, runtimeOptions.data);
  const composerError =
    session.error ??
    (saveTicketRuntime.isError
      ? (saveTicketRuntime.error as Error)?.message || "Failed to save model settings"
      : null) ??
    (saveWorkspaceRuntime.isError
      ? (saveWorkspaceRuntime.error as Error)?.message || "Failed to save model settings"
      : null);

  const messages = session.messages;

  return (
    <div className="branch-triage-main branch-triage-chat-panel triage-panel-shell">
      <div className="branch-triage-summary">
        <div className="branch-triage-summary-title">
          <h2>{branch}</h2>
          {branchEntry?.is_current ? <BranchTriageCurrentTag /> : null}
        </div>
        {branchEntry ? (
          <div className="branch-triage-card-meta">
            {branchEntry.ahead > 0 ? <span>{branchEntry.ahead} ahead</span> : null}
            {branchEntry.behind > 0 ? <span>{branchEntry.behind} behind</span> : null}
            {branchEntry.dirty ? <span>dirty</span> : null}
            {branchEntry.linked_tickets[0] ? (
              <button
                type="button"
                className="btn-secondary btn-compact"
                onClick={() =>
                  navigate(ticketPath(branchEntry.linked_tickets[0].id, "triage"))
                }
              >
                Open ticket triage
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="triage-panel-body">
        {session.loadError ? (
          <div className="triage-panel-alert">
            Branch triage chat unavailable. Restart the dev server if this persists.
          </div>
        ) : null}

        <section className="triage-chat-section">
          <div className="ticket-studio-chat-header">
            <div>
              <div className="ticket-studio-chat-title">Branch triage chat</div>
              <div className="ticket-studio-chat-sub">
                Run git cleanup directly — commit, push, checkout, merge, delete, and more.
              </div>
            </div>
          </div>
          <StudioChatMessages
            messages={messages}
            assistantLabel={TRIAGE_AGENT_NAME}
            thinkingActivity="typing"
            autoScroll={autoScroll}
            emptyMessage={`Ask ${TRIAGE_AGENT_NAME} to commit, push, merge, or clean up this branch. ${TRIAGE_AGENT_NAME} runs git in your workspace and reports results.`}
            isThinking={busy || session.isLoading}
            thinkingMessage={`${TRIAGE_AGENT_NAME} is working…`}
          />
        </section>
      </div>

      <StudioChatComposer
        value={draft}
        onChange={setDraft}
        onSubmit={submitChat}
        placeholder="Ask about this branch's state, risks, or cleanup plan…"
        isSending={busy}
        sendLabel={`Ask ${TRIAGE_AGENT_NAME}`}
        error={composerError}
        optionsRow={
          <div className="studio-chat-composer-options-inline">
            <label className="chat-composer-option">
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={(event) => setAutoScroll(event.target.checked)}
              />
              Auto-scroll
            </label>
            <button
              type="button"
              className="btn-secondary btn-compact chat-composer-quick-action"
              disabled={busy || session.isLoading}
              onClick={() => sendQuickMessage("commit and push")}
            >
              {busy ? "Sending…" : "Commit & push"}
            </button>
          </div>
        }
        toolbar={
          <button
            type="button"
            className="ticket-studio-composer-action"
            disabled={!runtimeOptions.data || session.isLoading}
            onClick={() => setModelModalOpen(true)}
            title="Triage model settings"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10" />
              <path d="M12 6v6l4 2" />
            </svg>
            Model · {modelLabel}
          </button>
        }
      />

      <TriageModelModal
        open={modelModalOpen}
        runtime={savedRuntime}
        runtimeOptions={runtimeOptions.data}
        isSaving={saveTicketRuntime.isPending || saveWorkspaceRuntime.isPending}
        onClose={() => setModelModalOpen(false)}
        onSave={async (runtime) => {
          if (linkedTicketId) {
            await saveTicketRuntime.mutateAsync(runtime);
          } else {
            await saveWorkspaceRuntime.mutateAsync(runtime);
          }
        }}
      />
    </div>
  );
}
