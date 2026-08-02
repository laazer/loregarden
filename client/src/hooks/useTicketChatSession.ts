import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { ChatSendOptions, ChatSession } from "../lib/chatSession";
import { useTriageSession } from "./useTriageSession";

/**
 * The ticket triage conversation as a `ChatSession`.
 *
 * Serves both the triage panel and the logs panel — they differ in what they
 * put around the conversation (a log excerpt, a different empty state), not in
 * the conversation itself.
 *
 * Only the transport moves here. Draft text, the auto-approve toggle and the
 * log-excerpt attachment stay in the composer that owns them: they are how one
 * surface composes a message, not properties of the conversation.
 */
export function useTicketChatSession(ticketId: string | undefined): ChatSession {
  const qc = useQueryClient();
  const { triage, isBusy } = useTriageSession(ticketId);

  const sendMessage = useMutation({
    meta: { errorTitle: "Send message" },
    mutationFn: ({ content, options }: { content: string; options?: ChatSendOptions }) =>
      api.sendTriageMessage(ticketId!, content, { auto_approve: options?.autoApprove ?? false }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["triage", ticketId] });
    },
  });

  return {
    kind: "ticket-triage",
    id: ticketId ?? "",
    messages: triage.data?.messages ?? [],
    // Include the in-flight POST so the walker shows before run_status flips.
    isBusy: isBusy || sendMessage.isPending,
    // Ticket triage has no pending message row: the run is the turn.
    activeTurnId: triage.data?.active_run_id ?? null,
    isLoading: triage.isLoading,
    loadError: triage.isError,
    error: sendMessage.isError
      ? (sendMessage.error as Error)?.message || "Failed to send message"
      : null,
    send: (content, options) => sendMessage.mutateAsync({ content, options }),
  };
}
