import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type RuntimeOptions, type WorkspaceRuntimeSettings } from "../api/client";
import { TRIAGE_AGENT_NAME } from "../lib/triageAgent";
import { StudioChatComposer } from "./studio/StudioChat";
import { TriageModelModal } from "./TriageModelModal";
import { runtimeSummaryLabel } from "./WorkspaceRuntimeFields";

const DEFAULT_RUNTIME: WorkspaceRuntimeSettings = {
  cli_adapter: "default",
  claude_model: "",
  cursor_model: "",
  lmstudio_base_url: "",
  lmstudio_model: "",
};

export function TriageComposer({
  ticketId,
  runtimeOptions,
  placeholder = `Message ${TRIAGE_AGENT_NAME} about this ticket…`,
  attachLogContext,
  showAttachLogsToggle = false,
  attachLogsDefault = true,
  showAutoScrollToggle = false,
  autoScroll = true,
  onAutoScrollChange,
  onSendStart,
  onSendEnd,
  onSent,
}: {
  ticketId: string;
  runtimeOptions: RuntimeOptions | undefined;
  placeholder?: string;
  attachLogContext?: () => string | null;
  showAttachLogsToggle?: boolean;
  attachLogsDefault?: boolean;
  showAutoScrollToggle?: boolean;
  autoScroll?: boolean;
  onAutoScrollChange?: (value: boolean) => void;
  onSendStart?: () => void;
  onSendEnd?: () => void;
  onSent?: () => void;
}) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const [modelModalOpen, setModelModalOpen] = useState(false);
  const [attachLogs, setAttachLogs] = useState(attachLogsDefault);

  const triage = useQuery({
    queryKey: ["triage", ticketId],
    queryFn: () => api.triage(ticketId),
    enabled: !!ticketId,
    retry: 1,
  });

  const savedRuntime = triage.data?.runtime ?? DEFAULT_RUNTIME;

  const saveRuntime = useMutation({
    mutationFn: (runtime: WorkspaceRuntimeSettings) => api.setTriageRuntime(ticketId, runtime),
    onSuccess: (saved) => {
      qc.setQueryData(["triage", ticketId], (current: typeof triage.data) =>
        current ? { ...current, runtime: saved } : current,
      );
    },
  });

  const sendMessage = useMutation({
    mutationFn: (content: string) => api.sendTriageMessage(ticketId, content),
    onMutate: () => {
      onSendStart?.();
    },
    onSuccess: () => {
      setDraft("");
      qc.invalidateQueries({ queryKey: ["triage", ticketId] });
      onSent?.();
    },
    onSettled: () => {
      onSendEnd?.();
    },
  });

  useEffect(() => {
    setAttachLogs(attachLogsDefault);
  }, [ticketId, attachLogsDefault]);

  const submitChat = () => {
    const text = draft.trim();
    if (!text || sendMessage.isPending) return;

    let content = text;
    if (showAttachLogsToggle && attachLogs && attachLogContext) {
      const excerpt = attachLogContext()?.trim();
      if (excerpt) {
        content = `Question about the run logs below:\n\n\`\`\`\n${excerpt}\n\`\`\`\n\n${text}`;
      }
    }

    setDraft("");
    sendMessage.mutate(content);
  };

  const modelLabel = runtimeSummaryLabel(savedRuntime, runtimeOptions);
  const composerError =
    (sendMessage.isError ? (sendMessage.error as Error)?.message || "Failed to send message" : null) ??
    (saveRuntime.isError
      ? (saveRuntime.error as Error)?.message || "Failed to save triage model settings"
      : null);

  const optionsRow =
    showAttachLogsToggle || showAutoScrollToggle ? (
      <>
        {showAttachLogsToggle && (
          <label className="chat-composer-option">
            <input
              type="checkbox"
              checked={attachLogs}
              onChange={(e) => setAttachLogs(e.target.checked)}
            />
            Include recent log output with your question
          </label>
        )}
        {showAutoScrollToggle && onAutoScrollChange && (
          <label className="chat-composer-option">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => onAutoScrollChange(e.target.checked)}
            />
            Auto-scroll
          </label>
        )}
      </>
    ) : undefined;

  return (
    <>
      <StudioChatComposer
        value={draft}
        onChange={setDraft}
        onSubmit={submitChat}
        placeholder={placeholder}
        isSending={sendMessage.isPending}
        sendLabel={`Ask ${TRIAGE_AGENT_NAME}`}
        optionsRow={optionsRow}
        error={composerError}
        toolbar={
          <button
            type="button"
            className="ticket-studio-composer-action"
            disabled={!runtimeOptions || triage.isLoading}
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
        runtimeOptions={runtimeOptions}
        isSaving={saveRuntime.isPending}
        onClose={() => setModelModalOpen(false)}
        onSave={async (runtime) => {
          await saveRuntime.mutateAsync(runtime);
        }}
      />
    </>
  );
}
