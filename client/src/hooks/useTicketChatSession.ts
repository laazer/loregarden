import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { ChatMessageView } from "../components/chat/chatUtils";
import type { ChatSendOptions, ChatSession } from "../lib/chatSession";
import { useTicketAsides } from "./useTicketAsides";
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
  const { asideMessages } = useTicketAsides(ticketId);

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
    messages: mergeByCreatedAt(triage.data?.messages ?? [], asideMessages),
    // Include the in-flight POST so the walker shows before run_status flips.
    isBusy: isBusy || sendMessage.isPending,
    // Ticket triage has no pending message row: the run is the turn.
    activeTurnId: triage.data?.active_run_id ?? null,
    // Assume it can until the snapshot says otherwise — a first paint that
    // warned "advisory" and then took it back on load would be worse than
    // silence.
    canAct: (triage.data?.chat_intent ?? "execute") === "execute",
    isLoading: triage.isLoading,
    loadError: triage.isError,
    error: sendMessage.isError
      ? (sendMessage.error as Error)?.message || "Failed to send message"
      : null,
    send: (content, options) => sendMessage.mutateAsync({ content, options }),
  };
}

/**
 * Interleave two already-ordered message lists by timestamp.
 *
 * Unanswered asides are not appended: one raised before the last few chat turns
 * belongs where it was asked, or the thread stops reading as a sequence of
 * events. Entries without a timestamp keep their relative order at the end.
 */
function mergeByCreatedAt(
  messages: ChatMessageView[],
  asides: ChatMessageView[],
): ChatMessageView[] {
  if (!asides.length) return messages;
  const at = (message: ChatMessageView) =>
    message.created_at ? Date.parse(message.created_at) : Number.MAX_SAFE_INTEGER;
  return [...messages, ...asides].sort((a, b) => at(a) - at(b));
}
