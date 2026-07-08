import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, type RuntimeOptions, type WorkspaceRuntimeSettings } from "../api/client";
import { ticketPath } from "../lib/appNavigation";
import {
  fetchBranchChat,
  sendBranchChatMessage,
  type BranchTriageEntry,
} from "../lib/branchTriageApi";
import { StudioChatComposer, StudioChatMessages } from "./studio/StudioChat";
import { BranchTriageCurrentTag } from "./BranchTriageCurrentTag";
import { TriageModelModal } from "./TriageModelModal";
import { runtimeSummaryLabel } from "./WorkspaceRuntimeFields";

const DEFAULT_RUNTIME: WorkspaceRuntimeSettings = {
  cli_adapter: "default",
  claude_model: "",
  cursor_model: "",
  lmstudio_base_url: "",
  lmstudio_model: "",
};

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
  const [isSending, setIsSending] = useState(false);

  const runtimeOptions = useQuery({
    queryKey: ["runtime-options"],
    queryFn: api.runtimeOptions,
  });

  const chat = useQuery({
    queryKey: ["branch-triage-chat", workspaceSlug, branch],
    queryFn: () => fetchBranchChat(workspaceSlug, branch),
    enabled: Boolean(workspaceSlug && branch),
  });

  const linkedTicketId = chat.data?.linked_ticket_id ?? null;
  const savedRuntime = chat.data?.runtime ?? DEFAULT_RUNTIME;

  const saveTicketRuntime = useMutation({
    mutationFn: (runtime: WorkspaceRuntimeSettings) =>
      api.setTriageRuntime(linkedTicketId!, runtime),
    onSuccess: (saved) => {
      qc.setQueryData(["branch-triage-chat", workspaceSlug, branch], (current: typeof chat.data) =>
        current ? { ...current, runtime: saved } : current,
      );
    },
  });

  const saveWorkspaceRuntime = useMutation({
    mutationFn: (runtime: WorkspaceRuntimeSettings) =>
      api.setWorkspaceRuntime(workspaceSlug, runtime),
    onSuccess: (saved) => {
      qc.setQueryData(["branch-triage-chat", workspaceSlug, branch], (current: typeof chat.data) =>
        current ? { ...current, runtime: saved } : current,
      );
    },
  });

  const sendMessage = useMutation({
    mutationFn: (content: string) => sendBranchChatMessage(workspaceSlug, branch, content),
    onMutate: () => setIsSending(true),
    onSuccess: () => {
      setDraft("");
      qc.invalidateQueries({ queryKey: ["branch-triage-chat", workspaceSlug, branch] });
    },
    onSettled: () => setIsSending(false),
  });

  useEffect(() => {
    setAutoScroll(true);
  }, [branch]);

  const submitChat = () => {
    const text = draft.trim();
    if (!text || sendMessage.isPending) return;
    setDraft("");
    sendMessage.mutate(text);
  };

  const modelLabel = runtimeSummaryLabel(savedRuntime, runtimeOptions.data);
  const composerError =
    (sendMessage.isError ? (sendMessage.error as Error)?.message || "Failed to send message" : null) ??
    (saveTicketRuntime.isError
      ? (saveTicketRuntime.error as Error)?.message || "Failed to save model settings"
      : null) ??
    (saveWorkspaceRuntime.isError
      ? (saveWorkspaceRuntime.error as Error)?.message || "Failed to save model settings"
      : null);

  const messages = chat.data?.messages ?? [];

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
        {chat.isError ? (
          <div className="triage-panel-alert">
            Branch triage chat unavailable. Restart the dev server if this persists.
          </div>
        ) : null}

        <section className="triage-chat-section">
          <div className="ticket-studio-chat-header">
            <div>
              <div className="ticket-studio-chat-title">Branch triage chat</div>
              <div className="ticket-studio-chat-sub">
                Ask about merge/rebase/delete, weird branch state, or cleanup next steps.
              </div>
            </div>
          </div>
          <StudioChatMessages
            messages={messages}
            assistantLabel="Triage assistant"
            thinkingActivity="typing"
            autoScroll={autoScroll}
            emptyMessage="Ask how to clean up this branch. The assistant sees branch health signals, linked work items, and this conversation."
            isThinking={isSending || chat.isLoading}
            thinkingMessage="Triage assistant is thinking…"
          />
        </section>
      </div>

      <StudioChatComposer
        value={draft}
        onChange={setDraft}
        onSubmit={submitChat}
        placeholder="Ask about this branch's state, risks, or cleanup plan…"
        isSending={sendMessage.isPending}
        sendLabel="Ask triage"
        error={composerError}
        optionsRow={
          <label className="chat-composer-option">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(event) => setAutoScroll(event.target.checked)}
            />
            Auto-scroll
          </label>
        }
        toolbar={
          <button
            type="button"
            className="ticket-studio-composer-action"
            disabled={!runtimeOptions.data || chat.isLoading}
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
