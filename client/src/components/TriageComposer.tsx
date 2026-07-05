import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type RuntimeOptions, type WorkspaceRuntimeSettings } from "../api/client";
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
  placeholder = "Message the triage assistant about this ticket…",
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

    sendMessage.mutate(content);
  };

  const modelLabel = runtimeSummaryLabel(savedRuntime, runtimeOptions);

  return (
    <>
      <div
        style={{
          borderTop: "1px solid var(--bd)",
          padding: 12,
          background: "var(--bg1)",
        }}
      >
        {(showAttachLogsToggle || showAutoScrollToggle) && (
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 12,
              marginBottom: 8,
            }}
          >
            {showAttachLogsToggle && (
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  fontSize: 11.5,
                  color: "var(--txm)",
                  cursor: "pointer",
                }}
              >
                <input
                  type="checkbox"
                  checked={attachLogs}
                  onChange={(e) => setAttachLogs(e.target.checked)}
                />
                Include recent log output with your question
              </label>
            )}
            {showAutoScrollToggle && onAutoScrollChange && (
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  fontSize: 11.5,
                  color: "var(--txm)",
                  cursor: "pointer",
                }}
              >
                <input
                  type="checkbox"
                  checked={autoScroll}
                  onChange={(e) => onAutoScrollChange(e.target.checked)}
                />
                Auto-scroll
              </label>
            )}
          </div>
        )}
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={3}
          placeholder={placeholder}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submitChat();
            }
          }}
          style={{
            width: "100%",
            padding: "10px 12px",
            borderRadius: 10,
            border: "1px solid var(--bd)",
            background: "var(--bg2)",
            color: "var(--tx)",
            fontSize: 12.5,
            resize: "vertical",
            marginBottom: 8,
            boxSizing: "border-box",
          }}
        />
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", alignItems: "center" }}>
          <button
            type="button"
            className="btn-secondary"
            disabled={!runtimeOptions || triage.isLoading}
            onClick={() => setModelModalOpen(true)}
            title="Triage model settings"
          >
            Model · {modelLabel}
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!draft.trim() || sendMessage.isPending}
            onClick={submitChat}
          >
            {sendMessage.isPending ? "Sending…" : "Ask triage"}
          </button>
        </div>
        {sendMessage.isError && (
          <div style={{ fontSize: 11.5, color: "var(--rdl)", marginTop: 8, textAlign: "right" }}>
            {(sendMessage.error as Error)?.message || "Failed to send message"}
          </div>
        )}
        {saveRuntime.isError && (
          <div style={{ fontSize: 11.5, color: "var(--rdl)", marginTop: 8, textAlign: "right" }}>
            {(saveRuntime.error as Error)?.message || "Failed to save triage model settings"}
          </div>
        )}
      </div>

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
